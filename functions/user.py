from flask import request
from flask.views import View
from http import HTTPStatus
import secrets
from datetime import datetime, date
from Common.Response import create_response
from database.operateFunction import execuFunction
from functions.check import verifyPassword, generate_password_hash
from database.Postgresql import get_postgres_connection

# ==================== 登录类 ====================
class LoginFunction:
    def checklogin(self, username=None, password=None):
        try:
            if not username or not password:
                return create_response(HTTPStatus.BAD_REQUEST, "用户名和密码为必填项", False)
            db_function = execuFunction()
            # 查询用户
            query_result = db_function.query_individual_users(
                dbName='user', queryParams="name", queryData=username)
            if not query_result:
                return create_response(HTTPStatus.BAD_REQUEST, "用户名或密码错误", False)
            # 验证密码
            stored_password = query_result['password']
            stored_salt = bytes.fromhex(query_result.get('salt', ''))
            if not verifyPassword(password, stored_password, stored_salt):
                return create_response(HTTPStatus.BAD_REQUEST, "用户名或密码错误", False)
            new_token = secrets.token_hex(32)
            update_time_result = db_function.update_user_key_value(
                db_name='user',
                username=username,
                key_value='name',
                new_data=datetime.now(),
                key_type='updated_time'
            )

            if not update_time_result.get('success', False):
                return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, "更新登录时间失败", False)

            return create_response(
                HTTPStatus.OK,
                "登录成功",
                True,
                data={
                    "token": new_token,
                    "username": username,
                    "avatar_path": query_result.get("avatar_path"),
                    "updated_time": query_result.get("updated_time").isoformat() if query_result.get("updated_time") else None,
                    "created_time": query_result.get("created_time").isoformat() if query_result.get("created_time") else None,
                }
            )

        except Exception as e:
            return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)


# ==================== 注册类 ====================
class RegisterFunction:
    def register(self, username=None, password=None):
        try:
            if not username or not password:
                return create_response(HTTPStatus.BAD_REQUEST, "用户名和密码为必填项", False)

            if len(username) < 3 or len(username) > 30:
                return create_response(HTTPStatus.BAD_REQUEST, "用户名长度必须在 3-30 字符之间", False)
            if len(password) < 8:
                return create_response(HTTPStatus.BAD_REQUEST, "密码长度至少 8 位", False)

            db_function = execuFunction()

            query_result = db_function.query_individual_users(
                dbName='user', queryParams="name", queryData=username)
            if query_result:
                return create_response(HTTPStatus.BAD_REQUEST, "用户名已存在", False)

            hashed, salt = generate_password_hash(password)
            salt_hex = salt.hex()

            insert_data = [{
                "name": username,
                "password": hashed,
                "salt": salt_hex,
                "avatar_path": None
            }]

            add_result = db_function.add_data(dbName='user', insertData=insert_data)
            if not add_result.get("success", False):
                return create_response(HTTPStatus.INTERNAL_SERVER_ERROR,
                                     add_result.get("message", "注册失败"), False)

            return create_response(
                HTTPStatus.CREATED,
                "注册成功",
                True,
                data={"username": username}
            )

        except Exception as e:
            return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)


