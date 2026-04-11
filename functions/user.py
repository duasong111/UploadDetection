from flask import request
from http import HTTPStatus
import secrets
from datetime import datetime
from Common.Response import create_response
from database.operateFunction import execuFunction
from functions.check import verifyPassword, generate_password_hash

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

            # 生成 token（更安全长度）
            new_token = secrets.token_hex(32)   # 推荐改成32，提高安全性

            # 只更新 updated_time（利用已有字段）
            update_time_result = db_function.update_user_key_value(
                db_name='user',
                username=username,
                key_value='name',
                new_data=datetime.now(),
                key_type='updated_time'   # 改成已有的字段
            )

            if not update_time_result.get('success', False):
                return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, "更新登录时间失败", False)

            return create_response(
                HTTPStatus.OK,
                "登录成功",
                True,
                data={"token": new_token, "username": username}
            )

        except Exception as e:
            return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)


# ==================== 注册类 ====================
class RegisterFunction:
    def register(self, username=None, password=None):
        try:
            if not username or not password:
                return create_response(HTTPStatus.BAD_REQUEST, "用户名和密码为必填项", False)

            # 基础输入校验（强烈建议加上）
            if len(username) < 3 or len(username) > 30:
                return create_response(HTTPStatus.BAD_REQUEST, "用户名长度必须在 3-30 字符之间", False)
            if len(password) < 8:
                return create_response(HTTPStatus.BAD_REQUEST, "密码长度至少 8 位", False)

            db_function = execuFunction()

            # 检查用户名是否已存在
            query_result = db_function.query_individual_users(
                dbName='user', queryParams="name", queryData=username)
            if query_result:
                return create_response(HTTPStatus.BAD_REQUEST, "用户名已存在", False)

            # 生成安全哈希和盐
            hashed, salt = generate_password_hash(password)
            salt_hex = salt.hex()

            # 准备插入数据（只使用表里已有的字段）
            insert_data = [{
                "name": username,
                "password": hashed,
                "salt": salt_hex,
                "avatar_path": None
                # 不传 token 和 last_login
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