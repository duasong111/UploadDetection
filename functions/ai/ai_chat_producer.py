"""
AI 聊天消息 - 生产者

将 AI 聊天请求投递到 RabbitMQ 异步处理，彻底与全局线程池解耦：
HTTP 接口不再被 DeepSeek 长时间调用阻塞，线程池饿死风险消除。

结果回调方式：HTTP → 请求方轮询 /api/ai_chat/result/；Socket.IO → 消费者直推房间。
"""
import json
import logging
import time
import uuid as uuid_lib

import pika

import config
from Common import rabbitmq

logger = logging.getLogger(__name__)

_MAX_RETRY = 3


def publish_ai_chat(message: str, history: list = None, username: str = None,
                    channel: str = "http", sid: str = None) -> str:
    """投递 AI 聊天请求，返回任务 ID（请求方凭此轮询结果）"""
    task_id = str(uuid_lib.uuid4())
    body = {
        "task_id": task_id,
        "message": message,
        "history": history or [],
        "username": username,
        "channel": channel,     # http | socketio
        "sid": sid,             # Socket.IO 客户端会话 ID（channel=socketio 时使用）
    }

    ch = rabbitmq.get_channel()
    for attempt in range(1, _MAX_RETRY + 1):
        try:
            ch.basic_publish(
                exchange=config.AI_CHAT_EXCHANGE,
                routing_key=config.AI_CHAT_ROUTING_KEY,
                body=json.dumps(body, ensure_ascii=False),
                properties=pika.BasicProperties(
                    delivery_mode=2,          # 持久化：消费者重启不丢请求
                    content_type="application/json",
                ),
            )
            return task_id
        except (pika.exceptions.AMQPConnectionError, pika.exceptions.ChannelWrongStateError) as e:
            if attempt < _MAX_RETRY:
                logger.warning(f"AI 聊天投递失败（第 {attempt} 次）: {e}")
                time.sleep(0.5 * attempt)
                try:
                    rabbitmq.get_channel()
                except Exception:
                    pass
            else:
                logger.error(f"AI 聊天投递失败（已重试 {_MAX_RETRY} 次）: {e}")
    return None
