"""
AI Chat 模块
支持 Tool Calling / Parallel / Agent Loop
"""
import requests
import json
import re
import threading
from flask.views import View
from flask import request
from http import HTTPStatus
from datetime import date

from functions.tools.tools_calling_export import AVAILABLE_TOOLS, TOOL_MAP
from config import AI_AGENT_KEY, CODE_ERROR
from Common.Response import create_ai_response, create_error_response
from database.Postgresql import get_postgres_connection

# DeepSeek 配置
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
AI_DAILY_LIMIT = 20  # 每日限制

# System prompt
SYSTEM_PROMPT = """你是一个专业的AI助手，帮助用户解决设备管理和技术问题。

回答要求：
1. 简洁明了，抓住重点，直接回答用户问题
2. 不要说无关的客套话
3. 数据类回答要具体，如"设备303当前在线，最后上报时间14:30"
4. 如果调用了工具，以工具返回的数据为准回答
5. 如果工具返回数据不足以回答，说明情况即可，不要编造答案

如果需要查询天气或设备信息，请使用工具。"""

# Loop Agent 提示词
LOOP_PROMPT = """请根据工具执行结果判断任务是否完成：
1. 如果任务完成，直接给出最终回答
2. 如果任务未完成或有错误，说明需要补充的信息
3. 不要重复调用已经成功执行的工具"""


def get_ai_usage_count(username: str) -> int:
    """获取用户当日AI使用次数"""
    try:
        conn = get_postgres_connection()
        today = date.today().isoformat()
        with conn.cursor() as cur:
            cur.execute(
                'SELECT login_history->\'ai_chat\'->>%s FROM "user" WHERE name = %s',
                (today, username)
            )
            row = cur.fetchone()
        return int(row[0]) if row and row[0] else 0
    except:
        return 0
    finally:
        if 'conn' in dir():
            conn.close()


def increment_ai_usage_count(username: str):
    """增加用户当日AI使用次数"""
    try:
        conn = get_postgres_connection()
        today = date.today().isoformat()
        with conn.cursor() as cur:
            cur.execute(
                '''UPDATE "user" SET login_history = COALESCE(login_history, '{}'::jsonb) ||
                    jsonb_build_object('ai_chat', jsonb_build_object(%s, COALESCE((login_history->'ai_chat'->>%s)::int, 0) + 1))
                    WHERE name = %s''',
                (today, today, username)
            )
            conn.commit()
    except:
        pass
    finally:
        if 'conn' in dir():
            conn.close()


def execute_tool_call(tool_call: dict) -> dict:
    """执行单个工具调用"""
    func = tool_call.get("function") or {}
    fn_name = func.get("name")
    if not fn_name:
        return {"name": None, "args": {}, "result": {"success": False, "message": "Tool call missing function name"}, "success": False}
    args_str = func.get("arguments", "{}")

    # 解析参数
    try:
        args = json.loads(args_str) if isinstance(args_str, str) else args_str
    except:
        args = {}

    print(f"[Agent] Executing: {fn_name} with {args}")

    try:
        if fn_name in TOOL_MAP:
            result = TOOL_MAP[fn_name](**args)
            return {"name": fn_name, "args": args, "result": result, "success": True}
        else:
            return {"name": fn_name, "args": args, "result": {"success": False, "message": f"Unknown tool: {fn_name}"}, "success": False}
    except Exception as e:
        print(f"[Agent] Tool error: {e}")
        return {"name": fn_name, "args": args, "result": {"success": False, "message": str(e)}, "success": False}


