import os
import socket
import paramiko
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from flask.views import MethodView
from flask import request
from http import HTTPStatus

from Common.Response import create_response
from database.Postgresql import get_postgres_connection

# 从 config.py 统一导入配置
from config import (
    FRPS_IP,
    FRPS_PORT,
    MAX_WORKERS,
    CONNECTION_TIMEOUT,
    COMMAND_TIMEOUT,
    CONFIG_FILE
)


def create_frp_socket(host: str):
    """创建 FRP 代理 socket 连接"""
    try:
        sock = socket.create_connection((FRPS_IP, FRPS_PORT), timeout=CONNECTION_TIMEOUT)
        req = f"CONNECT {host} HTTP/1.1\r\nHost: {host}\r\n\r\n"
        sock.send(req.encode())
        resp = sock.recv(1024)
        if b"200" not in resp:
            sock.close()
            return None
        return sock
    except Exception:
        return None


def get_uptime(host: str, password: str):
    """获取单个设备的 uptime（优化超时处理）"""
    sock = None
    transport = None
    try:
        sock = create_frp_socket(host)
        if sock is None:
            return host, "连接不到 FRP 代理"

        transport = paramiko.Transport(sock)

        # 关键修复：增加 banner_timeout 和 auth_timeout，解决 "Error reading SSH protocol banner"
        transport.start_client(timeout=CONNECTION_TIMEOUT)

        # 设置 banner_timeout（等待 SSH 服务返回协议头）
        transport.banner_timeout = 30  # 根据实际网络情况可调整为 20~60 秒
        transport.auth_timeout = 30  # 认证超时

        transport.auth_password("root", password)

        channel = transport.open_session()
        channel.settimeout(COMMAND_TIMEOUT)
        channel.exec_command("uptime -p")

        result = channel.makefile().read().decode().strip()
        if not result:
            result = "无返回结果"
        return host, result

    except paramiko.AuthenticationException:
        return host, "SSH 认证失败（密码错误）"
    except paramiko.SSHException as e:
        return host, f"SSH 协议错误: {str(e)}"
    except socket.timeout:
        return host, "连接超时"
    except Exception as e:
        return host, f"连接失败: {str(e)}"
    finally:
        if transport:
            try:
                transport.close()
            except Exception:
                pass
        if sock:
            try:
                sock.close()
            except Exception:
                pass


def read_frp_hosts():
    """读取 Common/config_frp.txt 中的主机和密码"""
    hosts = []
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(maxsplit=1)
                if len(parts) >= 2:
                    host, password = parts[0], parts[1]
                    hosts.append((host, password))
        return hosts
    except FileNotFoundError:
        return []
    except Exception:
        return []

class QueryFrpDeviceUptimeView(MethodView):
    """查询 FRP 设备在线状态及 uptime（POST）"""
    def post(self):
        try:
            data = request.get_json() or {}
            n_str = data.get("number")
            if n_str is not None:
                try:
                    n = int(n_str)
                    if n <= 0:
                        raise ValueError
                except (ValueError, TypeError):
                    return create_response(
                        HTTPStatus.BAD_REQUEST,
                        "number 必须为正整数",
                        False
                    )
            else:
                n = None

            hosts = read_frp_hosts()
            if not hosts:
                return create_response(
                    HTTPStatus.NOT_FOUND,
                    "配置文件中未找到任何设备信息",
                    False
                )

            query_start = datetime.now()

            records = []
            hosts_results = []  # 用于入库：(host, password, uptime_result)

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = [
                    executor.submit(get_uptime, host, password)
                    for host, password in hosts
                ]
                for future in as_completed(futures):
                    host, uptime_result = future.result()
                    query_time = datetime.now()

                    records.append({
                        "host": host,
                        "uptime": uptime_result,
                        "query_time": query_time.isoformat(),
                        "query_time_local": query_time.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                        "uptime_s": query_time.astimezone().strftime("%Y-%m-%d %H:%M:%S")   # 新增字段
                    })

                    # 保存用于入库
                    hosts_results.append((host, next((p for h, p in hosts if h == host), ""), uptime_result))

            if n is not None and n < len(records):
                records = records[:n]

            self._save_uptime_logs(hosts_results)

            return create_response(
                HTTPStatus.OK,
                "查询成功",
                True,
                data={
                    "total_devices": len(hosts),
                    "returned_count": len(records),
                    "requested_count": n if n is not None else "all",
                    "query_start_time": query_start.isoformat(),
                    "query_start_time_local": query_start.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                    "records": records
                }
            )

        except Exception as e:
            return create_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"服务器错误: {str(e)}",
                False
            )

    def _save_uptime_logs(self, hosts_results):
        """内部方法：批量保存本次查询结果到数据库"""
        if not hosts_results:
            return

        conn = None
        try:
            conn = get_postgres_connection()
            with conn.cursor() as cur:
                now = datetime.now()

                # 1. Upsert frp_device 表（确保设备存在并更新密码）
                device_data = [(host, pwd) for host, pwd, _ in hosts_results]
                cur.executemany("""
                    INSERT INTO frp_device (host, password, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (host) 
                    DO UPDATE SET 
                        password = EXCLUDED.password,
                        updated_at = CURRENT_TIMESTAMP
                """, device_data)

                # 2. 获取 device_id 映射
                host_list = [host for host, _, _ in hosts_results]
                cur.execute("""
                    SELECT id, host FROM frp_device 
                    WHERE host = ANY(%s)
                """, (host_list,))
                device_map = {row[1]: row[0] for row in cur.fetchall()}

                # 3. 准备日志插入数据
                log_data = []
                for host, _, uptime_result in hosts_results:
                    device_id = device_map.get(host)
                    if device_id:
                        log_data.append((
                            device_id,
                            host,
                            uptime_result,
                            now
                        ))

                # 4. 批量插入日志表
                if log_data:
                    cur.executemany("""
                        INSERT INTO frp_device_uptime_log 
                            (device_id, host, uptime_result, query_time)
                        VALUES (%s, %s, %s, %s)
                    """, log_data)

            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            print(f"FRP uptime 日志入库失败: {str(e)}")
        finally:
            if conn:
                conn.close()

