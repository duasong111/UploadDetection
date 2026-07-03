from datetime import datetime, timedelta
import psycopg2
import psycopg2.extras
from psycopg2.extras import DictCursor
from database.Postgresql import get_postgres_connection
from config import CODE_ERROR, CODE_SUCCESS

# 北京时间比 UTC 快 8 小时
BEIJING_OFFSET = timedelta(hours=8)


def to_local_naive(dt):
    """将 datetime 转为北京时间显示（UTC + 8小时）"""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    dt = dt + BEIJING_OFFSET
    return dt


def format_datetime(dt):
    """将 datetime 转为格式化字符串"""
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class execuFunction():
    def _quote_identifier(self, identifier: str) -> str:
        """安全地给表名或列名加双引号"""
        if not identifier:
            raise ValueError("标识符不能为空")
        return f'"{identifier.replace("\"", "\"\"")}"'

    def add_data(self, dbName: str, insertData: list[dict]):
        """通用插入"""
        if not insertData:
            return {"success": True, "message": "无数据插入", "inserted_count": 0}

        try:
            conn = get_postgres_connection()
            with conn.cursor() as cur:
                table = self._quote_identifier(dbName)
                columns = list(insertData[0].keys())
                quoted_columns = [self._quote_identifier(col) for col in columns]

                sql = f"INSERT INTO {table} ({', '.join(quoted_columns)}) VALUES %s"
                values = [tuple(d.get(col) for col in columns) for d in insertData]

                psycopg2.extras.execute_values(cur, sql, values)
                conn.commit()
                return {
                    "success": True,
                    "message": "数据添加成功",
                    "inserted_count": len(insertData)
                }
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return {"success": False, "message": str(e)}

    def query_individual_users(self, dbName: str, queryParams: str, queryData):
        """查询单个用户"""
        try:
            conn = get_postgres_connection()
            with conn.cursor(cursor_factory=DictCursor) as cur:
                table = self._quote_identifier(dbName)
                column = self._quote_identifier(queryParams)
                sql = f"SELECT * FROM {table} WHERE {column} = %s LIMIT 1"
                cur.execute(sql, (queryData,))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            return None

    def update_user_key_value(self, db_name: str, key_value: str, username: str, new_data, key_type: str):
        """更新单个字段"""
        try:
            if not key_value or not key_type:
                return {"success": False, "message": "key_value 和 key_type 不能为空"}

            conn = get_postgres_connection()
            with conn.cursor() as cur:
                table = self._quote_identifier(db_name)
                where_col = self._quote_identifier(key_value)
                set_col = self._quote_identifier(key_type)

                sql = f"UPDATE {table} SET {set_col} = %s WHERE {where_col} = %s"
                cur.execute(sql, (new_data, username))
                conn.commit()
                affected = cur.rowcount
                return {
                    "success": affected > 0,
                    "message": f"{key_type} 更新成功" if affected > 0 else f"未找到用户 {username}"
                }
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return {"success": False, "message": f"更新失败: {str(e)}"}

    # ==================== 设备相关查询 ====================

    def get_all_devices(self):
        """
        获取所有设备列表及状态
        :return: {
            "total": int,
            "online": int,
            "offline": int,
            "devices": [dict]
        }
        """
        try:
            conn = get_postgres_connection()
            now_server = datetime.now()
            yesterday = now_server - timedelta(days=1)

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

            for row in rows:
                sn = row[1]
                created_at = to_local_naive(row[2])
                last_report = to_local_naive(row[3])
                is_online = last_report is not None and last_report >= yesterday

                if is_online:
                    online_count += 1

                devices.append({
                    "sn": sn,
                    "created_at": format_datetime(created_at),
                    "last_report": format_datetime(last_report),
                    "is_online": is_online
                })

            conn.close()
            return {
                "total": len(devices),
                "online": online_count,
                "offline": len(devices) - online_count,
                "devices": devices[:50]
            }
        except Exception as e:
            return {"error": str(e)}

    def get_device_by_sn(self, device_sn: str):
        """
        根据序列号查询设备
        :param device_sn: 设备序列号
        :return: dict or None
        """
        try:
            conn = get_postgres_connection()
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute("""
                    SELECT d.id, d.sn, d.created_at,
                           MAX(drs.last_report_time) as last_report,
                           COUNT(drs.id) as session_count
                    FROM device d
                    LEFT JOIN device_run_session drs ON d.id = drs.device_id
                    WHERE d.sn = %s
                    GROUP BY d.id, d.sn, d.created_at
                """, (device_sn,))
                row = cur.fetchone()

            if not row:
                conn.close()
                return None

            conn.close()
            return dict(row)
        except Exception as e:
            return None

    def get_device_status(self, device_sn: str):
        """
        获取设备详细状态
        :param device_sn: 设备序列号
        :return: dict
        """
        try:
            conn = get_postgres_connection()
            now_server = datetime.now()
            yesterday = now_server - timedelta(days=1)

            # 获取设备信息
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute("""
                    SELECT d.id, d.sn, d.created_at,
                           MAX(drs.last_report_time) as last_report,
                           COUNT(drs.id) as session_count
                    FROM device d
                    LEFT JOIN device_run_session drs ON d.id = drs.device_id
                    WHERE d.sn = %s
                    GROUP BY d.id, d.sn, d.created_at
                """, (device_sn,))
                row = cur.fetchone()

            if not row:
                conn.close()
                return {"error": f"设备 {device_sn} 不存在"}

            device_data = dict(row)
            device_id = device_data['id']

            created_at = to_local_naive(device_data['created_at'])
            last_report = to_local_naive(device_data['last_report'])
            is_online = last_report is not None and last_report >= yesterday

            # 获取最近一次运行时长
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT max_runtime_seconds, first_report_time, last_report_time
                    FROM device_run_session
                    WHERE device_id = %s
                    ORDER BY last_report_time DESC
                    LIMIT 1
                """, (device_id,))
                last_session = cur.fetchone()

            last_runtime = None
            if last_session:
                last_runtime = {
                    "max_runtime_seconds": last_session[0],
                    "first_report_time": format_datetime(to_local_naive(last_session[1])),
                    "last_report_time": format_datetime(to_local_naive(last_session[2]))
                }

            conn.close()
            return {
                "sn": device_sn,
                "created_at": format_datetime(created_at),
                "last_report": format_datetime(last_report),
                "is_online": is_online,
                "total_sessions": device_data.get('session_count') or 0,
                "last_session": last_runtime
            }
        except Exception as e:
            return {"error": str(e)}

    def get_device_history(self, device_sn: str, limit: int = 10):
        """
        获取设备运行历史
        :param device_sn: 设备序列号
        :param limit: 返回记录数
        :return: [dict]
        """
        try:
            conn = get_postgres_connection()

            # 获取设备ID
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM device WHERE sn = %s", (device_sn,))
                row = cur.fetchone()

            if not row:
                conn.close()
                return {"error": f"设备 {device_sn} 不存在"}

            device_id = row[0]

            # 获取历史记录
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT uuid, first_report_time, last_report_time,
                           max_runtime_seconds, created_at
                    FROM device_run_session
                    WHERE device_id = %s
                    ORDER BY first_report_time DESC
                    LIMIT %s
                """, (device_id, limit))
                rows = cur.fetchall()

            records = []
            for row in rows:
                records.append({
                    "uuid": row[0],
                    "first_report_time": format_datetime(to_local_naive(row[1])),
                    "last_report_time": format_datetime(to_local_naive(row[2])),
                    "max_runtime_seconds": row[3],
                    "created_at": format_datetime(to_local_naive(row[4]))
                })

            conn.close()
            return {
                "device_sn": device_sn,
                "total": len(records),
                "records": records
            }
        except Exception as e:
            return {"error": str(e)}

    def get_device_statistics(self):
        """
        获取设备统计信息
        :return: dict
        """
        try:
            conn = get_postgres_connection()
            now_server = datetime.now()
            today_start = now_server.replace(hour=0, minute=0, second=0, microsecond=0)

            with conn.cursor() as cur:
                # 总设备数
                cur.execute("SELECT COUNT(*) FROM device")
                total = cur.fetchone()[0]

                # 在线设备数
                cur.execute("""
                    SELECT COUNT(DISTINCT d.id)
                    FROM device d
                    INNER JOIN device_run_session drs ON d.id = drs.device_id
                    WHERE drs.last_report_time >= %s
                """, (now_server - timedelta(days=1),))
                online = cur.fetchone()[0]

                # 今日新增设备
                cur.execute("SELECT COUNT(*) FROM device WHERE created_at >= %s", (today_start,))
                today_new = cur.fetchone()[0]

                # 今日运行次数
                cur.execute("SELECT COUNT(*) FROM device_run_session WHERE created_at >= %s", (today_start,))
                today_runs = cur.fetchone()[0]

            conn.close()
            return {
                "total_devices": total,
                "online_devices": online,
                "offline_devices": total - online,
                "today_new_devices": today_new,
                "today_runs": today_runs
            }
        except Exception as e:
            return {"error": str(e)}