def call_deepseek_streaming(messages: list, tools: list = None, max_iterations: int = 10,
                            stream_callback=None) -> tuple:
    """
    Agent Loop: 真流式（SSE）输出 + 并行执行 + 循环调用工具直到完成

    Args:
        messages: 消息列表
        tools: 工具列表
        max_iterations: 最大迭代次数
        stream_callback: 流式回调函数，接收 (chunk: str, is_final: bool)
                        - chunk: 收到的文本片段（SSE 增量，非逐字模拟）
                        - is_final: 是否是最终片段

    Returns:
        (answer, success, tool_calls_info)
    """
    headers = {
        "Authorization": f"Bearer {AI_AGENT_KEY}",
        "Content-Type": "application/json"
    }

    tool_calls_info = []
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
        print(f"[Agent] Iteration {iteration}/{max_iterations}")

        payload = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "stream": True  # 真流式：SSE 增量返回
        }
        if tools:
            payload["tools"] = tools

        try:
            response = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=120, stream=True)
        except requests.exceptions.Timeout:
            return "Request timeout", False, tool_calls_info
        except Exception as e:
            return f"Request failed: {e}", False, tool_calls_info

        if response.status_code != 200:
            return f"API error: {response.text}", False, tool_calls_info

        # ============ 流式解析 SSE ============
        full_content = ""
        tool_calls_acc = {}  # index -> {id, type, function:{name, arguments}}（工具调用增量拼接）

        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            try:
                delta = data["choices"][0].get("delta", {})
            except (KeyError, IndexError):
                continue

            # 文本增量：逐 chunk 触发流式回调（打字机效果）
            content = delta.get("content")
            if content:
                full_content += content
                if stream_callback:
                    stream_callback(content, False)

            # 工具调用增量：按 index 累积拼接（name/arguments 都是增量片段）
            for tc in (delta.get("tool_calls") or []):
                idx = tc.get("index", 0)
                acc = tool_calls_acc.setdefault(idx, {
                    "id": None, "type": "function", "function": {"name": "", "arguments": ""}
                })
                if tc.get("id"):
                    acc["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    acc["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    acc["function"]["arguments"] += fn["arguments"]

        # 过滤掉工具执行标记
        full_content = re.sub(r'\[TOOL_[^\]]*\](?:\[[^\]]*\])?', '', full_content).strip()
        full_content = re.sub(r'\[TOOL_COMPLETE\]', '', full_content).strip()
        full_content = re.sub(r'\[TOOL_ERROR\][^\[]*', '', full_content).strip()

        # 组装 assistant_message 的 tool_calls（只保留完整片段）
        valid_tool_calls = []
        for idx in sorted(tool_calls_acc.keys()):
            tc = tool_calls_acc[idx]
            if tc["id"] and tc["function"]["name"]:
                valid_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"].get("arguments") or "{}"
                    }
                })

        # 检查工具调用
        if not valid_tool_calls:
            # 没有工具调用，直接返回回答
            print(f"[Agent] No more tools, full_content='{full_content[:100]}'")
            if stream_callback:
                stream_callback("", True)  # 标记流结束
            return full_content, True, tool_calls_info

        print(f"[Agent] Found {len(valid_tool_calls)} tool calls")

        if stream_callback:
            tool_names = [tc.get("function", {}).get("name") for tc in valid_tool_calls]
            tool_names = [n for n in tool_names if n]
            if tool_names:
                stream_callback(f"[TOOL_CALLS] {', '.join(tool_names)}", False)

        messages.append({
            "role": "assistant",
            "content": full_content,
            "tool_calls": [
                {"id": tc.get("id"), "type": "function", "function": {"name": tc["function"]["name"], "arguments": tc["function"].get("arguments") or "{}"}}
                for tc in valid_tool_calls
                if tc and tc.get("function", {}).get("name")
            ]
        })

        # 并行执行所有工具
        for tool_call in valid_tool_calls:
            tool_result = execute_tool_call(tool_call)
            tool_calls_info.append(tool_result)

            # 跳过无效工具调用
            if not tool_result["name"]:
                continue

            # 添加结果到消息
            messages.append({
                "role": "tool",
                "type": "tool",
                "tool_call_id": tool_call.get("id"),
                "content": json.dumps(tool_result["result"], ensure_ascii=False)
            })

            # 检查工具执行结果
            if stream_callback:
                stream_callback(f"[TOOL_ERROR] {tool_result['name']}: {tool_result['result']}", False)

        if stream_callback:
            stream_callback("[TOOL_COMPLETE]", False)

        print(f"[Agent] All tools executed, continuing loop")

    if stream_callback:
        stream_callback("", True)
    return "Max iterations reached", False, tool_calls_info


def call_deepseek(messages: list, tools: list = None, max_iterations: int = 10) -> tuple:
    """
    Agent Loop: 支持并行执行 + 循环调用工具直到完成 (非流式版本)

    Args:
        messages: 消息列表
        tools: 工具列表
        max_iterations: 最大迭代次数

    Returns:
        (answer, success, tool_calls_info)
    """
    def noop_callback(_chunk, _is_final):
        pass
    return call_deepseek_streaming(messages, tools, max_iterations, stream_callback=noop_callback)


# 逐字回调（打字机效果）用的空格缩进常量：兼容 Markdown 渲染时逐字追加丢缩进
_STREAM_SPACE_PAD = " "


