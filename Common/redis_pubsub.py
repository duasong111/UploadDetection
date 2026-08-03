"""
Redis 发布订阅辅助模块 - 轻量封装

统一管理 Redis 发布 / 订阅连接，供 AI 流式转发、配置任务进度推送等场景使用。

设计要点：
- 发布端：复用普通的 Redis 连接（`publish` 是同步非阻塞的，生产后可立即复用）
- 订阅端：异步监听采用独立连接（pub/sub 模式下该连接不能执行其它命令），
  组件退出前需 `close()` 释放，避免连接泄漏。
- 消息负载为 JSON 字符串（与既有流式事件约定一致）。
"""
import json
import threading

import redis

from config import REDIS_URL


def get_redis() -> redis.Redis:
    """获取发布 / 直连用 Redis 连接（普通连接，可复用其它命令）"""
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


def publish(channel: str, event: str, data: dict | None = None) -> None:
    """发布一条 JSON 事件到频道（消费者进程 / 后台任务侧调用）"""
    try:
        payload = json.dumps({"event": event, "data": data or {}}, ensure_ascii=False)
        get_redis().publish(channel, payload)
    except Exception as e:
        print(f"[redis_pubsub] 发布到 {channel} 失败: {e}")


class PubSubListener:
    """订阅监听器：后台线程阻塞订阅，事件投递到 asyncio.Queue（app 进程侧调用）

    用法：
        listener = PubSubListener(channel)
        try:
            events = await listener.get_events(timeout=2)  # 返回最近收到的 [event, data] 列表
        finally:
            listener.close()
    """

    def __init__(self, channel: str):
        self._channel = channel
        self._client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self._pubsub = self._client.pubsub()
        self._pubsub.subscribe(channel)
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        """后台订阅循环：监听频道，将消息存入 Python asyncio.Queue 不适用，
        改用线程安全的 list + Event 唤醒（asyncio 侧用 await asyncio.to_thread 轮询即可）。
        """
        self._queue: list[tuple] = []
        self._queue_ready = threading.Event()
        try:
            for msg in self._pubsub.listen():
                if self._stop_event.is_set():
                    break
                if msg["type"] == "message":
                    try:
                        payload = json.loads(msg["data"])
                        self._queue.append((payload.get("event"), payload.get("data", {})))
                        self._queue_ready.set()
                    except (json.JSONDecodeError, TypeError):
                        continue
        except Exception as e:
            print(f"[redis_pubsub] 订阅 {self._channel} 异常: {e}")

    def get_events(self, timeout: float = 2.0) -> list[tuple]:
        """非阻塞 / 短超时取出已收到的所有事件（逐一弹出）"""
        # 等待新事件；为空则阻塞 timeout 秒后返回
        self._queue_ready.wait(timeout)
        self._queue_ready.clear()
        events = self._queue[:]
        self._queue.clear()
        return events

    def close(self) -> None:
        self._stop_event.set()
        try:
            self._pubsub.unsubscribe(self._channel)
            self._pubsub.close()
        except Exception:
            pass
        try:
            self._client.close()
        except Exception:
            pass
