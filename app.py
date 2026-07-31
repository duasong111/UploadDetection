"""
FastAPI 主入口 - 纯路由层
业务逻辑全部在 functions/ 模块中
"""
import asyncio
import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from functions.ai.ai_chat import consume_sid_stream
from functions.ai.ai_chat_result import get_result

import socketio
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from config import REDIS_URL, CODE_ERROR
from Common.Response import create_response, create_ai_response, create_error_response
from functions.ai.book_rag import book_rag_service
from functions.firmware import FirmwareManager
from functions.device.device_api import list_devices, query_device_history, query_device
from functions.device.device_report import save_runtime
from functions.frp.frp_api import query_frp_uptime, update_frp_config, update_n2n_config, add_frp
from functions.ssh.ssh_api import add_license, batch_deploy, add_duration, control_duration, quick_configuration
import redis

# ==================== 应用初始化 ====================
app = FastAPI(title="Upload Detection API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*', ping_interval=25, ping_timeout=60)
socket_app = socketio.ASGIApp(sio, app)

# 全局实例
firmware_mgr = FirmwareManager()
book_redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
executor = ThreadPoolExecutor(max_workers=20)

# ==================== Socket.IO ====================
@sio.event
async def connect(sid, _environ):
    await sio.emit('connected', {'sid': sid}, room=sid)

@sio.event
async def disconnect(_sid):
    pass

@sio.event
async def ping_server(sid):
    await sio.emit('pong', {'time': None}, room=sid)

@sio.event
async def ai_chat(sid, data):
    """AI 聊天（Socket.IO）：请求入队，消费者流式转发，打字机效果推送"""
    from functions.ai.ai_chat_producer import publish_ai_chat

    message, history, username = data.get('message'), data.get('history', []), data.get('username')
    if not message:
        await sio.emit('ai_response', {'success': False, 'message': '消息内容不能为空'}, room=sid)
        return

    # 入队：立即返回，不再阻塞线程池
    task_id = publish_ai_chat(message, history, username, channel="socketio", sid=sid)
    if not task_id:
        await sio.emit('ai_response', {'success': False, 'message': '消息队列不可用，请稍后重试'}, room=sid)
        return

    await sio.emit('ai_stream_start', {'status': 'started'}, room=sid)


    async def _emit_stream_events(events: list):
        """把一批转发事件推给客户端"""
        for evt in events:
            if evt.get("event") == "ai_stream_token":
                token = evt.get("data", {}).get("token", "")
                if token:
                    await sio.emit('ai_stream_token', {'token': token}, room=sid)
            # ai_stream_end 只是结束标记，最终结果以 ai_chat_result 为准

    # 循环：边等结果边把流式事件推给客户端
    while True:
        result = get_result(task_id)
        if result is None:
            # 结果尚未写入（消费者还在处理），继续转发流式 chunk
            events = consume_sid_stream(sid, timeout=2)
            if events:
                await _emit_stream_events(events)
            await asyncio.sleep(0.05)
            continue

        status = result.get("status")
        if status in ("done", "error"):
            # 结果已出：先把剩余流式事件推完，再推送最终结果
            events = consume_sid_stream(sid, timeout=1)
            if events:
                await _emit_stream_events(events)
            await sio.emit('ai_stream_end', {
                'answer': result.get("answer"),
                'tool_calls': result.get("tool_calls"),
                'success': status == "done",
                'daily_usage': result.get("daily_usage"),
                'daily_limit': result.get("daily_limit", 20),
            }, room=sid)
            return
        else:
            # 还在 pending：转发流式 chunk 后继续等
            events = consume_sid_stream(sid, timeout=2)
            if events:
                await _emit_stream_events(events)
            await asyncio.sleep(0.05)


# ==================== 辅助 ====================
async def run_sync(func, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, lambda: func(*args))


# ==================== 认证 ====================
@app.post("/api/register/")
async def register(request: Request):
    from functions.user import RegisterFunction
    data = await request.json()
    return RegisterFunction().register(data.get('username'), data.get('password'))

@app.post("/api/login/")
async def login(request: Request):
    from functions.user import LoginFunction
    data = await request.json()
    return LoginFunction().checklogin(data.get('username'), data.get('password'))

@app.post("/api/change_password/")
async def change_password(request: Request):
    from functions.user import ChangePasswordView
    return ChangePasswordView().dispatch_request()

@app.post("/api/user_contributions/")
async def user_contributions(request: Request):
    from functions.user import UserContributionView
    data = await request.json()
    return UserContributionView().get_contributions(data.get('username'), data.get('month'))


# ==================== 设备 ====================
@app.get("/api/list_devices/")
async def api_list_devices():
    return await run_sync(list_devices)

@app.post("/api/query_device_online_history/")
async def api_query_device_history(request: Request):
    data = await request.json()
    sn, n = data.get("device_sn"), data.get("number")
    if not sn: return create_response(400, "缺少设备序列号 sn", False)
    if not n: return create_response(400, "缺少返回条数 number", False)
    try: n = int(n)
    except: return create_response(400, "number 必须为正整数", False)
    return await run_sync(query_device_history, sn, n)

@app.post("/api/static_time/")
async def api_static_time(request: Request):
    data = await request.json()
    sn, uuid_val, runtime = data.get("sn"), data.get("uuid"), data.get("runtime")
    if not sn or not uuid_val or runtime is None: return create_response(400, "缺少必要参数", False)
    try: runtime = int(runtime)
    except: return create_response(400, "runtime 必须为整数", False)
    return await run_sync(save_runtime, sn, uuid_val, runtime)

@app.post("/api/query_device/")
async def api_query_device(request: Request):
    data = await request.json()
    keyword = data.get('keyword')
    if not keyword: return create_response(400, "缺少必要参数：keyword", False)
    return await run_sync(query_device, keyword)


# ==================== FRP ====================
@app.post("/api/device_uptime/")
async def api_device_uptime(request: Request):
    data = await request.json()
    n = None
    if data.get("number"):
        try: n = int(data["number"])
        except: return create_response(400, "number 必须为正整数", False)
    return await run_sync(query_frp_uptime, n)

@app.post("/api/frp_config_update/")
async def api_frp_config_update(request: Request):
    data = await request.json()
    devices = data.get("devices")
    if not devices or not isinstance(devices, list): return create_response(400, "devices 参数错误", False)
    return await run_sync(update_frp_config, devices)

@app.post("/api/n2n_config_update/")
async def api_n2n_config_update(request: Request):
    data = await request.json()
    devices = data.get("devices")
    if not devices or not isinstance(devices, list): return create_response(400, "devices 参数错误", False)
    return await run_sync(update_n2n_config, devices)

@app.post("/api/add_frp/")
async def api_add_frp(request: Request):
    data = await request.json()
    ip, password, device_name = data.get('ip'), data.get('password'), data.get('device_name')
    if not all([ip, password, device_name]): return create_response(400, "缺少必要参数", False)
    return await run_sync(add_frp, ip, password, device_name)


# ==================== 设备配置 ====================
@app.post("/api/quick_configuration/")
async def api_quick_configuration(request: Request):
    data = await request.json()
    params = {
        "frpc_value": data.get('frpc_value'), "device_sn_value": data.get('device_sn'),
        "duration_sn": data.get('duration_sn'), "device_ip": data.get('device_ip'),
        "username": data.get('username', 'root'), "password": data.get('password', 'gsm200818534'),
        "n2n_command": data.get('n2n_command'), "ping_ip": data.get('ping_ip', '10.10.10.11'),
        "operator": data.get('operator', 'system_admin')
    }
    return await run_sync(quick_configuration, params)

@app.post("/api/add_license/")
async def api_add_license(request: Request):
    data = await request.json()
    device_ip, password = data.get('device_ip'), data.get('password')
    if not all([device_ip, password]): return create_response(400, "缺少必要参数", False)
    return await run_sync(add_license, device_ip, password)

@app.post("/api/batch_deploy/")
async def api_batch_deploy(request: Request):
    data = await request.json()
    ip_list = data.get('ip_list')
    if not ip_list or not isinstance(ip_list, list) or len(ip_list) == 0: return create_response(400, "ip_list 参数错误", False)
    return await run_sync(batch_deploy, ip_list)


# ==================== 远程持久测试 ====================
@app.post("/api/add_duration/")
async def api_add_duration(request: Request):
    data = await request.json()
    ip, password, device_sn = data.get('ip'), data.get('password'), data.get('device_sn')
    if not all([ip, password, device_sn]): return create_response(400, "缺少必要参数", False)
    return await run_sync(add_duration, ip, password, device_sn)

@app.post("/api/duration_status/")
async def api_duration_status(request: Request):
    data = await request.json()
    ip, password, enable = data.get('ip'), data.get('password'), data.get('enable')
    if not all([ip, password]) or not isinstance(enable, bool): return create_response(400, "参数错误", False)
    return await run_sync(control_duration, ip, password, enable)


# ==================== 头像 ====================
@app.post("/api/upload_avatar/")
async def api_upload_avatar(request: Request):
    from functions.avatar import AvatarManager
    from werkzeug.datastructures import FileStorage

    content_type = request.headers.get('content-type', '')
    if 'multipart/form-data' in content_type:
        form = await request.form()
        username, file = form.get('username'), form.get('file')
    else:
        data = await request.json()
        username, file = data.get('username'), None

    if not username: return create_response(400, "用户名为必填项", False)
    if not file: return create_response(400, "文件为必填项", False)

    contents = await file.read()
    flask_file = FileStorage(stream=io.BytesIO(contents), filename=file.filename, content_type=file.content_type)
    return AvatarManager().upload_avatar(username, flask_file)

@app.get("/api/avatar/{filename:path}")
async def api_get_avatar(filename: str):
    from functions.avatar import AvatarManager
    filename = filename.rstrip('/')
    ext = filename.rsplit('.', 1)
    ext = ext[1].lower() if len(ext) > 1 else 'jpg'
    content_type = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp'}.get(ext, 'image/jpeg')

    try:
        avatar_mgr = AvatarManager()
        cached = avatar_mgr._get_avatar_cache(filename)
        if cached:
            return Response(content=cached['content'].encode('latin-1'), media_type=content_type)

        response = avatar_mgr.client.get_object(avatar_mgr.bucket_name, filename)
        file_content = response.read()
        response.close()
        response.release_conn()
        avatar_mgr._set_avatar_cache(filename, file_content, content_type)
        return Response(content=file_content, media_type=content_type)
    except Exception as e:
        if "Object does not" in str(e) or "NoSuchKey" in str(e):
            return JSONResponse({"status_code": 404, "message": "头像不存在", "success": False}, status_code=404)
        return JSONResponse({"status_code": 500, "message": f"获取头像失败: {str(e)}", "success": False}, status_code=500)


# ==================== 书籍 RAG ====================
@app.post("/api/book/upload/")
async def api_upload_book(request: Request):
    content_type = request.headers.get('content-type', '')
    if 'multipart/form-data' in content_type:
        form = await request.form()
        file, book_name = form.get('file'), form.get('book_name') or (form.get('file').filename.replace('.pdf', '') if form.get('file') else '')
    else:
        file, book_name = None, None

    if not file: return create_response(400, "请选择要上传的书籍文件", False)
    if not file.filename.lower().endswith('.pdf'): return create_response(400, "仅支持 PDF 格式", False)

    file_content = await file.read()
    file_hash = hashlib.md5(file_content).hexdigest()
    cache_key = f"book:hash:{file_hash}"

    existing = book_redis.get(cache_key)
    if existing: return create_response(409, f"该书籍已上传过（{existing}）", False, {"book_name": existing})

    def _upload():
        try:
            object_name = book_rag_service.rustfs.upload_book(file_content, file.filename)
            chunk_count, error = book_rag_service.process_book(None, book_name, object_name)
            if error: return create_response(500, error, False)
            book_redis.setex(cache_key, 86400 * 30, book_name)
            return create_response(200, "书籍上传成功", True, {"book_name": book_name, "chunk_count": chunk_count})
        except Exception as e:
            return create_response(500, f"服务器错误: {str(e)}", False)

    return await run_sync(_upload)

@app.get("/api/book/list/")
async def api_list_books():
    books = book_rag_service.get_book_list()
    return create_response(200, "查询成功", True, {"books": books, "total": len(books)})

@app.post("/api/book/query/")
async def api_query_book(request: Request):
    data = await request.json()
    question, book_name = data.get('question'), data.get('book_name')
    if not question: return create_response(400, "问题不能为空", False)
    answer, chunks = book_rag_service.ask(question, book_name)
    references = [{"book_name": c['metadata'].get('book_name', '未知'), "content": c['content'][:200] + "..." if len(c['content']) > 200 else c['content'], "relevance_score": c.get('relevance_score', 0)} for c in chunks]
    return create_response(200, "查询成功", True, {"answer": answer, "references": references})


# ==================== 固件 ====================
@app.post("/api/firmware/upload/")
async def api_firmware_upload(request: Request):
    content_type = request.headers.get('content-type', '')
    if 'multipart/form-data' in content_type:
        form = await request.form()
        file, filename = form.get('file'), form.get('file').filename if form.get('file') else None
        file_data = await file.read() if file else None
    else:
        file_data, filename = await request.body(), request.query_params.get("filename") or "firmware.bin"
    if not file_data: return create_response(400, "请求体为空", False)
    success, msg, data = firmware_mgr.upload_firmware(file_data, filename)
    return create_response(200 if success else 400, msg, success, data=data)

@app.get("/api/firmware/download/")
async def api_firmware_download(request: Request):
    filename = request.query_params.get("filename")
    username = request.query_params.get("username")
    if not filename: return create_response(400, "filename required", False)
    flask_resp, status = firmware_mgr.download_firmware(filename, username)
    if hasattr(flask_resp, 'response'):
        data = flask_resp.response[0]
        headers = dict(flask_resp.headers)
        return Response(content=data, media_type=headers.get('Content-Type', 'application/octet-stream'), headers={'Content-Disposition': headers.get('Content-Disposition', f'attachment; filename={filename}')}, status_code=status)
    return flask_resp

@app.get("/api/firmware/list/")
async def api_firmware_list():
    return create_response(200, "success", True, firmware_mgr.list_firmware())

@app.post("/api/firmware/delete/")
async def api_firmware_delete(request: Request):
    data = await request.json()
    filename = data.get("filename")
    if not filename: return create_response(400, "filename required", False)
    success, msg, _ = firmware_mgr.delete_firmware(filename)
    return create_response(200 if success else 400, msg, success)


# ==================== AI 聊天 ====================
@app.post("/api/ai_chat/")
async def api_ai_chat(request: Request):
    """AI 聊天（HTTP）：请求入队，立即返回 task_id，前端轮询 /api/ai_chat/result/"""
    from functions.ai.ai_chat_producer import publish_ai_chat
    from functions.ai.ai_chat_result import init_task
    from functions.ai.ai_chat import AI_DAILY_LIMIT

    data = await request.json()
    message, history, username = data.get("message"), data.get("history", []), data.get("username")
    if not message: return create_error_response("Message required", CODE_ERROR)

    # 每日限制前置检查（与旧逻辑一致，duasong 用户不受限）
    from functions.ai.ai_chat import get_ai_usage_count
    if username and username != "duasong":
        count = get_ai_usage_count(username)
        if count >= AI_DAILY_LIMIT:
            return create_ai_response(403, f"Daily limit reached ({count}/{AI_DAILY_LIMIT})", False,
                                      daily_usage=count, daily_limit=AI_DAILY_LIMIT)

    task_id = publish_ai_chat(message, history, username, channel="http")
    if not task_id:
        return create_error_response("消息队列不可用，请稍后重试", CODE_ERROR)

    init_task(task_id)
    return create_ai_response(200, "任务已提交", True, extra={"task_id": task_id})


@app.get("/api/ai_chat/result/")
async def api_ai_chat_result(task_id: str = None):
    """AI 聊天结果轮询接口（HTTP 异步模式）"""
    from functions.ai.ai_chat_result import get_result
    if not task_id:
        return create_error_response("缺少 task_id", CODE_ERROR)

    result = get_result(task_id)
    if result is None:
        return create_response(404, "任务不存在或已过期", False)

    status = result.get("status")
    if status == "pending":
        return create_ai_response(202, "任务处理中", False, extra={"status": "pending"})
    if status == "done":
        return create_ai_response(200, "Success", True,
                                  answer=result.get("answer"),
                                  tool_calls=result.get("tool_calls"),
                                  daily_usage=result.get("daily_usage"),
                                  daily_limit=result.get("daily_limit"))
    # error
    return create_ai_response(500, result.get("message") or "AI 处理失败", False,
                              daily_usage=result.get("daily_usage"),
                              daily_limit=result.get("daily_limit"))


# ==================== Ansible 任务接口 ====================

@app.post("/api/ansible/replace/")
async def ansible_replace(request: Request):
    """替换远程文件"""
    from functions.ansible.ansible_tasks import ansible_runner
    data = await request.json()
    hosts = data.get('hosts', [])
    target_path = data.get('target_path')
    file_content = data.get('file_content')
    file_mode = data.get('file_mode', '0644')

    if not hosts: return create_response(400, "hosts 参数不能为空", False)
    if not target_path: return create_response(400, "target_path 参数不能为空", False)
    if not file_content: return create_response(400, "file_content 参数不能为空", False)

    result = ansible_runner.replace_file(hosts, target_path, file_content, file_mode)
    if result['success']:
        return create_response(200, "文件替换成功", True, result)
    else:
        return create_response(500, "文件替换失败", False, result)


@app.post("/api/ansible/service/")
async def ansible_service(request: Request):
    """管理 Systemd 服务"""
    from functions.ansible.ansible_tasks import ansible_runner
    data = await request.json()
    hosts = data.get('hosts', [])
    service_name = data.get('service_name')
    service_path = data.get('service_path')
    service_content = data.get('service_content')
    service_state = data.get('service_state', 'started')

    if not hosts: return create_response(400, "hosts 参数不能为空", False)
    if not service_name: return create_response(400, "service_name 参数不能为空", False)
    if not service_path: return create_response(400, "service_path 参数不能为空", False)
    if not service_content: return create_response(400, "service_content 参数不能为空", False)

    result = ansible_runner.manage_service(hosts, service_name, service_path, service_content, service_state)
    if result['success']:
        return create_response(200, "服务管理成功", True, result)
    else:
        return create_response(500, "服务管理失败", False, result)


@app.post("/api/ansible/command/")
async def ansible_command(request: Request):
    """执行远程命令"""
    from functions.ansible.ansible_tasks import ansible_runner
    data = await request.json()
    hosts = data.get('hosts', [])
    cmd = data.get('cmd')

    if not hosts: return create_response(400, "hosts 参数不能为空", False)
    if not cmd: return create_response(400, "cmd 参数不能为空", False)

    result = ansible_runner.execute_command(hosts, cmd)
    if result['success']:
        return create_response(200, "命令执行成功", True, result)
    else:
        return create_response(500, "命令执行失败", False, result)


@app.post("/api/ansible/test/")
async def ansible_test(request: Request):
    """测试 Ansible 连接"""
    from functions.ansible.ansible_tasks import ansible_runner
    data = await request.json()
    hosts = data.get('hosts', [])

    result = ansible_runner.test_connection(hosts if hosts else None)
    if result['success']:
        return create_response(200, "连接测试成功", True, result)
    else:
        return create_response(500, "连接测试失败", False, result)


# ==================== 健康检查 ====================
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "UploadDetection API", "version": "2.0.0", "framework": "FastAPI"}

@app.get("/")
async def root():
    return {"service": "UploadDetection API", "version": "2.0.0", "framework": "FastAPI", "docs": "/docs"}


# ==================== 启动 ====================
if __name__ == "__main__":
    uvicorn.run("app:socket_app", host="0.0.0.0", port=5000, reload=True)
