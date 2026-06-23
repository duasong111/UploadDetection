import requests
from config import AI_AGENT_KEY, CODE_SUCCESS, CODE_ERROR
from Common.Response import create_response
from flask import request
from http import HTTPStatus
from flask.views import View
from datetime import date
from database.Postgresql import get_postgres_connection

# DeepSeek API 配置
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
AI_DAILY_LIMIT = 10  # 每日AI对话次数限制


def get_ai_usage_count(username):
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


def increment_ai_usage_count(username):
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


class AIChatView(View):
    """AI 聊天视图"""

    def chat(self, message, history=None, username=None):
        """
        调用 DeepSeek API 进行聊天
        :param message: 用户消息
        :param history: 对话历史列表
        :param username: 用户名（用于限制次数）
        :return: (result_dict, success_bool)
        """
        try:
            # 检查使用次数
            if username:
                current_count = get_ai_usage_count(username)
                if current_count >= AI_DAILY_LIMIT:
                    return {
                        "success": False,
                        "message": f"今日AI对话次数已用完（{current_count}/{AI_DAILY_LIMIT}），请明天再试",
                        "daily_usage": current_count,
                        "daily_limit": AI_DAILY_LIMIT
                    }, False

            headers = {
                "Authorization": f"Bearer {AI_AGENT_KEY}",
                "Content-Type": "application/json"
            }

            # 构建消息列表
            messages = [
                {"role": "system", "content": "你是一个专业的AI助手，帮助用户解决设备管理和技术问题。你应该友好、专业，并且能够回答关于设备、配置、故障排除等方面的问题。"}
            ]

            # 添加历史对话
            if history:
                for h in history:
                    messages.append(h)

            # 添加用户消息
            messages.append({"role": "user", "content": message})

            payload = {
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "stream": False
            }

            response = requests.post(
                DEEPSEEK_API_URL,
                headers=headers,
                json=payload,
                timeout=60
            )

            if response.status_code == 200:
                result = response.json()
                ai_response = result["choices"][0]["message"]["content"]

                # 增加使用次数
                if username:
                    increment_ai_usage_count(username)
                    current_count = get_ai_usage_count(username)
                else:
                    current_count = None

                return {
                    "success": True,
                    "message": ai_response,
                    "daily_usage": current_count,
                    "daily_limit": AI_DAILY_LIMIT
                }, True
            else:
                error_msg = response.json().get("error", {}).get("message", "Unknown error")
                return {"success": False, "message": f"AI 服务调用失败: {error_msg}"}, False

        except requests.exceptions.Timeout:
            return {"success": False, "message": "AI 服务响应超时，请稍后重试"}, False
        except Exception as e:
            return {"success": False, "message": f"AI 服务异常: {str(e)}"}, False

    def dispatch_request(self):
        """处理 POST 请求"""
        data = request.get_json()
        message = data.get("message")
        history = data.get("history", [])
        username = data.get("username")

        if not message:
            return create_response(CODE_ERROR, "消息内容不能为空", False)

        result, success = self.chat(message, history, username)
        return create_response(CODE_SUCCESS if success else CODE_ERROR, result.get("message", ""), success, data=result if success else None)


# 创建视图实例
ai_chat_view = AIChatView.as_view('ai_chat')