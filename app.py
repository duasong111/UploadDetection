from flask_socketio import SocketIO, emit
from Common.Response import create_response
from flask import Flask, request
from functions.user import LoginFunction, RegisterFunction, UserContributionView, ChangePasswordView
from functions.device import ListDevicesView, QueryDeviceOnlineHistoryView, StaticRunTimeView
from functions.frp import QueryFrpDeviceUptimeView,UpdateFrpConfigView,UpdateN2NConfigView
from functions.ssh_config import AddLicenseView, BatchDeployView
from functions.device_query import QueryDeviceView
from functions.avatar import AvatarManager
from functions.ai_chat import ai_chat_view
from database.operateFunction import execuFunction
from functions.transmission import Configuration
from flask_cors import CORS
from http import HTTPStatus
checkLogin = LoginFunction()
registerFunc = RegisterFunction()
userContributionView = UserContributionView()
changePasswordView = ChangePasswordView()
avatar_manager = AvatarManager()
db_function = execuFunction()
config = Configuration()


app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ==================== 书籍 RAG 接口 ====================
from functions.book_rag import book_rag_service
import redis
import hashlib
from config import REDIS_URL

book_redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
BOOK_HASH_PREFIX = "book:hash:"  # 书籍文件哈希缓存前缀
BOOK_HASH_TTL = 86400 * 30  # 30天过期


# ==================== WebSocket 事件 ====================
@socketio.on('connect')
def handle_connect():
    """客户端连接"""
    print(f"客户端连接: {request.sid}")
    emit('connected', {'sid': request.sid})


@socketio.on('disconnect')
def handle_disconnect():
    """客户端断开"""
    print(f"客户端断开: {request.sid}")


@socketio.on('ai_chat')
def handle_ai_chat(data):
    """处理 AI 聊天 WebSocket 事件"""
    from functions.ai_chat import ai_chat_view

    message = data.get('message')
    history = data.get('history', [])
    username = data.get('username')

    if not message:
        emit('ai_response', {'success': False, 'message': '消息内容不能为空'})
        return

    # 调用 AI
    result, success = ai_chat_view.chat(message, history, username)

    # 通过 WebSocket 发送结果
    emit('ai_response', result)

# 不计入请求统计的接口
EXCLUDED_PATHS = {'/api/login/', '/api/register/', '/api/user_contributions/'}

@app.before_request
def track_request_count():
    """统计所有API请求次数"""
    from functions.user import UserContributionView

    # 排除不需要计数的接口
    if request.path in EXCLUDED_PATHS:
        return

    # 仅统计 /api/ 开头的请求
    if not request.path.startswith('/api/'):
        return

    # 从请求中获取用户名
    username = None

    # 1. 尝试从请求体获取
    if request.is_json:
        data = request.get_json(silent=True) or {}
        username = data.get('username')

    # 2. 尝试从 header 获取
    if not username:
        username = request.headers.get('X-Username')

    # 3. 尝试从 query string 获取
    if not username:
        username = request.args.get('username')

    if username:
        UserContributionView.increment_request_count(username)


@app.route("/api/register/", methods=["POST"], strict_slashes=False)
def register():
    try:
        data = request.get_json()
        user = data.get('username')
        pwd = data.get('password')
        return registerFunc.register(user, pwd)
    except Exception as e:
        return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)

@app.route("/api/login/", methods=["POST"], strict_slashes=False)
def login():
    try:
        data = request.get_json()
        user = data.get('username')
        pwd = data.get('password')
        return checkLogin.checklogin(user, pwd)
    except Exception as e:
        return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)

# 修改密码
app.add_url_rule(
    '/api/change_password/',
    view_func=ChangePasswordView.as_view('change_password'),
    methods=['POST']
)

# 展示设备列表
app.add_url_rule(
    '/api/list_devices/',
    view_func=ListDevicesView.as_view('list_devices'),
    methods=['GET']
)

# 查询设备上线历史
app.add_url_rule(
    '/api/query_device_online_history/',
    view_func=QueryDeviceOnlineHistoryView.as_view('query_device_online_history'),
    methods=['POST']
)

# 统计系统开始时间
app.add_url_rule(
    '/api/static_time/',
    view_func=StaticRunTimeView.as_view('static_runtime'),
    methods=['POST']
)

# 查询FRP设备在线表
app.add_url_rule(
    '/api/device_uptime/',
    view_func=QueryFrpDeviceUptimeView.as_view('query_frp_device_uptime'),
    methods=['POST']
)

# FRP 配置更新接口
app.add_url_rule(
    '/api/frp_config_update/',
    view_func=UpdateFrpConfigView.as_view('update_frp_config'),
    methods=['POST']
)

# N2N配置更新接口
app.add_url_rule(
    '/api/n2n_config_update/',
    view_func=UpdateN2NConfigView.as_view('update_n2n_config'),
    methods=['POST']
)

# 设备检验流程配置
app.add_url_rule(
    '/api/quick_configuration/',
    view_func=Configuration.as_view('quick_configuration'),
    methods=['POST']
)
# 远程增加鉴权文件
app.add_url_rule(
    '/api/add_license/',
    view_func=AddLicenseView.as_view('add_license'),
    methods=['POST']
)

# 批量部署SSH配置文件
app.add_url_rule(
    '/api/batch_deploy/',
    view_func=BatchDeployView.as_view('batch_deploy'),
    methods=['POST']
)