class UpdateFrpConfigView(MethodView):
    """更新 FRP 设备配置文件（POST）"""

    CONFIG_FILE = "Common/config_frp.txt"
    CONFIG_DIR = "Common"

    def post(self):
        try:
            data = request.get_json() or {}

            # 获取前端传递的设备列表
            devices = data.get("devices")

            # 参数校验
            if not devices or not isinstance(devices, list):
                return create_response(
                    HTTPStatus.BAD_REQUEST,
                    "缺少或格式错误的 devices 参数，必须为设备列表",
                    False
                )

            # 校验每个设备项必须包含 host 和 password
            for item in devices:
                if not isinstance(item, dict):
                    return create_response(
                        HTTPStatus.BAD_REQUEST,
                        "devices 列表中的每一项必须为对象",
                        False
                    )
                if "host" not in item or "password" not in item:
                    return create_response(
                        HTTPStatus.BAD_REQUEST,
                        "每个设备必须包含 host 和 password 字段",
                        False
                    )
                if not item["host"] or not isinstance(item["host"], str):
                    return create_response(
                        HTTPStatus.BAD_REQUEST,
                        "host 不能为空且必须为字符串",
                        False
                    )

            # 确保目录存在
            os.makedirs(self.CONFIG_DIR, exist_ok=True)

            # 写入配置文件（覆盖式更新）
            with open(self.CONFIG_FILE, "w", encoding="utf-8") as f:
                for item in devices:
                    host = item["host"].strip()
                    password = item["password"].strip()
                    if host:  # 跳过空 host
                        f.write(f"{host} {password}\n")

            # 可选：返回更新后的内容供前端确认
            updated_hosts = [item["host"] for item in devices if item.get("host")]

            return create_response(
                HTTPStatus.OK,
                "FRP 配置文件更新成功",
                True,
                data={
                    "total_devices": len(updated_hosts),
                    "hosts": updated_hosts,
                    "message": f"已成功更新 {len(updated_hosts)} 台设备配置"
                }
            )

        except Exception as e:
            return create_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"服务器错误: {str(e)}",
                False
            )

class UpdateN2NConfigView(MethodView):
    """更新 FRP 设备配置文件（POST）"""
    CONFIG_FILE = "Common/config_n2n.txt"
    CONFIG_DIR = "Common"

    def post(self):
        try:
            data = request.get_json() or {}

            # 获取前端传递的设备列表
            devices = data.get("devices")

            # 参数校验
            if not devices or not isinstance(devices, list):
                return create_response(
                    HTTPStatus.BAD_REQUEST,
                    "缺少或格式错误的 devices 参数，必须为设备列表",
                    False
                )
            for item in devices:
                if not isinstance(item, dict):
                    return create_response(
                        HTTPStatus.BAD_REQUEST,
                        "devices 列表中的每一项必须为对象",
                        False
                    )
                if "host" not in item or "password" not in item:
                    return create_response(
                        HTTPStatus.BAD_REQUEST,
                        "每个设备必须包含 host 和 password 字段",
                        False
                    )
                if not item["host"] or not isinstance(item["host"], str):
                    return create_response(
                        HTTPStatus.BAD_REQUEST,
                        "host 不能为空且必须为字符串",
                        False
                    )

            os.makedirs(self.CONFIG_DIR, exist_ok=True)
            # 写入配置文件（覆盖式更新）
            with open(self.CONFIG_FILE, "w", encoding="utf-8") as f:
                for item in devices:
                    host = item["host"].strip()
                    password = item["password"].strip()
                    if host:  # 跳过空 host
                        f.write(f"{host} {password}\n")

            updated_hosts = [item["host"] for item in devices if item.get("host")]
            return create_response(
                HTTPStatus.OK,
                "FRP 配置文件更新成功",
                True,
                data={
                    "total_devices": len(updated_hosts),
                    "hosts": updated_hosts,
                    "message": f"已成功更新 {len(updated_hosts)} 台设备配置"
                }
            )

        except Exception as e:
            return create_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"服务器错误: {str(e)}",
                False
            )