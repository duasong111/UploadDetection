import redis
import json
from flask import request, jsonify
from flask.views import MethodView
from http import HTTPStatus
from Common.Response import create_response
from database.operateFunction import execuFunction
from datetime import datetime
from config import REDIS_URL
from database.Postgresql import get_postgres_connection
r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

class ListDevicesView(MethodView):
    """查询所有设备列表（GET）"""

    def get(self):
        try:
            db_function = execuFunction()
            conn = None

            from database.Postgresql import get_postgres_connection
            conn = get_postgres_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT sn, created_at 
                    FROM device 
                    ORDER BY created_at DESC
                """)
                rows = cur.fetchall()

            data = []
            for row in rows:
                created_at = row[1]
                data.append({
                    "sn": row[0],
                    "created_at": created_at.isoformat() if created_at else None,
                    "created_at_local": created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S") if created_at else None
                })

            return create_response(
                HTTPStatus.OK,
                "查询成功",
                True,
                data={
                    "total_devices": len(data),
                    "devices": data
                }
            )

        except Exception as e:
            return create_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"服务器错误: {str(e)}",
                False
            )
        finally:
            if 'conn' in locals() and conn:
                conn.close()


class QueryDeviceOnlineHistoryView(MethodView):
    """查询设备上线历史（POST）"""
    def post(self):
        try:
            data = request.get_json() or {}
            sn = data.get("device_sn")
            n_str = data.get("number")

            if not sn:
                return create_response(HTTPStatus.BAD_REQUEST, "缺少设备序列号 sn", False)

            if not n_str:
                return create_response(HTTPStatus.BAD_REQUEST, "缺少返回条数 number", False)

            try:
                n = int(n_str)
                if n <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                return create_response(HTTPStatus.BAD_REQUEST, "number 必须为正整数", False)

            db_function = execuFunction()

            device = db_function.query_individual_users(
                dbName='device', queryParams="sn", queryData=sn)
            if not device:
                return create_response(
                    HTTPStatus.NOT_FOUND,
                    f"未找到序列号为 {sn} 的设备",
                    False
                )

            from database.Postgresql import get_postgres_connection
            conn = get_postgres_connection()
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
                records.append({
                    "uuid": row[0],
                    "start_time": row[1].isoformat() if row[1] else None,
                    "end_time": row[2].isoformat() if row[2] else None,
                    "max_runtime_seconds": row[3],
                    "created_at": row[4].isoformat() if row[4] else None,
                    "start_time_local": row[1].astimezone().strftime("%Y-%m-%d %H:%M:%S") if row[1] else None,
                    "end_time_local": row[2].astimezone().strftime("%Y-%m-%d %H:%M:%S") if row[2] else None,
                    "created_at_local": row[4].astimezone().strftime("%Y-%m-%d %H:%M:%S") if row[4] else None,
                })

            return create_response(
                HTTPStatus.OK,
                "查询成功",
                True,
                data={
                    "device_sn": sn,
                    "total_sessions_found": len(records),
                    "requested_count": n,
                    "records": records
                }
            )

        except Exception as e:
            return create_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"服务器错误: {str(e)}",
                False
            )
        finally:
            if 'conn' in locals() and conn:
                conn.close()


class StaticRunTimeView(MethodView):
    """设备运行时长上报接口（POST）-- 使用redis 进行缓存"""
    def post(self):
        try:
            data = request.get_json(silent=True) or {}

            sn = data.get("sn")
            uuid_val = data.get("uuid")
            runtime = data.get("runtime")

            # 参数校验
            if not sn or not uuid_val or runtime is None:
                return create_response(
                    HTTPStatus.BAD_REQUEST,
                    "缺少必要参数：sn, uuid, runtime",
                    False
                )

            try:
                runtime = int(runtime)
                if runtime < 0:
                    raise ValueError
            except (ValueError, TypeError):
                return create_response(
                    HTTPStatus.BAD_REQUEST,
                    "runtime 必须为非负整数",
                    False
                )

            now = datetime.now().isoformat()
            key = f"runtime:{sn}:{uuid_val}"

            # ==============================
            # 🚀 优先写 Redis（高性能路径）
            # ==============================
            try:
                old_data = r.get(key)

                if old_data:
                    old_data = json.loads(old_data)

                    max_runtime = max(old_data["max_runtime"], runtime)

                    new_data = {
                        "max_runtime": max_runtime,
                        "first_report_time": old_data["first_report_time"],
                        "last_report_time": now
                    }
                else:
                    new_data = {
                        "max_runtime": runtime,
                        "first_report_time": now,
                        "last_report_time": now
                    }

                # pipeline 提升性能
                pipe = r.pipeline()
                pipe.set(key, json.dumps(new_data))
                pipe.expire(key, 86400)  # 1天过期（防脏数据）
                pipe.execute()

                return create_response(
                    HTTPStatus.OK,
                    "上报成功（缓存）",
                    True,
                    data=new_data
                )

            except Exception as redis_error:
                # ==============================
                # ⚠️ Redis挂了 → 兜底写数据库
                # ==============================
                try:
                    conn = get_postgres_connection()

                    with conn:
                        with conn.cursor() as cur:

                            # 1️⃣ UPSERT device
                            cur.execute("""
                                INSERT INTO device (sn, created_at)
                                VALUES (%s, NOW())
                                ON CONFLICT (sn)
                                DO UPDATE SET sn = EXCLUDED.sn
                                RETURNING id
                            """, (sn,))
                            device_id = cur.fetchone()[0]

                            now_dt = datetime.now()

                            # 2️⃣ UPSERT session（核心优化）
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
                                RETURNING 
                                    first_report_time,
                                    last_report_time,
                                    max_runtime_seconds
                            """, (device_id, uuid_val, now_dt, now_dt, runtime, now_dt))

                            row = cur.fetchone()

                    return create_response(
                        HTTPStatus.OK,
                        "上报成功（数据库兜底）",
                        True,
                        data={
                            "status": "ok",
                            "session_max_runtime": row[2],
                            "session_first_report": row[0].isoformat(),
                            "session_last_report": row[1].isoformat()
                        }
                    )

                except Exception as db_error:
                    return create_response(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        f"Redis失败且数据库写入失败: {str(db_error)}",
                        False
                    )

        except Exception as e:
            return create_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"服务器错误: {str(e)}",
                False
            )