# 查询设备信息
app.add_url_rule(
    '/api/query_device/',
    view_func=QueryDeviceView.as_view('query_device'),
    methods=['POST']
)

# 用户贡献统计（每日登录次数）
@app.route("/api/user_contributions/", methods=["POST"], strict_slashes=False)
def user_contributions():
    try:
        data = request.get_json()
        username = data.get('username')
        month = data.get('month')  # 格式: "YYYY-MM"
        return userContributionView.get_contributions(username, month)
    except Exception as e:
        return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)


# 用户头像上传
@app.route("/api/upload_avatar/", methods=["POST"], strict_slashes=False)
def upload_avatar():
    try:
        username = request.form.get('username') or (request.json.get('username') if request.is_json else None)
        file = request.files.get('file')

        if not username:
            return create_response(HTTPStatus.BAD_REQUEST, "用户名为必填项", False)
        if not file:
            return create_response(HTTPStatus.BAD_REQUEST, "文件为必填项", False)

        return avatar_manager.upload_avatar(username, file)
    except Exception as e:
        return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)

# 获取用户头像
@app.route("/api/avatar/<path:filename>/", methods=["GET"], strict_slashes=False)
@app.route("/api/avatar/<path:filename>", methods=["GET"], strict_slashes=False)
def get_avatar(filename):
    try:
        return avatar_manager.get_avatar(filename)
    except Exception as e:
        return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)


# AI 聊天接口
app.add_url_rule(
    '/api/ai_chat/',
    view_func=ai_chat_view,
    methods=['POST']
)



# 上传书籍
@app.route("/api/book/upload/", methods=["POST"], strict_slashes=False)
def upload_book():
    try:
        if 'file' not in request.files:
            return create_response(HTTPStatus.BAD_REQUEST, "请选择要上传的书籍文件", False)

        file = request.files['file']
        book_name = request.form.get('book_name') or file.filename.replace('.pdf', '')

        if not file.filename.lower().endswith('.pdf'):
            return create_response(HTTPStatus.BAD_REQUEST, "仅支持 PDF 格式的书籍文件", False)

        # 读取文件内容
        file_content = file.read()
        print(f"[上传] 文件大小: {len(file_content)} bytes")

        # 计算文件哈希（用于去重）
        file_hash = hashlib.md5(file_content).hexdigest()
        cache_key = f"{BOOK_HASH_PREFIX}{file_hash}"
        print(f"[上传] 文件哈希: {file_hash}")

        # 检查是否已上传过
        existing = book_redis.get(cache_key)
        if existing:
            print(f"[上传] 检测到重复上传: {existing}")
            return create_response(
                HTTPStatus.CONFLICT,
                f"该书籍已上传过（{existing}），请勿重复上传",
                False,
                data={"book_name": existing}
            )

        # 上传到 RUSTFS
        print(f"[上传] 上传到 RUSTFS: {file.filename}")
        object_name = book_rag_service.rustfs.upload_book(file_content, file.filename)
        print(f"[上传] RUSTFS 对象名: {object_name}")

        # 处理书籍（切片 + 向量化）
        print(f"[上传] 开始处理书籍: {book_name}")
        chunk_count, error = book_rag_service.process_book(None, book_name, object_name)
        print(f"[上传] 处理完成: chunk_count={chunk_count}, error={error}")

        if error:
            return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, error, False)

        # 存入 Redis 缓存（30天过期）
        book_redis.setex(cache_key, BOOK_HASH_TTL, book_name)
        print(f"[上传] 已缓存书籍哈希: {book_name}")

        return create_response(HTTPStatus.OK, "书籍上传成功", True, data={
            "book_name": book_name,
            "chunk_count": chunk_count
        })

    except Exception as e:
        print(f"[上传] 上传失败: {e}")
        return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)

# 获取书籍列表
@app.route("/api/book/list/", methods=["GET"], strict_slashes=False)
def list_books():
    try:
        books = book_rag_service.get_book_list()
        return create_response(HTTPStatus.OK, "查询成功", True, data={
            "books": books,
            "total": len(books)
        })
    except Exception as e:
        return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)

# 书籍 RAG 问答
@app.route("/api/book/query/", methods=["POST"], strict_slashes=False)
def query_book():
    try:
        data = request.get_json()
        question = data.get('question')
        book_name = data.get('book_name')  # 可选，指定书籍

        if not question:
            return create_response(HTTPStatus.BAD_REQUEST, "问题不能为空", False)

        print(f"[问答] 问题: {question}, 书籍: {book_name}")

        answer, chunks = book_rag_service.ask(question, book_name)
        print(f"[问答] 回答: {answer[:100]}...")

        # 构建参考来源
        references = []
        for chunk in chunks:
            references.append({
                "book_name": chunk['metadata'].get('book_name', '未知'),
                "content": chunk['content'][:200] + "..." if len(chunk['content']) > 200 else chunk['content'],
                "relevance_score": chunk.get('relevance_score', 0)
            })

        return create_response(HTTPStatus.OK, "查询成功", True, data={
            "answer": answer,
            "references": references
        })

    except Exception as e:
        print(f"[问答] 失败: {e}")
        import traceback
        traceback.print_exc()
        return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)


if __name__ == '__main__':

    socketio.run(app, host='0.0.0.0', port=5000, debug=False)