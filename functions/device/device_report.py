"""
设备运行时长上报 - RabbitMQ 生产者

上报接口不再直接写库，而是将消息投递到 device.report 交换机，
由批量消费者攒批后统一落库，实现削峰填谷。
"""
import json
import logging
import time

import pika

import config
from Common import rabbitmq
from Common.Response import create_response
from functions.device.device_heartbeat import refresh_heartbeat

logger = logging.getLogger(__name__)

# 消息投递失败最大重试次数
_MAX_RETRY = 3


def publish_device_report(sn: str, uuid_val: str, runtime: int, report_time: str) -> bool:
    """投递设备上报消息到 RabbitMQ"""
    message = json.dumps({
        "sn": sn,
        "uuid": uuid_val,
        "runtime": runtime,
        "report_time": report_time,   # ISO 格式的上报时间（消费者据此批量落库）
    })

    ch = rabbitmq.get_channel()
    for attempt in range(1, _MAX_RETRY + 1):
        try:
            ch.basic_publish(
                exchange=config.DEVICE_REPORT_EXCHANGE,
                routing_key=config.DEVICE_REPORT_ROUTING_KEY,
                body=message,
                properties=pika.BasicProperties(
                    delivery_mode=2,          # 持久化消息，broker 重启不丢失
                    content_type="application/json",
                ),
            )
            return True
        except (pika.exceptions.AMQPConnectionError, pika.exceptions.ChannelWrongStateError) as e:
            # 连接/通道级异常：丢弃当前线程连接，下次 get_channel 重建全新连接
            if attempt < _MAX_RETRY:
                logger.warning(f"设备上报投递失败（第 {attempt} 次）: {e}，重建连接后重试")
                try:
                    rabbitmq.reset_connection()
                    ch = rabbitmq.get_channel()
                except Exception as reconnect_err:
                    logger.error(f"RabbitMQ 重连失败: {reconnect_err}")
                    time.sleep(0.5 * attempt)
            else:
                logger.error(f"设备上报投递失败（已重试 {_MAX_RETRY} 次）: {e}")
    return False


def save_runtime(sn: str, uuid_val: str, runtime: int) -> dict:
    """设备上报入口：校验参数 → 投递消息 → 立即返回

    与旧版 save_runtime 保持同名同签名，app.py 无需改动；
    返回体与旧版结构一致（客户端 duration_time.py 只读 session_max_runtime）。
    """
    from datetime import datetime, timezone

    # 参数校验（与旧版一致）
    if not sn or not uuid_val or runtime is None:
        return create_response(400, "缺少必要参数", False)
    if not isinstance(runtime, int):
        try:
            runtime = int(runtime)
        except (TypeError, ValueError):
            return create_response(400, "runtime 必须为整数", False)

    now_local = datetime.now(timezone.utc)  # 统一 UTC

    # 刷新 Redis 心跳：在线状态实时判定，不依赖批量落库
    refresh_heartbeat(sn)

    # 投递消息到 RabbitMQ
    try:
        ok = publish_device_report(sn, uuid_val, runtime, now_local.isoformat())
    except Exception as e:
        logger.error(f"RabbitMQ 投递异常: {e}")
        ok = False

    if not ok:
        # MQ 不可用时降级：直接写库，保证设备上报不丢
        logger.warning(f"RabbitMQ 不可用，降级为直写数据库（sn={sn}）")
        from functions.device.device_api import save_runtime as _fallback_save
        return _fallback_save(sn, uuid_val, runtime)

    # 投递成功立即返回（响应结构与旧版一致，客户端无感知）
    return create_response(200, "上报成功", True, {
        "status": "ok",
        "session_max_runtime": runtime,
        "session_first_report": now_local.isoformat(),
        "session_last_report": now_local.isoformat(),
    })