class AIChatView(View):
    """AI Chat 视图 (Agent Loop)"""

    def chat(self, message: str, history: list = None, username: str = None) -> tuple:
        """处理聊天请求"""
        # 检查每日限制（duasong 用户不受限制）
        if username and username != "duasong":
            count = get_ai_usage_count(username)
            if count >= AI_DAILY_LIMIT:
                return {
                    "message": f"Daily limit reached ({count}/{AI_DAILY_LIMIT})",
                    "daily_usage": count,
                    "daily_limit": AI_DAILY_LIMIT
                }, False

        # 构建消息
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if history:
            for h in history:
                messages.append(h)
        messages.append({"role": "user", "content": message})

        # 调用 Agent Loop
        answer, success, tool_calls = call_deepseek(messages, AVAILABLE_TOOLS)

        # 更新计数
        daily_usage = None
        if username and success:
            increment_ai_usage_count(username)
            daily_usage = get_ai_usage_count(username)

        return {
            "answer": answer,
            "tool_calls": tool_calls,
            "daily_usage": daily_usage,
            "daily_limit": AI_DAILY_LIMIT
        }, success

    def chat_streaming(self, message: str, history: list = None, username: str = None,
                      stream_callback=None) -> tuple:
        """处理流式聊天请求"""
        # 检查每日限制（duasong 用户不受限制）
        daily_usage = None
        if username and username != "duasong":
            count = get_ai_usage_count(username)
            if count >= AI_DAILY_LIMIT:
                if stream_callback:
                    stream_callback(f"[ERROR] Daily limit reached ({count}/{AI_DAILY_LIMIT})", True)
                return f"Daily limit reached ({count}/{AI_DAILY_LIMIT})", False, [], None

        # 构建消息
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if history:
            for h in history:
                messages.append(h)
        messages.append({"role": "user", "content": message})

        # 调用流式 Agent Loop（SSE 逐 chunk）
        answer, success, tool_calls = call_deepseek_streaming(
            messages, AVAILABLE_TOOLS, stream_callback=stream_callback
        )

        # 更新计数
        if username and success:
            increment_ai_usage_count(username)
            daily_usage = get_ai_usage_count(username)

        return answer, success, tool_calls, daily_usage

    def dispatch_request(self):
        """处理 HTTP 请求"""
        data = request.get_json() or {}
        message = data.get("message")
        history = data.get("history", [])
        username = data.get("username")

        if not message:
            return create_error_response("Message required", CODE_ERROR)

        result, success = self.chat(message, history, username)

        if success:
            return create_ai_response(
                HTTPStatus.OK, "Success", True,
                answer=result.get("answer"),
                tool_calls=result.get("tool_calls"),
                daily_usage=result.get("daily_usage"),
                daily_limit=result.get("daily_limit")
            )
        else:
            msg = result.get("message", "Error")
            if "limit" in msg.lower():
                return create_ai_response(
                    HTTPStatus.FORBIDDEN, msg, False,
                    daily_usage=result.get("daily_usage"),
                    daily_limit=result.get("daily_limit")
                )
            return create_error_response(msg, CODE_ERROR)


# ==================== 流式转发（打字机效果）====================
# 消费者进程通过 Redis Pub/Sub（PUBLISH）把 SSE chunk 实时转发给 app 进程，
# app 进程订阅后推送到 Socket.IO 房间。事件约定：
#   "ai_stream_token"  文本增量 chunk
#   "ai_stream_end"    流结束标记（最终完整结果走 ai_chat_result）
_STREAM_CHANNEL_PREFIX = "ai_chat:stream:"


def stream_forward_publish(sid: str, event: str, data: dict) -> None:
    """消费者进程：把流式事件发布到 Redis 转发通道（app 进程订阅后推送 Socket.IO）
    改用 Pub/Sub 实现推送式转发，替代原列表 RPUSH/BRPOP 轮询，降低打字机延迟。
    """
    try:
        from Common.redis_pubsub import publish
        publish(f"{_STREAM_CHANNEL_PREFIX}{sid}", event, data)
    except Exception as e:
        print(f"[ai_stream] 转发发布失败: {e}")


def ai_chat_redis_url() -> str:
    """延迟导入 config，避免循环依赖"""
    from config import REDIS_URL
    return REDIS_URL


def build_stream_callback(sid: str):
    """构造消费者的流式回调：把 DeepSeek 的 chunk 逐个转发到 Redis 通道"""
    from Common.redis_pubsub import publish

    def _cb(chunk: str, is_final: bool):
        # 过滤工具执行标记（不在打字机里显示）
        if chunk and (chunk.startswith("[TOOL_") or chunk == "[TOOL_COMPLETE]" or chunk.startswith("[TOOL_ERROR]")):
            return
        if chunk:
            publish(f"{_STREAM_CHANNEL_PREFIX}{sid}", "ai_stream_token", {"token": chunk})
        if is_final:
            publish(f"{_STREAM_CHANNEL_PREFIX}{sid}", "ai_stream_end", {"final": True})
    return _cb


def consume_sid_stream(sid: str, timeout: float = 300) -> list:
    """app 进程（async 任务）：订阅 Redis 频道，收齐 token 和 end 事件，返回事件列表
    采用后台线程监听 Pub/Sub + 短超时取出，比阻塞型 BRPOP 更贴合 asyncio 事件循环。
    """
    from Common.redis_pubsub import PubSubListener
    listener = PubSubListener(f"{_STREAM_CHANNEL_PREFIX}{sid}")
    try:
        events = []
        end = False
        while not end:
            raw = listener.get_events(timeout=timeout)
            for event, data in raw:
                evt = {"event": event, "data": data}
                events.append(evt)
                if event == "ai_stream_end":
                    end = True
            if raw:
                continue
            break
        return events
    finally:
        listener.close()


ai_chat_view = AIChatView.as_view("ai_chat")
