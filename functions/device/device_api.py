"""
设备管理 API 模块 - 提供 FastAPI 路由所需的业务函数
所有函数都是同步的，由调用方在线程池中执行
"""
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from database.Postgresql import get_postgres_connection
from Common.Response import create_response
from config import REDIS_URL
from functions.device.device_heartbeat import is_online, get_heartbeat
import redis

DEVICE_LIST_CACHE_KEY = "device:list:all"
DEVICE_LIST_CACHE_TTL = 30
DEVICE_HISTORY_CACHE_PREFIX = "device:history:"
DEVICE_HISTORY_CACHE_TTL = 60

_redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def get_redis():
    return _redis_client


def list_devices() -> Dict:
    """查询所有设备列表"""
    r = _redis_client

    # 尝试从 Redis 缓存获取
    try:
        cached = r.get(DEVICE_LIST_CACHE_KEY)
        if cached:
            data = json.loads(cached)
            data['from_cache'] = True
            return create_response(200, "查询成功（缓存）", True, data)
    except Exception:
        pass

    # 查询数据库
    # 本机是 UTC，DB 存的是 UTC —— 直接按服务器本地时间（UTC）判定与显示
    now_server = datetime.now()
    yesterday = now_server - timedelta(days=1)
    today_start = now_server.replace(hour=0, minute=0, second=0, microsecond=0)

    conn = get_postgres_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT d.id, d.sn, d.created_at,
                       MAX(drs.last_report_time) as last_report
                FROM device d
                LEFT JOIN device_run_session drs ON d.id = drs.device_id
                GROUP BY d.id, d.sn, d.created_at
                ORDER BY d.created_at DESC
            """)
            rows = cur.fetchall()

        devices = []
        online_count = 0
        offline_count = 0
        today_new_count = 0

        def to_local(dt):
            """服务器本地时间（UTC）：DB 存什么就显示什么，不做时区偏移"""
            if dt is None:
                return None
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt

        for row in rows:
            created_at = to_local(row[2])
            last_report = to_local(row[3])
            # 在线判定：Redis 心跳优先（实时），无心跳时回退到 24 小时内有上报（DB 兜底）
            online_heartbeat = is_online(row[1])
            is_online_flag = online_heartbeat if online_heartbeat is not None else (last_report is not None and last_report >= yesterday)
            is_today_new = created_at is not None and created_at >= today_start

            if is_online_flag:
                online_count += 1
            else:
                offline_count += 1
            if is_today_new:
                today_new_count += 1

            devices.append({
                "sn": row[1],
                "created_at": created_at.isoformat() if created_at else None,
                "created_at_local": created_at.strftime("%Y-%m-%d %H:%M:%S") if created_at else None,
                "last_report": last_report.isoformat() if last_report else None,
                "last_report_local": last_report.strftime("%Y-%m-%d %H:%M:%S") if last_report else None,
                "last_heartbeat": get_heartbeat(row[1]),
                "is_online": is_online_flag,
                "is_today_new": is_today_new
            })

        result_data = {
            "total_devices": len(devices),
            "online_devices": online_count,
            "offline_devices": offline_count,
            "today_new_devices": today_new_count,
            "devices": devices
        }

        # 写入缓存
        try:
            r.setex(DEVICE_LIST_CACHE_KEY, DEVICE_LIST_CACHE_TTL, json.dumps(result_data))
        except Exception:
            pass

        return create_response(200, "查询成功", True, result_data)
    finally:
        conn.close()


def query_device_history(sn: str, n: int) -> Dict:
    """查询设备上线历史"""
    r = _redis_client
    cache_key = f"{DEVICE_HISTORY_CACHE_PREFIX}{sn}:{n}"

    # 尝试从缓存获取
    try:
        cached = r.get(cache_key)
        if cached:
            data = json.loads(cached)
            data['from_cache'] = True
            return create_response(200, "查询成功（缓存）", True, data)
    except Exception:
        pass

    conn = get_postgres_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT uuid, first_report_time, last_report_time,
                       max_runtime_seconds, created_at
                FROM device_run_session
                WHERE device_id = (SELECT id FROM device WHERE sn = %s)
                ORDER BY first_report_time DESC
                LIMIT %s
            """, (sn, n))
            rows = cur.fetchall()

        records = []
        for row in rows:
            def format_dt(dt):
                """服务器本地时间（UTC）：DB 存什么就显示什么，不做时区偏移"""
                if dt is None:
                    return None
                if dt.tzinfo is not None:
                    dt = dt.replace(tzinfo=None)
                return dt.strftime("%Y-%m-%d %H:%M:%S")

            records.append({
                "uuid": row[0],
                "start_time": row[1].isoformat() if row[1] else None,
                "end_time": row[2].isoformat() if row[2] else None,
                "max_runtime_seconds": row[3],
                "created_at": row[4].isoformat() if row[4] else None,
                "start_time_local": format_dt(row[1]),
                "end_time_local": format_dt(row[2]),
                "created_at_local": format_dt(row[4]),
            })

        result_data = {
            "device_sn": sn,
            "total_sessions_found": len(records),
            "requested_count": n,
            "records": records
        }

        try:
            r.setex(cache_key, DEVICE_HISTORY_CACHE_TTL, json.dumps(result_data))
        except Exception:
            pass

        return create_response(200, "查询成功", True, result_data)
    finally:
        conn.close()


def save_runtime(sn: str, uuid_val: str, runtime: int) -> Dict:
    """保存设备运行时长（降级兜底路径：MQ 不可用时直写数据库）"""
    now_local = datetime.now()

    conn = get_postgres_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO device (sn, created_at)
                    VALUES (%s, NOW())
                    ON CONFLICT (sn)
                    DO UPDATE SET sn = EXCLUDED.sn
                    RETURNING id
                """, (sn,))
                device_id = cur.fetchone()[0]

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
                    RETURNING first_report_time, last_report_time, max_runtime_seconds
                """, (device_id, uuid_val, now_local, now_local, runtime, now_local))

                row = cur.fetchone()
                db_first_time = row[0].isoformat()
                db_last_time = row[1].isoformat()
                db_max_runtime = row[2]

        # 更新缓存（仅失效列表缓存，心跳由 device_heartbeat 模块管理）
        try:
            r.delete(DEVICE_LIST_CACHE_KEY)
        except Exception:
            pass

        # 刷新心跳，保持与 MQ 路径一致
        from functions.device.device_heartbeat import refresh_heartbeat
        refresh_heartbeat(sn)

        return create_response(200, "上报成功", True, {
            "status": "ok",
            "session_max_runtime": db_max_runtime,
            "session_first_report": db_first_time,
            "session_last_report": db_last_time
        })
    finally:
        conn.close()


def query_device(keyword: str) -> Dict:
    """查询设备信息"""
    conn = get_postgres_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, number, imei, n2n, remarks, metadata, group_id,
                       scheduled_status, module_4g_type, created_at, updated_at
                FROM hawkair_device
                WHERE number = %s OR imei = %s OR n2n = %s
                LIMIT 1
            """, (keyword, keyword, keyword))
            row = cur.fetchone()

            if row:
                columns = [desc[0] for desc in cur.description]
                data = dict(zip(columns, row))
                return create_response(200, "查询成功", True, {
                    "number": data.get('number'),
                    "imei": data.get('imei'),
                    "n2n": data.get('n2n'),
                    "full_info": data
                })
            else:
                return create_response(404, f"未找到匹配的设备（搜索词: {keyword}）", False)
    finally:
        conn.close()
