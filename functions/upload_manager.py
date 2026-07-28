"""
安全文件上传管理模块
- 分片上传 + 断点续传
- ClamAV 病毒扫描
- 存储到 MinIO (RUSTFS)
- 生成下载链接供 Ansible/设备使用
"""
import os
import uuid
import json
import socket
import shutil
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from io import BytesIO

from minio import Minio
from minio.error import S3Error
import redis

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    BUCKET_IP, BUCKET_PORT, RUSTFS_BUCKET_NAME, RUSTFS_SECRET,
    UPLOAD_BUCKET_NAME, UPLOAD_MAX_SIZE, UPLOAD_CHUNK_SIZE,
    ALLOWED_UPLOAD_EXTENSIONS, CLAMAV_ENABLED, CLAMAV_TIMEOUT,
    CLAMAV_HOST, CLAMAV_PORT,
    REDIS_URL
)

# ==================== 常量 ====================

TEMP_DIR = Path(__file__).parent.parent / "uploads" / "temp"
QUARANTINE_DIR = Path(__file__).parent.parent / "uploads" / "quarantine"

# Redis Key 前缀
REDIS_UPLOAD_PREFIX = "upload:"
REDIS_UPLOAD_TTL = 7200  # 上传信息保留 2 小时

# 预签名 URL 有效期
PRESIGNED_URL_TTL = timedelta(hours=1)

# ==================== MinIO 客户端 ====================

_minio_client = None


def get_minio_client() -> Minio:
    """获取 MinIO 单例"""
    global _minio_client
    if _minio_client is None:
        _minio_client = Minio(
            f"{BUCKET_IP}:{BUCKET_PORT}",
            access_key=RUSTFS_BUCKET_NAME,
            secret_key=RUSTFS_SECRET,
            secure=False
        )
    return _minio_client


_redis_client = None


def get_redis() -> redis.Redis:
    """获取 Redis 单例"""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


# ==================== 上传管理器 ====================

