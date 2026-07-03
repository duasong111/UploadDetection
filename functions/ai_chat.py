import requests
import os
import sys
import json as json_lib
from functions.tools.tools_calling_export import AVAILABLE_TOOLS, TOOL_MAP
from config import AI_AGENT_KEY, CODE_SUCCESS, CODE_ERROR
from Common.Response import create_ai_response, create_success_response, create_error_response
from flask import request
from http import HTTPStatus
from flask.views import View
from datetime import date
from database.Postgresql import get_postgres_connection

# DeepSeek API 配置
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
AI_DAILY_LIMIT = 10  # 每日AI对话次数限制

# 系统提示词
SYSTEM_PROMPT = """你是一个专业的AI助手，帮助用户解决设备管理和技术问题。

    回答要求：
    1. 简洁明了，抓住重点，直接回答用户问题
    2. 不要说无关的客套话，如"好的，我来帮您查询"等
    3. 数据类回答要具体，如"设备303当前在线，最后上报时间14:30"
    4. 如果调用了工具，以工具返回的数据为准回答
    5. 如果工具返回数据不足以回答，说明情况即可，不要编造答案

如果需要查询天气或设备信息，请使用工具。"""


def get_ai_usage_count(username: str) -> int:
    """获取用户当日AI使用次数"""
    try:
        conn = get_postgres_connection()
        today = date.today().isoformat()

        with conn.cursor() as cur:
            cur.execute("""
                SELECT login_history->'ai_chat'->>%s
                FROM "user"
                WHERE name = %s
            """, (today, username))
            row = cur.fetchone()

        if row and row[0]:
            return int(row[0])
        return 0
    except Exception as e:
        print(f"获取AI使用次数失败: {str(e)}")
        return 0
    finally:
        if 'conn' in locals() and conn:
            conn.close()


def increment_ai_usage_count(username: str):
    """增加用户当日AI使用次数"""
    try:
        conn = get_postgres_connection()
        today = date.today().isoformat()

        with conn.cursor() as cur:
            cur.execute("""
                UPDATE "user"
                SET login_history = COALESCE(login_history, '{}'::jsonb) ||
                    jsonb_build_object(
                        'ai_chat', jsonb_build_object(
                            %s, COALESCE((login_history->'ai_chat'->>%s)::int, 0) + 1
                        )
                    )
                WHERE name = %s
            """, (today, today, username))
            conn.commit()
    except Exception as e:
        print(f"更新AI使用次数失败: {str(e)}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()


def call_deepseek(messages: list, tools: list = None, max_iterations: int = 10) -> tuple:
    """
    调用 DeepSeek API，支持并行 Tool Calling

    Args:
        messages: 消息列表
        tools: 工具列表
        max_iterations: 最大迭代次数

    Returns:
        (answer, success, tool_calls_info)
    """
    headers = {
        "Authorization": f"Bearer {AI_AGENT_KEY}",
        "Content-Type": "application/json"
    }

    tool_calls_info = []

    for iteration in range(max_iterations):
        payload = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "stream": False
        }

        if tools:
            payload["tools"] = tools

        # print(f"[DeepSeek] 第 {iteration + 1} 次迭代")

        try:
            response = requests.post(
                DEEPSEEK_API_URL,
                headers=headers,
                json=payload,
                timeout=120
            )
        except requests.exceptions.Timeout:
            return "AI 服务响应超时，请稍后重试", False, tool_calls_info

        if response.status_code != 200:
            error_msg = response.json().get("error", {}).get("message", "Unknown error")
            return f"AI 服务调用失败: {error_msg}", False, tool_calls_info

        result = response.json()
        assistant_message = result["choices"][0]["message"]

        # 检查是否有函数调用
        if "tool_calls" in assistant_message:
            print(f"[DeepSeek] 检测到 {len(assistant_message['tool_calls'])} 个工具调用")
            # 添加 AI 的回复（包含 tool_calls）
            messages.append(assistant_message)

            # 执行每个工具调用
            for tool_call in assistant_message["tool_calls"]:
                function_name = tool_call["function"]["name"]
                arguments = tool_call["function"]["arguments"]

                # 解析参数
                try:
                    args = json_lib.loads(arguments) if isinstance(arguments, str) else arguments
                except Exception as e:
                    print(f"[Tool] 参数解析失败: {e}")
                    args = {}

                print(f"[Tool] 执行工具: {function_name}, 参数: {args}")

                # 执行工具
                try:
                    if function_name in TOOL_MAP:
                        tool_result = TOOL_MAP[function_name](**args)
                    else:
                        tool_result = {"success": False, "message": f"未知工具: {function_name}"}
                except Exception as e:
                    print(f"[Tool] 工具执行失败: {e}")
                    tool_result = {"success": False, "message": f"工具执行失败: {str(e)}"}

                # 记录工具调用信息
                tool_calls_info.append({
                    "name": function_name,
                    "args": args,
                    "result": tool_result
                })

                # 添加工具结果到消息（使用 JSON 字符串）
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": json_lib.dumps(tool_result, ensure_ascii=False)
                })

            print(f"[Tool] 所有工具执行完成，继续迭代")
            # 继续循环，让 AI 生成最终回答
            continue

        # 没有函数调用，返回直接回答
        print(f"[DeepSeek] 返回最终回答")
        return assistant_message["content"], True, tool_calls_info

    return "已达到最大迭代次数（可能存在循环调用）", False, tool_calls_info


class AIChatView(View):
    """AI 聊天视图"""

    def chat(self, message: str, history: list = None, username: str = None) -> tuple:
        """
        调用 DeepSeek API 进行聊天（支持 Tool Calling）

        Args:
            message: 用户消息
            history: 对话历史列表
            username: 用户名（用于限制次数）

        Returns:
            (response_data, success)
        """
        # 检查使用次数
        if username:
            current_count = get_ai_usage_count(username)
            if current_count >= AI_DAILY_LIMIT:
                return {
                    "message": f"今日AI对话次数已用完（{current_count}/{AI_DAILY_LIMIT}），请明天再试",
                    "daily_usage": current_count,
                    "daily_limit": AI_DAILY_LIMIT
                }, False

        # 构建消息列表
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

        # 添加历史对话
        if history:
            for h in history:
                messages.append(h)

        # 添加用户消息
        messages.append({"role": "user", "content": message})

        # 调用 DeepSeek（支持 Tool Calling）
        answer, success, tool_calls = call_deepseek(messages, AVAILABLE_TOOLS)

        # 增加使用次数
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

    def dispatch_request(self):
        """处理 POST 请求"""
        data = request.get_json()
        message = data.get("message")
        history = data.get("history", [])
        username = data.get("username")

        if not message:
            return create_error_response("消息内容不能为空", CODE_ERROR)

        result, success = self.chat(message, history, username)

        if success:
            return create_ai_response(
                HTTPStatus.OK,
                "查询成功",
                True,
                answer=result.get("answer"),
                tool_calls=result.get("tool_calls"),
                daily_usage=result.get("daily_usage"),
                daily_limit=result.get("daily_limit")
            )
        else:
            # 检查是否是次数用完的错误
            if "次数已用完" in result.get("message", ""):
                return create_ai_response(
                    HTTPStatus.FORBIDDEN,
                    result.get("message"),
                    False,
                    daily_usage=result.get("daily_usage"),
                    daily_limit=result.get("daily_limit")
                )
            return create_error_response(result.get("message", "AI 服务异常"), CODE_ERROR)


# 创建视图实例
ai_chat_view = AIChatView.as_view('ai_chat')
