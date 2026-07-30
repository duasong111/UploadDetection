from flask import request
from flask.views import MethodView
from http import HTTPStatus
from Common.Response import create_response
import psycopg2
from psycopg2 import OperationalError
from config import DB_CONFIG


class QueryDeviceView(MethodView):
    """查询设备信息接口（POST）"""
    
    def post(self):
        try:
            data = request.get_json()
            keyword = data.get('keyword')
            
            if not keyword:
                return create_response(
                    HTTPStatus.BAD_REQUEST,
                    "缺少必要参数：keyword",
                    False
                )
            
            keyword = keyword.strip()
            
            if not keyword:
                return create_response(
                    HTTPStatus.BAD_REQUEST,
                    "keyword 不能为空",
                    False
                )
            
            sql = """
                SELECT id, number, imei, n2n, remarks, metadata, group_id, 
                       scheduled_status, module_4g_type, created_at, updated_at 
                FROM hawkair_device 
                WHERE number = %s OR imei = %s OR n2n = %s 
                LIMIT 1 
            """
            
            conn = None
            cur = None
            try:
                conn = psycopg2.connect(**DB_CONFIG)
                cur = conn.cursor()
                cur.execute(sql, (keyword, keyword, keyword))
                row = cur.fetchone()
                
                if row:
                    columns = [desc[0] for desc in cur.description]
                    data = dict(zip(columns, row))
                    
                    return create_response(
                        HTTPStatus.OK,
                        "查询成功",
                        True,
                        data={
                            "number": data.get('number'),
                            "imei": data.get('imei'),
                            "n2n": data.get('n2n'),
                            "full_info": data
                        }
                    )
                else:
                    return create_response(
                        HTTPStatus.NOT_FOUND,
                        f"未找到匹配的设备（搜索词: {keyword}）",
                        False
                    )
                    
            except OperationalError as e:
                return create_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    f"数据库连接失败: {e}",
                    False
                )
            except Exception as e:
                return create_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    f"查询异常: {e}",
                    False
                )
            finally:
                if cur:
                    cur.close()
                if conn:
                    conn.close()
                    
        except Exception as e:
            return create_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"服务器错误: {str(e)}",
                False
            )