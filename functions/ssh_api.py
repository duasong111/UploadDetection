"""
SSH & 设备配置 API 模块
"""
import os
import time
import paramiko
from typing import Dict, List

from Common.Response import create_response


def add_license(device_ip: str, password: str) -> Dict:
    """远程增加鉴权文件"""
    USERNAME = "root"
    REMOTE_DIR = "/home/IMX477/client_for_camera"
    REMOTE_FILENAME = "LicenseGenerator"

    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local_path = os.path.join(script_dir, "Common", "LicenseGenerator")

    if not os.path.exists(local_path):
        return create_response(400, f"本地文件不存在 → {local_path}", False)

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=device_ip, username=USERNAME, password=password,
            timeout=15, allow_agent=False, look_for_keys=False
        )

        ssh.exec_command(f"mkdir -p {REMOTE_DIR}")

        sftp = ssh.open_sftp()
        sftp.put(local_path, f"{REMOTE_DIR}/{REMOTE_FILENAME}")
        sftp.close()

        ssh.exec_command(f"chmod 755 {REMOTE_DIR}/{REMOTE_FILENAME}")

        _, stdout, _ = ssh.exec_command(f"ls -l {REMOTE_DIR}/{REMOTE_FILENAME}")
        file_info = stdout.read().decode().strip()

        ssh.close()
        return create_response(200, f"传输成功！文件已部署到 {device_ip}", True, {
            "device_ip": device_ip,
            "file_info": file_info
        })

    except paramiko.AuthenticationException:
        return create_response(401, "认证失败：用户名或密码错误", False)
    except paramiko.SSHException as e:
        return create_response(500, f"SSH 连接失败: {e}", False)
    except Exception as e:
        return create_response(500, f"发生错误: {e}", False)


def batch_deploy(ip_list: List[str]) -> Dict:
    """批量部署SSH配置文件"""
    username = "root"
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local_ssh_py = os.path.join(script_dir, "Common", "ssh.py")
    remote_dir = "/home/shell"
    remote_ssh_py = f"{remote_dir}/ssh.py"

    if not os.path.exists(local_ssh_py):
        return create_response(400, f"本地文件 {local_ssh_py} 不存在", False)

    results = []
    success_count = 0
    fail_count = 0

    for i, ip in enumerate(ip_list, 1):
        ip = ip.strip()
        if not ip:
            continue

        try:
            last_octet = ip.split(".")[-1]
            password = f"gsm200818534.{last_octet}"
        except Exception:
            results.append({"ip": ip, "status": "失败", "detail": "IP 格式错误"})
            fail_count += 1
            continue

        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=ip, username=username, password=password,
                timeout=15, allow_agent=False, look_for_keys=False
            )

            ssh.exec_command(f"mkdir -p {remote_dir}")

            sftp = ssh.open_sftp()
            sftp.put(local_ssh_py, remote_ssh_py)
            sftp.close()

            for cmd in [
                "systemctl daemon-reexec",
                "systemctl daemon-reload",
                "systemctl enable ssh_client.service",
                "systemctl start ssh_client.service",
            ]:
                ssh.exec_command(cmd)

            _, stdout, _ = ssh.exec_command("systemctl status ssh_client.service --no-pager")
            status_output = stdout.read().decode().strip()

            if "Active: active (running)" in status_output or "Active: activating" in status_output:
                results.append({"ip": ip, "status": "成功", "detail": "服务运行中"})
                success_count += 1
            else:
                results.append({"ip": ip, "status": "失败", "detail": "服务未启动"})
                fail_count += 1

            ssh.close()
        except paramiko.AuthenticationException:
            results.append({"ip": ip, "status": "失败", "detail": "认证失败"})
            fail_count += 1
        except Exception as e:
            results.append({"ip": ip, "status": "失败", "detail": str(e)})
            fail_count += 1

        if i < len(ip_list):
            time.sleep(3)

    return create_response(200, f"批量部署完成！成功 {success_count} 台，失败 {fail_count} 台", True, {
        "total": len(ip_list),
        "success_count": success_count,
        "fail_count": fail_count,
        "results": results
    })


