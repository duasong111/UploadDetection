"""
RabbitMQ 连接管理 - 基于 pika 的阻塞连接封装

pika 的 BlockingConnection 不是线程安全的，而本项目设备上报走 ThreadPoolExecutor
（20 线程并发），因此采用「线程本地连接」：每个线程懒创建、持有自己的连接与 channel，
彻底避免跨线程共享导致 "Channel is closed / Transport indicated EOF" 竞态问题。

生产/消费者各自复用本模块；消费者进程是单线程，行为与之前一致。
"""
import logging
import threading

import pika

from config import RabbitMQ_HOST, RabbitMQ_PORT, RabbitMQ_USERNAME, RabbitMQ_PASSWORD, RabbitMQ_VHOST

logger = logging.getLogger(__name__)

# 连接参数
_PARAMS = pika.ConnectionParameters(
    host=RabbitMQ_HOST,
    port=RabbitMQ_PORT,
    virtual_host=RabbitMQ_VHOST,
    credentials=pika.PlainCredentials(RabbitMQ_USERNAME, RabbitMQ_PASSWORD),
    heartbeat=60,
    blocked_connection_timeout=30,
)

# 线程本地存储：每个线程独立的连接/channel/已声明队列
_thread_local = threading.local()

# 全局连接注册表（应用退出时统一关闭）
_conns_lock = threading.Lock()
_all_conns = set()


def _register(conn) -> None:
    with _conns_lock:
        _all_conns.add(conn)


def get_connection() -> pika.BlockingConnection:
    """获取当前线程的 RabbitMQ 连接（懒创建，断线自动重建）"""
    conn = getattr(_thread_local, "conn", None)
    if conn is None or conn.is_closed:
        conn = pika.BlockingConnection(_PARAMS)
        _thread_local.conn = conn
        _thread_local.channel = None
        _register(conn)
        logger.debug("线程 %s 创建 RabbitMQ 连接", threading.current_thread().name)
    return conn


def get_channel() -> pika.channel.Channel:
    """获取当前线程的 channel（连接或 channel 失效时自动重建）"""
    get_connection()
    ch = getattr(_thread_local, "channel", None)
    if ch is None or ch.is_closed:
        ch = _thread_local.conn.channel()
        _thread_local.channel = ch
    return ch


def reset_connection() -> None:
    """强制丢弃当前线程的连接（连接级错误后调用，下次 get 时重建全新连接）"""
    conn = getattr(_thread_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    _thread_local.conn = None
    _thread_local.channel = None
    _thread_local.declared = set()


def declare_queue(queue: str, exchange: str = "", routing_key: str = "", durable: bool = True) -> None:
    """声明交换机 + 队列 + 绑定（幂等，重复调用安全）"""
    ch = get_channel()
    if exchange:
        ch.exchange_declare(exchange=exchange, exchange_type="direct", durable=durable)
    ch.queue_declare(queue=queue, durable=durable)
    if exchange and routing_key:
        ch.queue_bind(queue=queue, exchange=exchange, routing_key=routing_key)


def ensure_declared(queue: str, exchange: str = "", routing_key: str = "") -> None:
    """确保当前线程已声明过该队列（每个线程只声明一次，避免高频重复声明）"""
    declared = getattr(_thread_local, "declared", set())
    key = (queue, exchange, routing_key)
    if key in declared:
        return
    declare_queue(queue, exchange, routing_key)
    declared.add(key)
    _thread_local.declared = declared


def close() -> None:
    """关闭所有线程的连接（应用退出时调用）"""
    with _conns_lock:
        conns = list(_all_conns)
        _all_conns.clear()
    for conn in conns:
        try:
            conn.close()
        except Exception:
            pass
    for attr in ("conn", "channel", "declared"):
        try:
            if hasattr(_thread_local, attr):
                delattr(_thread_local, attr)
        except Exception:
            pass
