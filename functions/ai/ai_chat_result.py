"""
AI 聊天 - 结果存储（Redis）

消费者把 AI 结果写到 Redis（TTL 10 分钟），HTTP 请求方凭 task_id 轮询取回。
"""
import json
import redis

from config import REDIS_URL

# 结果 TTL：10 分钟（AI 最长 120 秒，留足余量）
RESULT_TTL = 600
# 状态：pending（等待中）| done（已完成）| error（失败）
KEY_PREFIX = "ai_chat:result:"

_r = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def init_task(task_id: str) -> None:
    """初始化任务状态为 pending"""
    _r.setex(f"{KEY_PREFIX}{task_id}", RESULT_TTL, json.dumps({
        "status": "pending", "answer": None, "tool_calls": None,
        "daily_usage": None, "daily_limit": None, "message": None,
    }))


def save_result(task_id: str, status: str, answer: str = None, tool_calls: list = None,
                daily_usage: int = None, daily_limit: int = None, message: str = None) -> None:
    """消费者保存最终结果"""
    _r.setex(f"{KEY_PREFIX}{task_id}", RESULT_TTL, json.dumps({
        "status": status, "answer": answer, "tool_calls": tool_calls,
        "daily_usage": daily_usage, "daily_limit": daily_limit, "message": message,
    }))


def get_result(task_id: str) -> dict | None:
    """请求方轮询取回结果；不存在返回 None"""
    raw = _r.get(f"{KEY_PREFIX}{task_id}")
    if not raw:
        return None
    return json.loads(raw)
