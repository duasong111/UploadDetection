"""
FRP & SSH 配置 API 模块
"""
import os
import time
import socket
import paramiko
from datetime import datetime
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import FRPS_IP, FRPS_PORT, MAX_WORKERS, CONNECTION_TIMEOUT, COMMAND_TIMEOUT, CONFIG_FILE
from Common.Response import create_response


def create_frp_socket(host: str):
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
    sock, transport = None, None
    try:
        sock = create_frp_socket(host)
        if sock is None:
            return host, "连接不到 FRP 代理"
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=CONNECTION_TIMEOUT)
        transport.banner_timeout = 30
        transport.auth_timeout = 30
        transport.auth_password("root", password)
        channel = transport.open_session()
        channel.settimeout(COMMAND_TIMEOUT)
        channel.exec_command("uptime -p")
        result = channel.makefile().read().decode().strip()
        return host, result if result else "无返回结果"
    except paramiko.AuthenticationException:
        return host, "SSH 认证失败"
    except paramiko.SSHException as e:
        return host, f"SSH 协议错误: {e}"
    except socket.timeout:
        return host, "连接超时"
    except Exception as e:
        return host, f"连接失败: {e}"
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
    hosts = []
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(maxsplit=1)
                if len(parts) >= 2:
                    hosts.append((parts[0], parts[1]))
    except FileNotFoundError:
        return []
    return hosts


def query_frp_uptime(n: Optional[int] = None) -> Dict:
    """查询FRP设备在线状态"""
    hosts = read_frp_hosts()
    if not hosts:
        return create_response(404, "配置文件中未找到任何设备信息", False)

    query_start = datetime.now()
    records = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(get_uptime, h, p) for h, p in hosts]
        for future in as_completed(futures):
            host, uptime_result = future.result()
            query_time = datetime.now()
            records.append({
                "host": host,
                "uptime": uptime_result,
                "query_time": query_time.isoformat(),
                "query_time_local": query_time.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                "uptime_s": query_time.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            })

    if n is not None and n < len(records):
        records = records[:n]

    return create_response(200, "查询成功", True, {
        "total_devices": len(hosts),
        "returned_count": len(records),
        "requested_count": n if n is not None else "all",
        "query_start_time": query_start.isoformat(),
        "query_start_time_local": query_start.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "records": records
    })


def update_frp_config(devices: List[Dict]) -> Dict:
    """更新FRP配置文件"""
    os.makedirs("Common", exist_ok=True)
    with open("Common/config_frp.txt", "w", encoding="utf-8") as f:
        for item in devices:
            host = item["host"].strip()
            password = item["password"].strip()
            if host:
                f.write(f"{host} {password}\n")

    updated_hosts = [item["host"] for item in devices if item.get("host")]
    return create_response(200, "FRP 配置文件更新成功", True, {
        "total_devices": len(updated_hosts),
        "hosts": updated_hosts,
        "message": f"已成功更新 {len(updated_hosts)} 台设备配置"
    })


def update_n2n_config(devices: List[Dict]) -> Dict:
    """更新N2N配置文件"""
    os.makedirs("Common", exist_ok=True)
    with open("Common/config_n2n.txt", "w", encoding="utf-8") as f:
        for item in devices:
            host = item["host"].strip()
            password = item["password"].strip()
            if host:
                f.write(f"{host} {password}\n")

    updated_hosts = [item["host"] for item in devices if item.get("host")]
    return create_response(200, "N2N 配置文件更新成功", True, {
        "total_devices": len(updated_hosts),
        "hosts": updated_hosts,
        "message": f"已成功更新 {len(updated_hosts)} 台设备配置"
    })


def add_frp(ip: str, password: str, device_name: str) -> Dict:
    """添加FRP配置"""
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=ip, port=22, username='root', password=password,
            timeout=15, allow_agent=False, look_for_keys=False
        )

        toml_content = f'''serverAddr = "8.134.128.64"
serverPort = 7000
auth.method = "token"
auth.token = "token123456"

[[proxies]]
name = "{device_name}"
type = "tcpmux"
multiplexer = "httpconnect"
customDomains = ["{device_name}"]
localIP = "127.0.0.1"
localPort = 22
'''

        service_content = '''[Unit]
Description=FRP Client Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/home/frp_0.61.1_linux_arm/frpc -c /home/frp_0.61.1_linux_arm/frpc.toml
ExecReload=/bin/kill -HUP $MAINPID
Restart=always
RestartSec=10
StartLimitInterval=0
KillMode=mixed
TimeoutStopSec=10
Environment=GOMAXPROCS=1

[Install]
WantedBy=multi-user.target
'''

        def exec_cmd(cmd):
            ssh.exec_command(cmd)
            time.sleep(0.5)

        exec_cmd(f'cat > /home/frp_0.61.1_linux_arm/frpc.toml << \'EOF\'\n{toml_content}\nEOF')
        exec_cmd(f'cat > /etc/systemd/system/frpc.service << \'EOF\'\n{service_content}\nEOF')
        exec_cmd("sudo systemctl daemon-reload")
        exec_cmd("sudo systemctl enable frpc.service")
        exec_cmd("sudo systemctl restart frpc.service")

        ssh.close()
        return create_response(200, f"FRP 配置添加成功，设备名称: {device_name}", True)

    except paramiko.AuthenticationException:
        return create_response(401, "SSH认证失败，请检查密码", False)
    except paramiko.SSHException as e:
        return create_response(500, f"SSH连接失败: {e}", False)
    except Exception as e:
        return create_response(500, f"添加FRP配置失败: {e}", False)
