"""
设备在线状态 - Redis 心跳模块

设备上报时刷新心跳（TTL 90 秒），提供毫秒级在线判定，
与 RabbitMQ 批量落库解耦：即使 DB 延迟 10 秒，在线状态依然实时。
"""
import json
import time

import redis

from config import REDIS_URL

# 心跳过期时间（秒）：设备 60 秒上报一次，3 倍余量判离线
HEARTBEAT_TTL = 90

# 在线状态缓存键
DEVICE_ONLINE_KEY = "device:online:sn"     # hash: sn -> 最后心跳时间（unix 时间戳）
DEVICE_ONLINE_SET = "device:online:sn:set"  # set: 当前在线 sn 集合

_redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def refresh_heartbeat(sn: str) -> None:
    """设备上报时刷新心跳（上报接口和批量落库后都会调用）"""
    try:
        now = time.time()
        pipe = _redis_client.pipeline()
        # 记录心跳时间 + 加入在线集合（带 TTL，设备失联后自动从集合消失）
        pipe.hset(DEVICE_ONLINE_KEY, sn, str(now))
        pipe.sadd(DEVICE_ONLINE_SET, sn)
        pipe.expire(DEVICE_ONLINE_KEY, HEARTBEAT_TTL)
        pipe.expire(DEVICE_ONLINE_SET, HEARTBEAT_TTL)
        pipe.execute()
    except Exception as e:
        print(f"[heartbeat] 刷新心跳失败: {e}")


def is_online(sn: str) -> bool:
    """判断设备是否在线（毫秒级）"""
    try:
        # 优先走集合（O(1)），TTL 过期自动剔除，无需比对时间戳
        return bool(_redis_client.sismember(DEVICE_ONLINE_SET, sn))
    except Exception:
        return False


def get_online_sns() -> list:
    """获取所有在线设备 SN 列表"""
    try:
        return list(_redis_client.smembers(DEVICE_ONLINE_SET))
    except Exception:
        return []


def get_heartbeat(sn: str) -> float:
    """获取设备最后心跳时间（unix 时间戳）"""
    try:
        val = _redis_client.hget(DEVICE_ONLINE_KEY, sn)
        return float(val) if val else 0.0
    except Exception:
        return 0.0


def remove_offline(sn: str) -> None:
    """设备下线时手动移除心跳"""
    try:
        pipe = _redis_client.pipeline()
        pipe.hdel(DEVICE_ONLINE_KEY, sn)
        pipe.srem(DEVICE_ONLINE_SET, sn)
        pipe.execute()
    except Exception:
        pass