# ==================== 用户贡献统计类 ====================
class UserContributionView:
    """获取用户每日API请求次数贡献统计"""
    def get_contributions(self, username=None, month=None):
        """
        获取用户每日API请求次数贡献统计
        month: 可选，格式 "YYYY-MM"，如 "2026-06"
        """
        try:
            if not username:
                return create_response(HTTPStatus.BAD_REQUEST, "用户名为必填项", False)

            conn = get_postgres_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT login_history
                    FROM "user"
                    WHERE name = %s
                """, (username,))
                row = cur.fetchone()

            if not row or row[0] is None:
                return create_response(
                    HTTPStatus.OK,
                    "查询成功",
                    True,
                    data={"contributions": []}
                )

            login_history = row[0]

            # 过滤指定月份
            if month:
                contributions = [
                    {"date": k, "count": int(v)}
                    for k, v in login_history.items()
                    if k.startswith(month)
                ]
            else:
                contributions = [
                    {"date": k, "count": int(v)}
                    for k, v in login_history.items()
                ]

            # 按日期倒序排列
            contributions.sort(key=lambda x: x["date"], reverse=True)

            return create_response(
                HTTPStatus.OK,
                "查询成功",
                True,
                data={"contributions": contributions}
            )

        except Exception as e:
            return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)
        finally:
            if 'conn' in locals() and conn:
                conn.close()

    @staticmethod
    def increment_request_count(username):
        """增加用户当日API请求次数"""
        try:
            conn = get_postgres_connection()
            today = date.today().isoformat()

            with conn.cursor() as cur:
                # 确保 login_history 字段存在
                cur.execute("""
                    ALTER TABLE "user" ADD COLUMN IF NOT EXISTS login_history JSONB DEFAULT '{}'
                """)
                conn.commit()

                # 使用 JSONB 函数更新请求次数
                cur.execute("""
                    UPDATE "user"
                    SET login_history = COALESCE(login_history, '{}'::jsonb) ||
                        jsonb_build_object(%s, COALESCE((login_history->>%s)::int, 0) + 1)
                    WHERE name = %s
                """, (today, today, username))
                conn.commit()
        except Exception as e:
            print(f"更新请求次数失败: {str(e)}")
        finally:
            if 'conn' in locals() and conn:
                conn.close()

    @staticmethod
    def get_ai_usage_count(username):
        """获取用户当日AI使用次数"""
        try:
            conn = get_postgres_connection()
            today = date.today().isoformat()

            with conn.cursor() as cur:
                cur.execute("""
                    SELECT login_history->>%s
                    FROM "user"
                    WHERE name = %s
                """, (today, username))
                row = cur.fetchone()

            if row and row[0]:
                return int(row[0])
            return 0
        except Exception as e:
            print(f"获取AI使用次数失败: {str(e)}")
            return 0
        finally:
            if 'conn' in locals() and conn:
                conn.close()

    @staticmethod
    def increment_ai_usage_count(username):
        """增加用户当日AI使用次数"""
        try:
            conn = get_postgres_connection()
            today = date.today().isoformat()

            with conn.cursor() as cur:
                # 使用 JSONB 函数更新 AI 使用次数（存储在 login_history 的 ai_chat 子节点）
                cur.execute("""
                    UPDATE "user"
                    SET login_history = COALESCE(login_history, '{}'::jsonb) ||
                        jsonb_build_object(
                            'ai_chat', jsonb_build_object(
                                %s, COALESCE((login_history->'ai_chat'->>%s)::int, 0) + 1
                            )
                        )
                    WHERE name = %s
                """, (today, today, username))
                conn.commit()
        except Exception as e:
            print(f"更新AI使用次数失败: {str(e)}")
        finally:
            if 'conn' in locals() and conn:
                conn.close()


# ==================== 修改密码类 ====================
class ChangePasswordView(View):
    """修改用户密码"""

    def dispatch_request(self):
        try:
            data = request.get_json()
            username = data.get('username')
            old_password = data.get('old_password')
            new_password = data.get('new_password')

            if not username or not old_password or not new_password:
                return create_response(HTTPStatus.BAD_REQUEST, "用户名、旧密码和新密码为必填项", False)

            if len(new_password) < 8:
                return create_response(HTTPStatus.BAD_REQUEST, "新密码长度至少 8 位", False)

            db_function = execuFunction()

            # 查询用户
            query_result = db_function.query_individual_users(
                dbName='user', queryParams="name", queryData=username)
            if not query_result:
                return create_response(HTTPStatus.BAD_REQUEST, "用户不存在", False)

            # 验证旧密码
            stored_password = query_result['password']
            stored_salt = bytes.fromhex(query_result.get('salt', ''))
            if not verifyPassword(old_password, stored_password, stored_salt):
                return create_response(HTTPStatus.BAD_REQUEST, "旧密码错误", False)

            # 生成新密码哈希
            hashed, salt = generate_password_hash(new_password)
            salt_hex = salt.hex()

            # 更新密码
            db_function.update_user_key_value(
                db_name='user',
                key_value='name',
                username=username,
                new_data=hashed,
                key_type='password'
            )
            # 更新盐值
            db_function.update_user_key_value(
                db_name='user',
                key_value='name',
                username=username,
                new_data=salt_hex,
                key_type='salt'
            )

            return create_response(
                HTTPStatus.OK,
                "密码修改成功",
                True
            )

        except Exception as e:
            return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)