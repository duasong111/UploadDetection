"""
设备运行时长上报 - 批量消费者（独立进程）

从 device.report.batch 队列消费消息，攒批后批量 UPSERT 落库。
运行方式：python -m functions.device.device_report_consumer

设计要点：
- 攒批策略：满 DEVICE_REPORT_BATCH_SIZE 条，或距上次写入超过 BATCH_INTERVAL 秒，触发落库
- 同设备（sn+uuid）多条消息只保留最后一条（max_runtime 取最大值、last_report_time 取最新）
- 消费确认：落库成功后才 ack；单批失败拆半重试，避免大批量消息永远卡死
- 连接断开自动重连，保证消费者常驻
"""
import json
import logging
import time
import uuid as uuid_lib
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import pika

import config
from Common import rabbitmq
from database.Postgresql import get_postgres_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("device_report_consumer")

# 消费消息后等待攒批的时间（秒）
_CONSUME_TIMEOUT = 5


def _batch_upsert(messages: List[dict]) -> None:
    """批量 UPSERT：device 表 + device_run_session 表（合并同设备多条）"""
    if not messages:
        return

    # 同设备（sn+uuid）合并：max_runtime 取最大，report_time 取最新
    merged: Dict[str, dict] = {}
    for m in messages:
        key = (m["sn"], m["uuid"])
        if key in merged:
            prev = merged[key]
            merged[key] = {
                "sn": m["sn"],
                "uuid": m["uuid"],
                "runtime": max(prev["runtime"], m["runtime"]),
                "report_time": max(prev["report_time"], m["report_time"]),
            }
        else:
            merged[key] = m

    conn = get_postgres_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                for m in merged.values():
                    report_dt = datetime.fromisoformat(m["report_time"])

                    # UPSERT device（不存在则插入）
                    cur.execute("""
                        INSERT INTO device (sn, created_at)
                        VALUES (%s, NOW())
                        ON CONFLICT (sn)
                        DO UPDATE SET sn = EXCLUDED.sn
                        RETURNING id
                    """, (m["sn"],))
                    device_id = cur.fetchone()[0]

                    # UPSERT device_run_session（max_runtime 只增不减）
                    cur.execute("""
                        INSERT INTO device_run_session
                        (device_id, uuid, first_report_time, last_report_time, max_runtime_seconds, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (device_id, uuid)
                        DO UPDATE SET
                            last_report_time = EXCLUDED.last_report_time,
                            max_runtime_seconds = GREATEST(
                                device_run_session.max_runtime_seconds,
                                EXCLUDED.max_runtime_seconds
                            )
                    """, (device_id, m["uuid"], report_dt, report_dt, m["runtime"], report_dt))

        logger.info(f"批量写入完成: {len(merged)} 条（原始 {len(messages)} 条）")
    except Exception as e:
        logger.error(f"批量写入失败: {e}")
        raise
    finally:
        conn.close()


def _invalidate_cache(sns: List[str]) -> None:
    """批量写入后失效 Redis 缓存（与旧版单条写入逻辑一致）"""
    try:
        import redis
        r = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)
        pipe = r.pipeline()
        # 失效设备列表缓存
        pipe.delete(config.DEVICE_LIST_CACHE_KEY if hasattr(config, "DEVICE_LIST_CACHE_KEY") else "device:list:all")
        # 失效相关设备的历史缓存
        for sn in set(sns):
            pipe.delete(f"device:history:{sn}:*")
        pipe.execute()
    except Exception as e:
        logger.warning(f"Redis 缓存失效失败: {e}")


def _refresh_heartbeats(sns: List[str]) -> None:
    """批量落库后刷新心跳：保证消费者重启后心跳仍然存在（与生产者侧互备）"""
    try:
        from functions.device.device_heartbeat import refresh_heartbeat
        for sn in set(sns):
            refresh_heartbeat(sn)
    except Exception as e:
        logger.warning(f"心跳刷新失败: {e}")


def _callback(ch, method, properties, body):
    """单条消息处理：放入攒批缓冲区，由调度逻辑触发批量写入"""
    try:
        msg = json.loads(body)
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return msg
    except json.JSONDecodeError as e:
        logger.error(f"消息解析失败，丢弃: {e}，body={body[:200]}")
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return None


def consume():
    """主消费循环：攒批 → 定时/定量落库 → 失败半拆重试"""
    channel = rabbitmq.get_channel()
    rabbitmq.declare_queue(config.DEVICE_REPORT_QUEUE, config.DEVICE_REPORT_EXCHANGE, config.DEVICE_REPORT_ROUTING_KEY)

    # 批量消费：每次取最多 BATCH_SIZE 条，等 CONSUME_TIMEOUT 秒或攒满即触发
    buffer: List[dict] = []
    last_flush = time.monotonic()

    def flush(batch: List[dict]) -> bool:
        """尝试落库；失败则将 batch 拆半递归重试，避免整批卡死"""
        if not batch:
            return True
        try:
            _batch_upsert(batch)
            _invalidate_cache([m["sn"] for m in batch])
            _refresh_heartbeats([m["sn"] for m in batch])
            return True
        except Exception:
            if len(batch) > 1:
                mid = len(batch) // 2
                ok1 = flush(batch[:mid])
                ok2 = flush(batch[mid:])
                return ok1 and ok2
            # 单条仍失败：记录并跳过（否则会无限重试死循环）
            logger.error(f"单条消息落库失败，跳过: {batch[0]}")
            return False

    def drain():
        nonlocal buffer, last_flush
        if not buffer:
            return
        pending = buffer
        buffer = []
        last_flush = time.monotonic()
        flush(pending)

    logger.info(f"设备上报消费者启动，队列: {config.DEVICE_REPORT_QUEUE}")

    while True:
        try:
            # 非阻塞消费，配合攒批调度
            method, properties, body = channel.basic_get(config.DEVICE_REPORT_QUEUE, auto_ack=False)
            if method:
                msg = _callback(channel, method, properties, body)
                if msg:
                    buffer.append(msg)
                if len(buffer) >= config.DEVICE_REPORT_BATCH_SIZE:
                    drain()
            else:
                # 队列空：若距上次写入已超过间隔，则把缓冲区中剩余消息落库
                if buffer and (time.monotonic() - last_flush) >= config.DEVICE_REPORT_BATCH_INTERVAL:
                    drain()
                else:
                    time.sleep(_CONSUME_TIMEOUT)
        except (pika.exceptions.AMQPConnectionError, pika.exceptions.ChannelWrongStateError) as e:
            logger.warning(f"RabbitMQ 连接断开: {e}，5 秒后重连...")
            time.sleep(5)
            rabbitmq.reset_connection()
            channel = rabbitmq.get_channel()
        except Exception as e:
            logger.error(f"消费循环异常: {e}")
            time.sleep(5)


if __name__ == "__main__":
    consume()
