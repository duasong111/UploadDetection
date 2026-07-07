"""
固件管理模块
上传、下载、列表查询
"""
import os
import hashlib
import redis
from io import BytesIO
from datetime import datetime
from minio import Minio
from minio.error import S3Error
from flask import request, send_file
from http import HTTPStatus
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    BUCKET_IP, BUCKET_PORT, RUSTFS_BUCKET_NAME, RUSTFS_SECRET,
    REDIS_URL
)
from Common.Response import create_response

# Redis 客户端
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# 固件桶名
FIRMWARE_BUCKET_NAME = "firmware"

# 去重前缀（基于文件内容哈希）
UPLOAD_DEDUP_PREFIX = "firmware:dedup:"

# 下载限流
DOWNLOAD_RATE_PREFIX = "firmware:dl:rate:"
DOWNLOAD_RATE_LIMIT = 10       # 每分钟最多10次
DOWNLOAD_RATE_WINDOW = 60      # 窗口60秒

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


class FirmwareManager:
    """固件管理类"""

    ALLOWED_EXTENSIONS = {"bin"}
    MAX_VERSIONS = 5

    def __init__(self):
        self.client = Minio(
            f"{BUCKET_IP}:{BUCKET_PORT}",
            access_key=RUSTFS_BUCKET_NAME,
            secret_key=RUSTFS_SECRET,
            secure=False
        )
        self.bucket_name = FIRMWARE_BUCKET_NAME
        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self):
        try:
            if not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name)
        except S3Error:
            pass

    def _get_file_hash(self, file_data: bytes) -> str:
        return hashlib.sha256(file_data).hexdigest()

    def _list_all_objects(self):
        """列出桶中所有对象"""
        try:
            return list(self.client.list_objects(self.bucket_name, recursive=True))
        except S3Error:
            return []

    def _get_versions(self, base_name: str):
        """获取某固件的所有版本，按时间倒序"""
        try:
            objects = self.client.list_objects(self.bucket_name, prefix=base_name, recursive=True)
            items = []
            for obj in objects:
                items.append({
                    "name": obj.object_name,
                    "size": obj.size,
                    "last_modified": obj.last_modified.isoformat() if obj.last_modified else None
                })
            items.sort(key=lambda x: x["last_modified"] or "", reverse=True)
            return items
        except S3Error:
            return []

    def _delete_old_versions(self, base_name: str, keep: int = 5):
        """删除旧版本，保留最新 N 个"""
        versions = self._get_versions(base_name)
        if len(versions) <= keep:
            return
        for v in versions[keep:]:
            try:
                self.client.remove_object(self.bucket_name, v["name"])
            except S3Error as e:
                print(f"[Firmware] Delete old version failed: {e}")

    def upload_firmware(self, file_data: bytes, filename: str) -> tuple:
        """
        上传固件 .bin 文件
        - 基于内容哈希去重
        - 同一固件最多保留5个版本
        Returns: (success, message, data)
        """
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in self.ALLOWED_EXTENSIONS:
            return False, f"仅支持 .bin 文件，当前: .{ext}", None

        if len(file_data) > MAX_FILE_SIZE:
            return False, f"文件大小超过 {MAX_FILE_SIZE // (1024*1024)}MB 限制", None

        # 基于内容哈希去重
        file_hash = self._get_file_hash(file_data)
        dedup_key = f"{UPLOAD_DEDUP_PREFIX}{file_hash}"
        if redis_client.exists(dedup_key):
            existing = redis_client.get(dedup_key)
            return False, f"固件已存在: {existing}", {"filename": existing, "duplicate": True}

        # 生成存储路径: {固件名}/{时间戳_hash12}.bin
        safe_hash = file_hash[:12]
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        base_name = filename.rstrip(".bin").rstrip(".")
        stored_name = f"{base_name}/{ts}_{safe_hash}.bin"

        try:
            self.client.put_object(
                self.bucket_name,
                stored_name,
                BytesIO(file_data),
                length=len(file_data)
            )
            # 写入去重记录
            redis_client.set(dedup_key, stored_name)
            # 清理旧版本
            self._delete_old_versions(base_name, self.MAX_VERSIONS)

            return True, f"上传成功", {
                "filename": stored_name,
                "size": len(file_data)
            }
        except S3Error as e:
            return False, f"上传失败: {e}", None

    def download_firmware(self, filename: str, username: str = None) -> tuple:
        """
        下载固件，带频率限制
        每用户每分钟最多 DOWNLOAD_RATE_LIMIT 次
        Returns: (flask_response, http_status_code)
        """
        user = username or request.remote_addr or "anonymous"
        rate_key = f"{DOWNLOAD_RATE_PREFIX}{user}"

        try:
            count = redis_client.get(rate_key)
            count = int(count) if count else 0
            if count >= DOWNLOAD_RATE_LIMIT:
                msg = f"下载过于频繁，请{DOWNLOAD_RATE_WINDOW // 60}分钟后再试 ({count}/{DOWNLOAD_RATE_LIMIT})"
                return create_response(HTTPStatus.TOO_MANY_REQUESTS, msg, False), HTTPStatus.TOO_MANY_REQUESTS

            # 增加计数，60秒过期
            redis_client.setex(rate_key, DOWNLOAD_RATE_WINDOW, count + 1)
        except Exception as e:
            print(f"[Firmware] Rate limit check error: {e}")

        # 读取文件
        try:
            resp = self.client.get_object(self.bucket_name, filename)
            data = resp.read()
            resp.close()
            resp.release_conn()
            return send_file(
                BytesIO(data),
                mimetype="application/octet-stream",
                as_attachment=True,
                download_name=filename.rsplit("/", 1)[-1]
            ), HTTPStatus.OK
        except S3Error as e:
            if "Object does not" in str(e) or "NoSuchKey" in str(e):
                return create_response(HTTPStatus.NOT_FOUND, "固件不存在", False), HTTPStatus.NOT_FOUND
            return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"下载失败: {e}", False), HTTPStatus.INTERNAL_SERVER_ERROR

    def list_firmware(self) -> list:
        """
        固件列表，按固件名分组，每组返回最新版本信息
        """
        try:
            objects = self._list_all_objects()
            # 按固件名（路径第一段）分组
            groups = {}
            for obj in objects:
                name = obj.object_name
                if name.endswith("/"):
                    continue
                parts = name.split("/")
                base = parts[0] if len(parts) > 1 else name
                version = "/".join(parts[1:]) if len(parts) > 1 else ""
                if base not in groups:
                    groups[base] = {"versions": [], "latest": None}
                groups[base]["versions"].append({
                    "name": name,
                    "version_file": version,
                    "size": obj.size,
                    "last_modified": obj.last_modified.isoformat() if obj.last_modified else None
                })
                # 更新最新版本
                if (not groups[base]["latest"] or
                    (obj.last_modified and
                     groups[base]["latest"]["last_modified"] and
                     obj.last_modified > groups[base]["latest"]["last_modified"])):
                    groups[base]["latest"] = {
                        "name": name,
                        "version_file": version,
                        "size": obj.size,
                        "last_modified": obj.last_modified.isoformat() if obj.last_modified else None
                    }

            result = []
            for base, info in groups.items():
                result.append({
                    "firmware_name": base,
                    "total_versions": len(info["versions"]),
                    "latest": info["latest"]
                })
            result.sort(key=lambda x: x["latest"]["last_modified"] or "" if x["latest"] else "", reverse=True)
            return result
        except S3Error as e:
            print(f"[Firmware] List failed: {e}")
            return []

    def delete_firmware(self, filename: str) -> tuple:
        """删除固件"""
        try:
            try:
                self.client.stat_object(self.bucket_name, filename)
            except S3Error:
                return False, "固件不存在", None
            self.client.remove_object(self.bucket_name, filename)
            return True, "删除成功", None
        except S3Error as e:
            return False, f"删除失败: {e}", None
