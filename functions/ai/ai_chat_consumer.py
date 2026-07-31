"""
AI 聊天 - 批量消费者（独立进程）

从 ai.chat.request 队列消费请求，调用 DeepSeek Agent Loop 处理：
- HTTP 请求：结果写入 Redis（请求方轮询）
- Socket.IO 请求：结果直推客户端房间

运行方式：python -m functions.ai.ai_chat_consumer

设计要点：
- prefetch=1 + 逐条 ack：一个消费者同一时刻只处理一条，天然支持多消费者横向扩容
- 自动重连：连接断开后无限重试，常驻进程
"""
import json
import logging
import time

import pika

import config
from Common import rabbitmq
from functions.ai.ai_chat_result import save_result
from functions.ai.ai_chat import AIChatView, SYSTEM_PROMPT, AI_DAILY_LIMIT, build_stream_callback

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ai_chat_consumer")

# 单条消息处理超时（秒），超时后消息会被重新投递到其他消费者
_MESSAGE_TIMEOUT = 300


def _handle_message(msg: dict) -> None:
    """处理单条 AI 聊天请求"""
    task_id = msg.get("task_id")
    message = msg.get("message")
    history = msg.get("history") or []
    username = msg.get("username")
    channel = msg.get("channel", "http")
    sid = msg.get("sid")

    if not task_id or not message:
        logger.error(f"消息缺少必要字段，丢弃: {msg}")
        return

    # 每日限制检查（复用 AIChatView 逻辑，duasong 用户不受限）
    from functions.ai.ai_chat import get_ai_usage_count
    if username and username != "duasong":
        count = get_ai_usage_count(username)
        if count >= AI_DAILY_LIMIT:
            save_result(task_id, "error",
                        message=f"Daily limit reached ({count}/{AI_DAILY_LIMIT})",
                        daily_usage=count, daily_limit=AI_DAILY_LIMIT)
            return

    # 构建消息
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": message})

    # 调用 Agent Loop（流式：chunk 实时转发 Redis → app 进程 → Socket.IO 打字机）
    view = AIChatView()
    stream_cb = build_stream_callback(sid) if (channel == "socketio" and sid) else None
    answer, success, tool_calls, daily_usage = view.chat_streaming(
        message, history, username, stream_callback=stream_cb
    )

    # 最终结果统一写入 task_id 键：app.py 循环轮询 task_id，Socket.IO 与 HTTP 共用同一套状态机
    save_result(task_id, "done" if success else "error",
                answer=answer if success else None,
                tool_calls=tool_calls if success else None,
                daily_usage=daily_usage,
                daily_limit=AI_DAILY_LIMIT,
                message=None if success else answer)


def _emit_to_sid(sid: str, payload: dict) -> None:
    """通过 Socket.IO 直推结果到客户端房间（已废弃，保留签名防止引用报错）"""
    pass


def consume():
    """主消费循环"""
    channel = rabbitmq.get_channel()
    rabbitmq.declare_queue(config.AI_CHAT_QUEUE, config.AI_CHAT_EXCHANGE, config.AI_CHAT_ROUTING_KEY)
    # 每个消费者同时只处理一条：保证横向扩容时请求均匀分配
    channel.basic_qos(prefetch_count=1)

    def callback(ch, method, properties, body):
        try:
            msg = json.loads(body)
            _handle_message(msg)
            ch.basic_ack(delivery_tag=method.delivery_tag)
        except Exception as e:
            logger.error(f"消息处理异常: {e}")
            # 处理失败不 ack：消息回到队列，交由其他消费者重试
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

    channel.basic_consume(queue=config.AI_CHAT_QUEUE, on_message_callback=callback)
    logger.info(f"AI 聊天消费者启动，队列: {config.AI_CHAT_QUEUE}")

    while True:
        try:
            channel.start_consuming()
        except (pika.exceptions.AMQPConnectionError, pika.exceptions.ChannelWrongStateError) as e:
            logger.warning(f"RabbitMQ 连接断开: {e}，5 秒后重连...")
            time.sleep(5)
            rabbitmq.reset_connection()
            channel = rabbitmq.get_channel()
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue=config.AI_CHAT_QUEUE, on_message_callback=callback)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"消费循环异常: {e}")
            time.sleep(5)


if __name__ == "__main__":
    consume()
