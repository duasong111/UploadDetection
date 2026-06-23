import os
import uuid
from datetime import datetime
from io import BytesIO
from flask import send_file, Response
from minio import Minio
from minio.error import S3Error
from http import HTTPStatus
import redis
import json

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    BUCKET_IP, BUCKET_PORT, RUSTFS_BUCKET_NAME, RUSTFS_SECRET,
    AVATAR_BUCKET_NAME, REDIS_URL
)
from Common.Response import create_response
from database.operateFunction import execuFunction

# Redis 缓存客户端
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
AVATAR_CACHE_TTL = 3600  # 头像缓存过期时间（秒），默认 1 小时
AVATAR_CACHE_PREFIX = "avatar:"  # 缓存 key 前缀


# ==================== 头像管理类 ====================
class AvatarManager:
    """用户头像管理：上传、获取、删除"""

    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB

    def __init__(self):
        self.client = Minio(
            f"{BUCKET_IP}:{BUCKET_PORT}",
            access_key=RUSTFS_BUCKET_NAME,
            secret_key=RUSTFS_SECRET,
            secure=False
        )
        self.bucket_name = AVATAR_BUCKET_NAME
        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self):
        """确保头像桶存在，不存在则创建"""
        try:
            if not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name)
        except S3Error:
            pass

    def _get_avatar_cache(self, filename):
        """从 Redis 获取头像缓存"""
        try:
            cache_key = f"{AVATAR_CACHE_PREFIX}{filename}"
            cached = redis_client.get(cache_key)
            if cached:
                return json.loads(cached)
            return None
        except Exception as e:
            print(f"获取头像缓存失败: {e}")
            return None

    def _set_avatar_cache(self, filename, file_content, content_type):
        """设置头像 Redis 缓存"""
        try:
            cache_key = f"{AVATAR_CACHE_PREFIX}{filename}"
            cache_data = {
                "content": file_content.decode('latin-1'),  # 存储二进制数据
                "content_type": content_type
            }
            redis_client.setex(cache_key, AVATAR_CACHE_TTL, json.dumps(cache_data))
        except Exception as e:
            print(f"设置头像缓存失败: {e}")

    def _invalidate_avatar_cache(self, username):
        """清除用户头像缓存"""
        try:
            # 获取用户的头像文件名
            db_function = execuFunction()
            user_info = db_function.query_individual_users(
                dbName='user', queryParams="name", queryData=username)
            if user_info and user_info.get('avatar_path'):
                cache_key = f"{AVATAR_CACHE_PREFIX}{user_info['avatar_path']}"
                redis_client.delete(cache_key)
        except Exception as e:
            print(f"清除头像缓存失败: {e}")

    def _allowed_file(self, filename):
        """检查文件扩展名是否允许"""
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in self.ALLOWED_EXTENSIONS

    def _generate_filename(self, original_filename):
        """生成唯一文件名，保留原始扩展名"""
        ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else 'jpg'
        unique_id = uuid.uuid4().hex
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        # 直接使用文件名，不带 avatars/ 前缀，因为 bucket 本身就是 avatar
        return f"{timestamp}_{unique_id}.{ext}"

    def upload_avatar(self, username, file):
        """上传用户头像"""
        try:
            if not username:
                return create_response(HTTPStatus.BAD_REQUEST, "用户名为必填项", False)

            if not file:
                return create_response(HTTPStatus.BAD_REQUEST, "文件为必填项", False)

            if file.content_length and file.content_length > self.MAX_FILE_SIZE:
                return create_response(HTTPStatus.BAD_REQUEST, "文件大小不能超过 20MB", False)

            if not self._allowed_file(file.filename):
                return create_response(
                    HTTPStatus.BAD_REQUEST,
                    f"不支持的文件格式，仅支持: {', '.join(self.ALLOWED_EXTENSIONS)}",
                    False
                )

            filename = self._generate_filename(file.filename)
            file_content = file.read()

            # 上传到 RUSTFS
            self.client.put_object(
                self.bucket_name,
                filename,
                BytesIO(file_content),
                length=len(file_content),
                content_type=f"image/{filename.rsplit('.', 1)[1].lower()}"
            )

            # 更新用户数据库中的 avatar_path 字段
            db_function = execuFunction()
            db_function.update_user_key_value(
                db_name='user',
                key_value='name',
                username=username,
                new_data=filename,
                key_type='avatar_path'
            )

            avatar_url = filename  # 只返回文件名，前端会拼接完整URL

            # 清除该用户的头像缓存
            self._invalidate_avatar_cache(username)

            return create_response(
                HTTPStatus.OK,
                "头像上传成功",
                True,
                data={"avatar_url": avatar_url, "filename": filename}
            )

        except S3Error as e:
            return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"存储服务错误: {str(e)}", False)
        except Exception as e:
            return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)

    def get_avatar(self, filename):
        """获取用户头像（带 Redis 缓存）"""
        try:
            # 移除尾部斜杠（Flask <path:filename> 会捕获斜杠）
            filename = filename.rstrip('/')

            if not filename:
                return create_response(HTTPStatus.BAD_REQUEST, "文件名为必填项", False)

            # 获取内容类型
            ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else 'jpg'
            content_type_map = {
                'jpg': 'image/jpeg',
                'jpeg': 'image/jpeg',
                'png': 'image/png',
                'gif': 'image/gif',
                'webp': 'image/webp'
            }
            content_type = content_type_map.get(ext, 'image/jpeg')

            # 尝试从 Redis 缓存获取
            cached = self._get_avatar_cache(filename)
            if cached:
                # 从缓存返回
                file_content = cached['content'].encode('latin-1')
                return send_file(
                    BytesIO(file_content),
                    mimetype=content_type,
                    as_attachment=False,
                    download_name=filename
                )

            # 从 RUSTFS 获取文件
            response = self.client.get_object(self.bucket_name, filename)
            file_content = response.read()
            response.close()

            # 写入 Redis 缓存
            self._set_avatar_cache(filename, file_content, content_type)

            return send_file(
                BytesIO(file_content),
                mimetype=content_type,
                as_attachment=False,
                download_name=filename
            )

        except S3Error as e:
            if "Object does not" in str(e) or "NoSuchKey" in str(e):
                return create_response(HTTPStatus.NOT_FOUND, "头像不存在", False)
            return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"获取头像失败: {str(e)}", False)
        except Exception as e:
            return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)

    def delete_avatar(self, filename):
        """删除用户头像"""
        try:
            if not filename:
                return create_response(HTTPStatus.BAD_REQUEST, "文件名为必填项", False)

            self.client.remove_object(self.bucket_name, filename)

            return create_response(
                HTTPStatus.OK,
                "头像删除成功",
                True
            )

        except S3Error as e:
            return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"删除头像失败: {str(e)}", False)
        except Exception as e:
            return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)
