from datetime import datetime
import psycopg2
import psycopg2.extras
from psycopg2.extras import DictCursor
from database.Postgresql import get_postgres_connection
from config import CODE_ERROR, CODE_SUCCESS

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
            return None   # 或者返回 {"error": str(e)}，由上层决定

    def update_user_key_value(self, db_name: str, key_value: str, username: str, new_data, key_type: str):
        """更新单个字段"""
        try:
            if not key_value or not key_type:
                return {"success": False, "message": "key_value 和 key_type 不能为空"}

            conn = get_postgres_connection()
            with conn.cursor() as cur:
                table = self._quote_identifier(db_name)
                where_col = self._quote_identifier(key_value)   # 通常是 'name'
                set_col = self._quote_identifier(key_type)      # 要更新的字段，如 'updated_time'

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