def add_duration(ip: str, password: str, device_sn: str) -> Dict:
    """添加运行时长服务"""
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname=ip, port=22, username='root', password=password, timeout=15)

        duration_script = f'''# 主要的功能是上传当前运行时间
import time, requests, uuid
from datetime import datetime
import signal, sys

SERVER_URL = "http://8.134.128.64:7690/api/static_time/"
DEVICE_SN = "{device_sn}"
INTERVAL = 60
REQUEST_TIMEOUT = 10

SESSION_UUID = str(uuid.uuid4())
start_monotonic = time.monotonic()
running = True

def graceful_exit(signum, frame):
    global running
    print("\\n收到退出信号，正在安全退出...")
    running = False

signal.signal(signal.SIGINT, graceful_exit)
signal.signal(signal.SIGTERM, graceful_exit)

def report():
    elapsed = int(time.monotonic() - start_monotonic)
    payload = {{"sn": DEVICE_SN, "uuid": SESSION_UUID, "runtime": elapsed}}
    try:
        resp = requests.post(SERVER_URL, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        print(f"[{{datetime.now():%H:%M:%S}}] 上报成功 | {{elapsed:,}}秒")
    except Exception as e:
        print(f"[{{datetime.now():%H:%M:%S}}] 上报失败: {{e}}")

def main():
    print("===== 运行时长上报服务启动 =====")
    print(f"设备 SN: {{DEVICE_SN}}")
    print(f"会话 UUID: {{SESSION_UUID}}")
    while running:
        time.sleep(INTERVAL)
        report()

if __name__ == "__main__":
    main()
'''

        def exec_cmd(cmd):
            ssh.exec_command(cmd)
            time.sleep(1)

        exec_cmd("mkdir -p /home/shell")
        exec_cmd(f'cat > /home/shell/duration_time.py << \'EOF\'\n{duration_script}\nEOF')
        exec_cmd("chmod +x /home/shell/duration_time.py")

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

[Install]
WantedBy=multi-user.target
'''

        exec_cmd(f'sudo sh -c \'cat > /etc/systemd/system/duration_time.service << \'EOF\'\n{service_content}\nEOF\'\'')
        exec_cmd("sudo systemctl daemon-reload")
        exec_cmd("sudo systemctl enable duration_time.service")
        exec_cmd("sudo systemctl start duration_time.service")

        ssh.close()
        return create_response(200, f"运行时长上报服务部署成功，SN: {device_sn}", True)

    except Exception as e:
        return create_response(500, f"部署失败: {str(e)}", False)


def control_duration(ip: str, password: str, enable: bool) -> Dict:
    """控制运行时长服务状态"""
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname=ip, port=22, username='root', password=password, timeout=15)

        def exec_cmd(cmd):
            ssh.exec_command(cmd)
            time.sleep(1)

        if enable:
            commands = [
                "sudo systemctl daemon-reload",
                "sudo systemctl enable duration_time.service",
                "sudo systemctl start duration_time.service",
            ]
            msg = "已启用开机自启"
        else:
            commands = [
                "sudo systemctl stop duration_time.service",
                "sudo systemctl disable duration_time.service",
            ]
            msg = "已禁用开机自启（服务已停止）"

        for cmd in commands:
            exec_cmd(cmd)

        ssh.close()
        return create_response(200, msg, True)

    except Exception as e:
        return create_response(500, f"操作失败: {str(e)}", False)


def quick_configuration(params: dict) -> Dict:
    """设备快速配置"""
    def update_progress(step_index, msg, success=True):
        pass  # 可扩展 Redis 进度推送

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            params['device_ip'],
            username=params.get('username', 'root'),
            password=params.get('password', 'gsm200818534'),
            timeout=15
        )

        def exec_cmd(cmd):
            ssh.exec_command(cmd)
            time.sleep(0.5)

        exec_cmd(f"sed -i 's/\"device_sn\"\\s*:\\s*\"[^\"]*\"/\"device_sn\": \"{params['device_sn_value']}\"/g' /home/client_for_camera/config.jsonc")
        exec_cmd(f"sed -i 's/name\\s*=\\s*\".*\"/name = \"{params['frpc_value']}\"/' /home/frp_0.61.1_linux_arm/frpc.toml && systemctl restart frpc.service")
        exec_cmd("rm -f /home/shell/ssh_config.py")
        exec_cmd(f"sed -i 's|DEVICE_SN\\s*=\\s*\".*\"|DEVICE_SN = \"{params['duration_sn']}\"|' /home/shell/duration_time.py")
        exec_cmd(f"sed -i 's|^command=.*|command={params['n2n_command']}|' /etc/supervisor/conf.d/n2n.conf")
        exec_cmd("sed -i 's/autostart=true/autostart=false/g' /etc/supervisor/conf.d/AutoScript.conf")
        exec_cmd("sed -i 's/autostart=true/autostart=false/g' /etc/supervisor/conf.d/captive_portal_setting.conf")
        exec_cmd("supervisorctl update && supervisorctl reread")

        ssh.close()
        return create_response(200, "配置任务已下发", True, {"status": "success"})

    except Exception as e:
        return create_response(500, f"配置失败: {str(e)}", False)
