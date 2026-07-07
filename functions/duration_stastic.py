from flask.views import MethodView
from flask import request
from http import HTTPStatus
import paramiko
import time
import logging
from Common.Response import create_response

logger = logging.getLogger(__name__)


class AddDurationTime(MethodView):
    """部署 duration_time.py 并设置为开机自启"""

    def post(self):
        try:
            data = request.get_json(silent=True)
            if data is None:
                return create_response(HTTPStatus.BAD_REQUEST, "请求体必须是有效的 JSON", False)

            ip = data.get('ip')
            password = data.get('password')
            device_sn = data.get('device_sn')

            if not all([ip, password, device_sn]):
                return create_response(HTTPStatus.BAD_REQUEST, "缺少必要参数: ip, password, device_sn", False)

            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(hostname=ip, port=22, username='root', password=password, timeout=15)

            # ==================== 1. 创建 /home/shell 目录 ====================
            self._exec_command(ssh, "mkdir -p /home/shell", "创建目录")

            # ==================== 2. 上传并定制 duration_time.py ====================
            duration_script = f'''# 主要的功能是上传当前运行时间，单独再服务器上运行
import time
import requests
import uuid
from datetime import datetime
import signal
import sys

# ==================== 配置 ====================
SERVER_URL = "http://8.134.128.64:7690/api/static_time/"
DEVICE_SN = "{device_sn}"                                 # ← 根据参数动态设置
INTERVAL    = 60
REQUEST_TIMEOUT = 10
# =============================================

SESSION_UUID = str(uuid.uuid4())
start_monotonic = time.monotonic()
start_time_str  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

running = True

def graceful_exit(signum, frame):
    global running
    print("\\n收到退出信号，正在安全退出...")
    running = False

signal.signal(signal.SIGINT, graceful_exit)
signal.signal(signal.SIGTERM, graceful_exit)

def report():
    elapsed = int(time.monotonic() - start_monotonic)
    payload = {{
        "sn": DEVICE_SN,
        "uuid": SESSION_UUID,
        "runtime": elapsed
    }}
    try:
        resp = requests.post(SERVER_URL, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        print(f"[{{datetime.now():%Y-%m-%d %H:%M:%S}}] "
              f"上报成功 | 已运行 {{elapsed:,}} 秒 | "
              f"本次会话峰值 {{data.get('session_max_runtime', elapsed):,}} 秒")
    except requests.RequestException as e:
        print(f"[{{datetime.now():%Y-%m-%d %H:%M:%S}}] 上报失败: {{e}}")

def main():
    print("===== 运行时长上报服务启动 =====")
    print(f"启动时刻     : {{start_time_str}}")
    print(f"设备 SN       : {{DEVICE_SN}}")
    print(f"本次会话 UUID : {{SESSION_UUID}}")
    print(f"上报间隔      : {{INTERVAL}} 秒")
    print("=================================")

    next_report_time = time.monotonic()
    while running:
        next_report_time += INTERVAL
        sleep_duration = next_report_time - time.monotonic()
        if sleep_duration > 0:
            time.sleep(sleep_duration)
        report()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"主循环异常退出: {{e}}", file=sys.stderr)
        sys.exit(1)
'''

            # 写入文件
            write_cmd = f'''cat > /home/shell/duration_time.py << 'EOF'
{duration_script}
EOF'''
            self._exec_command(ssh, write_cmd, "写入 duration_time.py")

            # 赋予执行权限
            self._exec_command(ssh, "chmod +x /home/shell/duration_time.py", "添加执行权限")

            # ==================== 3. 创建 systemd 服务 ====================
            service_content = '''[Unit]
Description=Duration Time Reporter Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/shell
ExecStart=/usr/bin/python3 /home/shell/duration_time.py
Restart=always
RestartSec=5
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
'''

            service_cmd = f'''cat > /etc/systemd/system/duration_time.service << 'EOF'
{service_content}
EOF'''
            self._exec_command(ssh, f"sudo {service_cmd}", "创建 systemd 服务文件")

            # ==================== 4. 启动并设置开机自启 ====================
            commands = [
                "sudo systemctl daemon-reload",
                "sudo systemctl enable duration_time.service",
                "sudo systemctl start duration_time.service",
                "sudo systemctl status duration_time.service --no-pager"
            ]

            for cmd in commands:
                self._exec_command(ssh, cmd, f"执行: {cmd}")

            ssh.close()
            return create_response(HTTPStatus.OK, f"运行时长上报服务部署成功，SN: {device_sn}", True)

        except Exception as e:
            return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"部署失败: {str(e)}", False)

    def _exec_command(self, ssh, command, desc="执行命令"):
        _, stdout, stderr = ssh.exec_command(command)
        out = stdout.read().decode('utf-8', errors='ignore')
        err = stderr.read().decode('utf-8', errors='ignore')
        time.sleep(1)

class ControlDurationTime(MethodView):
    """控制 duration_time 服务开机自启状态"""

    def post(self):
        try:
            data = request.get_json(silent=True)
            if data is None:
                return create_response(HTTPStatus.BAD_REQUEST, "请求体必须是有效的 JSON", False)

            ip = data.get('ip')
            password = data.get('password')
            enable = data.get('enable')          # True = 启用开机自启，False = 禁用

            if not all([ip, password]) or not isinstance(enable, bool):
                return create_response(HTTPStatus.BAD_REQUEST, "缺少参数或 enable 必须为布尔值", False)

            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(hostname=ip, port=22, username='root', password=password, timeout=15)

            if enable:
                # 启用开机自启
                commands = [
                    "sudo systemctl daemon-reload",
                    "sudo systemctl enable duration_time.service",
                    "sudo systemctl start duration_time.service",
                    "sudo systemctl status duration_time.service --no-pager"
                ]
                msg = "已启用开机自启"
            else:
                # 禁用开机自启
                commands = [
                    "sudo systemctl stop duration_time.service",
                    "sudo systemctl disable duration_time.service",
                    "sudo systemctl status duration_time.service --no-pager"
                ]
                msg = "已禁用开机自启（服务已停止）"

            for cmd in commands:
                self._exec_command(ssh, cmd, f"执行: {cmd}")

            ssh.close()
            return create_response(HTTPStatus.OK, msg, True)

        except Exception as e:
            return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"操作失败: {str(e)}", False)

    def _exec_command(self, ssh, command, desc="执行命令"):
        _, stdout, stderr = ssh.exec_command(command)
        out = stdout.read().decode('utf-8', errors='ignore')
        err = stderr.read().decode('utf-8', errors='ignore')
        time.sleep(1)