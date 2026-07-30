"""
AI Chat 模块
支持 Tool Calling / Parallel / Agent Loop
"""
import requests
import json
import re
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
    Agent Loop: 支持流式输出 + 并行执行 + 循环调用工具直到完成

    Args:
        messages: 消息列表
        tools: 工具列表
        max_iterations: 最大迭代次数
        stream_callback: 流式回调函数，接收 (chunk: str, is_final: bool)
                        - chunk: 收到的文本片段
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
            "stream": False  # 非流式，tool_calls 完整
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

        # 非流式读取响应
        full_content = ""
        assistant_message = {}

        result = response.json()
        msg = result.get("choices", [{}])[0].get("message", {})
        full_content = msg.get("content", "") or ""
        tool_calls_data = msg.get("tool_calls", []) or []

        # 过滤掉工具执行标记
        full_content = re.sub(r'\[TOOL_[^\]]*\](?:\[[^\]]*\])?', '', full_content).strip()
        full_content = re.sub(r'\[TOOL_COMPLETE\]', '', full_content).strip()
        full_content = re.sub(r'\[TOOL_ERROR\][^\[]*', '', full_content).strip()

        # 逐字触发流式回调
        if stream_callback and full_content:
            for char in full_content:
                stream_callback(char, False)

        for tc in tool_calls_data:
            if not tc or not tc.get("id"):
                continue
            func = tc.get("function") or {}
            if not func.get("name"):
                continue
            assistant_message.setdefault("tool_calls", [])
            assistant_message["tool_calls"].append({
                "id": tc.get("id"),
                "type": "function",
                "function": {"name": func["name"], "arguments": func.get("arguments") or "{}"}
            })

        # 检查工具调用
        if "tool_calls" not in assistant_message or not assistant_message["tool_calls"]:
            # 没有工具调用，直接返回回答
            print(f"[Agent] No more tools, full_content='{full_content[:100]}'")
            if stream_callback:
                stream_callback("", True)  # 标记流结束
            return full_content, True, tool_calls_info

        print(f"[Agent] Found {len(assistant_message['tool_calls'])} tool calls")

        # 规范化 tool_calls：确保每个都有 type: "function"
        valid_tool_calls = []
        for tc in assistant_message["tool_calls"]:
            if not tc or not tc.get("function", {}).get("name"):
                continue
            valid_tool_calls.append({
                "id": tc.get("id"),
                "type": "function",
                "function": {
                    "name": tc["function"]["name"],
                    "arguments": tc["function"].get("arguments", "{}")
                }
            })
        assistant_message["tool_calls"] = valid_tool_calls
        if stream_callback:
            tool_names = [tc.get("function", {}).get("name") for tc in assistant_message["tool_calls"]]
            tool_names = [n for n in tool_names if n]
            if tool_names:
                stream_callback(f"[TOOL_CALLS] {', '.join(tool_names)}", False)

        messages.append({
            "role": "assistant",
            "content": full_content,
            "tool_calls": [
                {"id": tc.get("id"), "type": "function", "function": {"name": tc["function"]["name"], "arguments": tc["function"].get("arguments") or "{}"}}
                for tc in assistant_message["tool_calls"]
                if tc and tc.get("function", {}).get("name")
            ]
        })

        # 并行执行所有工具
        for tool_call in assistant_message["tool_calls"]:
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

        # 调用流式 Agent Loop
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


ai_chat_view = AIChatView.as_view("ai_chat")