class UploadManager:
    """安全文件上传管理器（分片 + ClamAV + MinIO）"""

    def __init__(self):
        self.minio = get_minio_client()
        self.r = get_redis()
        self.bucket = UPLOAD_BUCKET_NAME
        self._ensure_bucket()

    def _ensure_bucket(self):
        """确保上传桶存在"""
        try:
            if not self.minio.bucket_exists(self.bucket):
                self.minio.make_bucket(self.bucket)
        except S3Error:
            pass

    # ==================== 上传流程 ====================

    def init_upload(self, filename: str, file_size: int, file_type: str = None) -> Dict:
        """
        初始化上传

        Args:
            filename: 原始文件名
            file_size: 文件总大小（字节）
            file_type: 文件类型描述（可选）

        Returns:
            包含 upload_id 的上传初始化信息
        """
        # 校验文件大小
        if file_size > UPLOAD_MAX_SIZE:
            return {
                'success': False,
                'error': f'文件大小超过限制 ({UPLOAD_MAX_SIZE // 1024 // 1024}MB)'
            }

        # 校验文件扩展名
        ext = Path(filename).suffix.lower()
        ext_display = ext if ext else '（无后缀）'
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            return {
                'success': False,
                'error': f'不支持的文件类型: {ext_display}，仅支持: {", ".join(e if e else "无后缀" for e in ALLOWED_UPLOAD_EXTENSIONS)}'
            }

        upload_id = uuid.uuid4().hex
        chunk_count = (file_size + UPLOAD_CHUNK_SIZE - 1) // UPLOAD_CHUNK_SIZE

        # 创建临时目录
        chunk_dir = TEMP_DIR / upload_id
        chunk_dir.mkdir(parents=True, exist_ok=True)

        # 记录上传信息到 Redis
        upload_info = {
            'upload_id': upload_id,
            'filename': filename,
            'file_size': file_size,
            'file_type': file_type or '',
            'chunk_count': chunk_count,
            'chunk_size': UPLOAD_CHUNK_SIZE,
            'received_chunks': 0,
            'status': 'initialized',
            'created_at': datetime.now().isoformat()
        }
        self.r.setex(f'{REDIS_UPLOAD_PREFIX}{upload_id}', REDIS_UPLOAD_TTL, json.dumps(upload_info))

        return {
            'success': True,
            'upload_id': upload_id,
            'chunk_size': UPLOAD_CHUNK_SIZE,
            'chunk_count': chunk_count,
            'status': 'initialized'
        }

    def save_chunk(self, upload_id: str, chunk_index: int, chunk_data: bytes) -> Dict:
        """
        保存单个分片

        Args:
            upload_id: 上传 ID
            chunk_index: 分片索引（从 0 开始）
            chunk_data: 分片二进制数据

        Returns:
            保存结果
        """
        # 校验 upload_id
        upload_info_json = self.r.get(f'{REDIS_UPLOAD_PREFIX}{upload_id}')
        if not upload_info_json:
            return {'success': False, 'error': 'upload_id 不存在或已过期'}

        # 保存分片到临时目录
        chunk_path = TEMP_DIR / upload_id / f'chunk_{chunk_index:06d}'
        chunk_path.write_bytes(chunk_data)

        # 更新进度
        upload_info = json.loads(upload_info_json)
        upload_info['received_chunks'] += 1
        upload_info['status'] = 'uploading'
        progress = int(upload_info['received_chunks'] / upload_info['chunk_count'] * 100)
        upload_info['progress'] = progress
        self.r.setex(f'{REDIS_UPLOAD_PREFIX}{upload_id}', REDIS_UPLOAD_TTL, json.dumps(upload_info))

        return {
            'success': True,
            'upload_id': upload_id,
            'chunk_index': chunk_index,
            'received': True,
            'progress': progress
        }

    def complete_upload(self, upload_id: str) -> Dict:
        """
        完成上传：合并分片 → ClamAV 扫描 → 上传 MinIO → 清理

        Args:
            upload_id: 上传 ID

        Returns:
            上传结果，包含 MinIO 路径和下载链接
        """
        upload_info_json = self.r.get(f'{REDIS_UPLOAD_PREFIX}{upload_id}')
        if not upload_info_json:
            return {'success': False, 'error': 'upload_id 不存在或已过期'}

        upload_info = json.loads(upload_info_json)
        chunk_dir = TEMP_DIR / upload_id

        if not chunk_dir.exists():
            return {'success': False, 'error': '分片目录不存在'}

        # 1. 合并分片
        date_str = datetime.now().strftime('%Y%m%d')
        safe_name = f"{uuid.uuid4().hex[:12]}_{upload_info['filename']}"
        local_temp_path = Path(__file__).parent.parent / "uploads" / "temp" / f"{upload_id}_merged"
        local_temp_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            chunk_files = sorted(chunk_dir.iterdir(), key=lambda p: int(p.name.split('_')[1]))
            with open(local_temp_path, 'wb') as f:
                for cf in chunk_files:
                    f.write(cf.read_bytes())

            # 清理分片目录
            shutil.rmtree(chunk_dir)

            # 2. 校验文件大小
            file_size = local_temp_path.stat().st_size
            if file_size > UPLOAD_MAX_SIZE:
                local_temp_path.unlink()
                return {'success': False, 'error': f'合并后文件大小超过限制 ({UPLOAD_MAX_SIZE // 1024 // 1024}MB)'}

            # 3. 计算文件哈希
            sha256_hash = hashlib.sha256()
            md5_hash = hashlib.md5()
            with open(local_temp_path, 'rb') as f:
                while chunk := f.read(8192):
                    sha256_hash.update(chunk)
                    md5_hash.update(chunk)
            file_sha256 = sha256_hash.hexdigest()
            file_md5 = md5_hash.hexdigest()

            # 4. ClamAV 病毒扫描
            scan_result = self._clamav_scan(local_temp_path)
            if scan_result['status'] == 'infected':
                # 移动到隔离区
                quarantine_path = QUARANTINE_DIR / safe_name
                quarantine_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(local_temp_path), str(quarantine_path))

                upload_info['status'] = 'quarantined'
                upload_info['virus_signature'] = scan_result['signature']
                self.r.setex(f'{REDIS_UPLOAD_PREFIX}{upload_id}', REDIS_UPLOAD_TTL, json.dumps(upload_info))

                return {
                    'success': False,
                    'error': f'文件检测到病毒: {scan_result["signature"]}',
                    'virus_signature': scan_result['signature'],
                    'quarantined': True
                }

            # 5. 上传到 MinIO
            object_name = f"uploads/{date_str}/{safe_name}"
            with open(local_temp_path, 'rb') as f:
                self.minio.put_object(
                    self.bucket,
                    object_name,
                    f,
                    length=file_size,
                    content_type='application/octet-stream'
                )

            # 6. 生成预签名下载 URL
            presigned_url = self.minio.presigned_get_object(
                self.bucket,
                object_name,
                expires=PRESIGNED_URL_TTL
            )

            # 7. 更新 Redis 状态
            upload_info['status'] = 'completed'
            upload_info['object_name'] = object_name
            upload_info['file_size'] = file_size
            upload_info['file_sha256'] = file_sha256
            upload_info['file_md5'] = file_md5
            upload_info['virus_scan'] = 'clean'
            upload_info['download_url'] = presigned_url
            self.r.setex(f'{REDIS_UPLOAD_PREFIX}{upload_id}', REDIS_UPLOAD_TTL, json.dumps(upload_info))

            # 8. 清理本地临时文件
            local_temp_path.unlink(missing_ok=True)

            return {
                'success': True,
                'upload_id': upload_id,
                'filename': upload_info['filename'],
                'object_name': object_name,
                'file_size': file_size,
                'file_hash': {
                    'sha256': file_sha256,
                    'md5': file_md5
                },
                'virus_scan': 'clean',
                'download_url': presigned_url,
                'status': 'completed'
            }

        except Exception as e:
            # 清理残留文件
            if local_temp_path.exists():
                local_temp_path.unlink()
            return {'success': False, 'error': f'上传处理失败: {str(e)}'}

    # ==================== 查询接口 ====================

    def get_upload_status(self, upload_id: str) -> Dict:
        """查询上传状态"""
        upload_info_json = self.r.get(f'{REDIS_UPLOAD_PREFIX}{upload_id}')
        if not upload_info_json:
            return {'success': False, 'error': 'upload_id 不存在或已过期'}

        info = json.loads(upload_info_json)
        return {
            'success': True,
            'upload_id': info['upload_id'],
            'filename': info['filename'],
            'file_size': info['file_size'],
            'status': info.get('status', 'unknown'),
            'progress': info.get('progress', 0),
            'received_chunks': info.get('received_chunks', 0),
            'total_chunks': info.get('chunk_count', 0),
            'virus_scan': info.get('virus_scan'),
            'download_url': info.get('download_url'),
            'created_at': info.get('created_at')
        }

    def get_cancelled_chunks(self, upload_id: str) -> Dict:
        """
        查询已收到的分片索引（用于断点续传）
        返回缺失的分片列表
        """
        upload_info_json = self.r.get(f'{REDIS_UPLOAD_PREFIX}{upload_id}')
        if not upload_info_json:
            return {'success': False, 'error': 'upload_id 不存在'}

        info = json.loads(upload_info_json)
        chunk_dir = TEMP_DIR / upload_id

        if not chunk_dir.exists():
            return {
                'success': True,
                'received_chunks': [],
                'missing_chunks': list(range(info['chunk_count']))
            }

        received = set()
        for f in chunk_dir.iterdir():
            if f.name.startswith('chunk_'):
                idx = int(f.name.split('_')[1])
                received.add(idx)

        total = info['chunk_count']
        missing = [i for i in range(total) if i not in received]

        return {
            'success': True,
            'received_chunks': sorted(received),
            'missing_chunks': missing,
            'progress': int(len(received) / total * 100)
        }

    # ==================== 下载接口 ====================

    def get_download_url(self, upload_id: str, ttl_hours: int = 1) -> Dict:
        """
        获取新的预签名下载 URL

        Args:
            upload_id: 上传 ID
            ttl_hours: URL 有效期（小时）

        Returns:
            下载 URL
        """
        upload_info_json = self.r.get(f'{REDIS_UPLOAD_PREFIX}{upload_id}')
        if not upload_info_json:
            return {'success': False, 'error': 'upload_id 不存在'}

        info = json.loads(upload_info_json)
        if info.get('status') != 'completed':
            return {'success': False, 'error': '文件还未上传完成'}

        object_name = info['object_name']
        url = self.minio.presigned_get_object(
            self.bucket,
            object_name,
            expires=timedelta(hours=ttl_hours)
        )

        return {
            'success': True,
            'download_url': url,
            'expires_in_hours': ttl_hours,
            'filename': info['filename'],
            'file_size': info['file_size']
        }

    # ==================== ClamAV 扫描 ====================

    def _clamav_scan(self, file_path: Path) -> Dict:
        """
        ClamAV 病毒扫描（通过 TCP 连接 Docker ClamAV）
        自动检测本地和远程 ClamAV
        """
        if not CLAMAV_ENABLED:
            return {'status': 'skipped', 'signature': None}

        # 优先尝试远程 ClamAV Docker 实例
        if CLAMAV_HOST and CLAMAV_PORT:
            try:
                return self._clamav_scan_remote(file_path)
            except Exception as e:
                print(f"[ClamAV] 远程扫描失败，回退本地: {e}")

        # 回退本地 clamdscan
        return self._clamav_scan_local(file_path)

    def _clamav_scan_remote(self, file_path: Path) -> Dict:
        """通过 TCP 连接远程 ClamAV Docker 扫描"""
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(CLAMAV_TIMEOUT)
            sock.connect((CLAMAV_HOST, CLAMAV_PORT))

            # 发送 INSTREAM 扫描命令
            sock.send(b'zINSTREAM\0')

            # 分块发送文件内容
            with open(file_path, 'rb') as f:
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    length = len(chunk).to_bytes(4, 'big')
                    sock.send(length + chunk)

            # 发送结束标志
            sock.send((0).to_bytes(4, 'big'))

            # 读取结果（最多 4096 字节）
            result = sock.recv(4096).decode().strip()
            sock.close()
            sock = None

            if 'FOUND' in result:
                sig = result.split(':')[1].strip() if ':' in result else result
                print(f"[ClamAV] 🔴 检测到病毒: {sig} → {file_path.name}")
                return {'status': 'infected', 'signature': sig}

            print(f"[ClamAV] 🟢 扫描通过: {file_path.name}")
            return {'status': 'clean', 'signature': None}

        except socket.timeout:
            print(f"[ClamAV] 远程扫描超时 ({CLAMAV_TIMEOUT}s)")
            return {'status': 'error', 'signature': 'timeout'}
        except ConnectionRefusedError:
            print(f"[ClamAV] 拒绝连接: {CLAMAV_HOST}:{CLAMAV_PORT}")
            return {'status': 'skipped', 'signature': 'connection_refused'}
        except Exception as e:
            print(f"[ClamAV] 远程扫描异常: {e}")
            return {'status': 'error', 'signature': str(e)}
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    def _clamav_scan_local(self, file_path: Path) -> Dict:
        """本地 ClamAV 扫描（回退方案）"""
        import subprocess

        try:
            result = subprocess.run(
                ['clamdscan', '--no-summary', str(file_path)],
                capture_output=True, text=True, timeout=CLAMAV_TIMEOUT
            )
            output = result.stdout.strip()

            if 'FOUND' in output:
                sig = output.split(':')[1].strip() if ':' in output else 'unknown'
                print(f"[ClamAV] 检测到病毒: {sig} → {file_path.name}")
                return {'status': 'infected', 'signature': sig}

            print(f"[ClamAV] 本地扫描通过: {file_path.name}")
            return {'status': 'clean', 'signature': None}

        except FileNotFoundError:
            print(f"[ClamAV] 未安装 clamdscan，跳过扫描")
            return {'status': 'skipped', 'signature': 'clamav_not_installed'}
        except subprocess.TimeoutExpired:
            print(f"[ClamAV] 扫描超时")
            return {'status': 'error', 'signature': 'timeout'}
        except Exception as e:
            print(f"[ClamAV] 本地扫描异常: {e}")
            return {'status': 'error', 'signature': str(e)}


# 全局实例
upload_manager = UploadManager()
