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
            from datetime import datetime, timedelta
            db_function = execuFunction()
            conn = None

            from database.Postgresql import get_postgres_connection
            conn = get_postgres_connection()

            # 获取24小时前的时间戳
            yesterday = (datetime.now() - timedelta(days=1))
            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

            with conn.cursor() as cur:
                # 查询设备及其最新上报时间
                cur.execute("""
                    SELECT d.id, d.sn, d.created_at,
                           MAX(drs.last_report_time) as last_report
                    FROM device d
                    LEFT JOIN device_run_session drs ON d.id = drs.device_id
                    GROUP BY d.id, d.sn, d.created_at
                    ORDER BY d.created_at DESC
                """)
                rows = cur.fetchall()

            data = []
            online_count = 0
            offline_count = 0
            today_new_count = 0

            for row in rows:
                device_id = row[0]
                sn = row[1]
                created_at = row[2]
                last_report = row[3]

                # 判断是否在线：24小时内有上报记录
                is_online = last_report is not None and last_report >= yesterday

                # 今日新增：创建时间在今天0点之后
                is_today_new = created_at is not None and created_at >= today_start

                if is_online:
                    online_count += 1
                else:
                    offline_count += 1

                if is_today_new:
                    today_new_count += 1

                data.append({
                    "sn": sn,
                    "created_at": created_at.isoformat() if created_at else None,
                    "created_at_local": created_at.strftime("%Y-%m-%d %H:%M:%S") if created_at else None,
                    "last_report": last_report.isoformat() if last_report else None,
                    "last_report_local": last_report.strftime("%Y-%m-%d %H:%M:%S") if last_report else None,
                    "is_online": is_online,
                    "is_today_new": is_today_new
                })

            return create_response(
                HTTPStatus.OK,
                "查询成功",
                True,
                data={
                    "total_devices": len(data),
                    "online_devices": online_count,
                    "offline_devices": offline_count,
                    "today_new_devices": today_new_count,
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
    """设备运行时长上报接口（POST）-- 总是写入数据库，Redis作为缓存"""
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

            now_dt = datetime.now()
            now = now_dt.isoformat()
            key = f"runtime:{sn}:{uuid_val}"

            # ==============================
            # 1️⃣ 优先写入数据库（保证数据持久化）
            # ==============================
            try:
                conn = get_postgres_connection()

                with conn:
                    with conn.cursor() as cur:

                        # UPSERT device
                        cur.execute("""
                            INSERT INTO device (sn, created_at)
                            VALUES (%s, NOW())
                            ON CONFLICT (sn)
                            DO UPDATE SET sn = EXCLUDED.sn
                            RETURNING id
                        """, (sn,))
                        device_id = cur.fetchone()[0]

                        # UPSERT session
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
                        db_first_time = row[0].isoformat()
                        db_last_time = row[1].isoformat()
                        db_max_runtime = row[2]

            except Exception as db_error:
                return create_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    f"数据库写入失败: {str(db_error)}",
                    False
                )

            # ==============================
            # 2️⃣ 更新 Redis 缓存（提升读取性能）
            # ==============================
            try:
                cache_data = {
                    "max_runtime": db_max_runtime,
                    "first_report_time": db_first_time,
                    "last_report_time": now
                }

                pipe = r.pipeline()
                pipe.set(key, json.dumps(cache_data))
                pipe.expire(key, 86400)  # 1天过期
                pipe.execute()

            except Exception:
                # Redis失败不影响主流程
                pass

            # ==============================
            # 返回响应
            # ==============================
            return create_response(
                HTTPStatus.OK,
                "上报成功",
                True,
                data={
                    "status": "ok",
                    "session_max_runtime": db_max_runtime,
                    "session_first_report": db_first_time,
                    "session_last_report": db_last_time
                }
            )

        except Exception as e:
            return create_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"服务器错误: {str(e)}",
                False
            )
