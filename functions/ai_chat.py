import requests
from config import AI_AGENT_KEY, CODE_SUCCESS, CODE_ERROR
from Common.Response import create_response
from flask import request
from http import HTTPStatus
from flask.views import View

# DeepSeek API 配置
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

class AIChatView(View):
    """AI 聊天视图"""

    def chat(self, message, history=None):
        """
        调用 DeepSeek API 进行聊天
        :param message: 用户消息
        :param history: 对话历史列表
        :return: AI 响应
        """
        try:
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
                return create_response(CODE_SUCCESS, ai_response, True)
            else:
                error_msg = response.json().get("error", {}).get("message", "Unknown error")
                return create_response(CODE_ERROR, f"AI 服务调用失败: {error_msg}", False)

        except requests.exceptions.Timeout:
            return create_response(CODE_ERROR, "AI 服务响应超时，请稍后重试", False)
        except Exception as e:
            return create_response(CODE_ERROR, f"AI 服务调用异常: {str(e)}", False)

    def dispatch_request(self):
        """处理 POST 请求"""
        data = request.get_json()
        message = data.get("message")
        history = data.get("history", [])

        if not message:
            return create_response(CODE_ERROR, "消息内容不能为空", False)

        return self.chat(message, history)

# 创建视图实例
ai_chat_view = AIChatView.as_view('ai_chat')