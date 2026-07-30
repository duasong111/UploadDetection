from flask import request
from flask.views import MethodView
from http import HTTPStatus
from Common.Response import create_response
import os
import paramiko
import time
from datetime import datetime

from database.Postgresql import get_postgres_connection

USERNAME = "root"
REMOTE_DIR = "/home/IMX477/client_for_camera"
REMOTE_FILENAME = "LicenseGenerator"


def save_license_log(device_ip, success, message, file_info=None):
    """保存授权文件部署日志到数据库"""
    conn = None
    try:
        conn = get_postgres_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO license_deploy_log (device_ip, success, message, file_info)
                VALUES (%s, %s, %s, %s)
            """, (device_ip, success, message, file_info))
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        print(f" 保存授权日志失败: {e}")
    finally:
        if conn:
            conn.close()


def save_batch_deploy_log(total_count, success_count, fail_count, results):
    """保存批量部署日志到数据库"""
    conn = None
    try:
        conn = get_postgres_connection()
        with conn.cursor() as cur:
            # 先插入批量部署主记录
            cur.execute("""
                INSERT INTO batch_deploy_log (total_count, success_count, fail_count)
                VALUES (%s, %s, %s)
                RETURNING id
            """, (total_count, success_count, fail_count))
            batch_id = cur.fetchone()[0]
            
            # 插入每条设备的详情记录
            for result in results:
                success = result['status'] == '成功'
                cur.execute("""
                    INSERT INTO batch_deploy_detail (batch_id, device_ip, success, detail)
                    VALUES (%s, %s, %s, %s)
                """, (batch_id, result['ip'], success, result['detail']))
        
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


def upload_license(device_ip, password):
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local_path = os.path.join(script_dir, "Common", "LicenseGenerator")

    if not os.path.exists(local_path):
        error_msg = f"本地文件不存在 → {local_path}"
        return {"success": False, "message": error_msg}

    remote_path = f"{REMOTE_DIR}/{REMOTE_FILENAME}"

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh.connect(
            hostname=device_ip,
            username=USERNAME,
            password=password,
            timeout=15,
            allow_agent=False,
            look_for_keys=False
        )

        ssh.exec_command(f"mkdir -p {REMOTE_DIR}")

        sftp = ssh.open_sftp()
        sftp.put(local_path, remote_path)
        sftp.close()

        ssh.exec_command(f"chmod 755 {remote_path}")

        stdin, stdout, stderr = ssh.exec_command(f"ls -l {remote_path}")
        file_info = stdout.read().decode().strip()

        success_msg = f"传输成功！文件已部署到 {device_ip}:{remote_path}"
        return {"success": True, "message": success_msg, "file_info": file_info}

    except paramiko.AuthenticationException:
        error_msg = "认证失败：用户名或密码错误"
        return {"success": False, "message": error_msg}
    except paramiko.SSHException as e:
        error_msg = f"SSH 连接失败: {e}"
        return {"success": False, "message": error_msg}
    except Exception as e:
        error_msg = f"发生错误: {e}"
        return {"success": False, "message": error_msg}
    finally:
        ssh.close()


def batch_deploy_ssh(ip_list):
    """批量部署SSH配置文件到多台设备"""
    username = "root"
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local_ssh_py = os.path.join(script_dir, "Common", "ssh.py")
    remote_dir = "/home/shell"
    remote_ssh_py = f"{remote_dir}/ssh.py"
    service_path = "/etc/systemd/system/ssh_client.service"

    service_content = """[Unit]
        Description=SSH Persistent Client
        After=network-online.target
        Wants=network-online.target
        
        [Service]
        Type=simple
        User=root
        WorkingDirectory=/home/shell
        ExecStart=/home/venv/bin/python /home/shell/ssh.py
        Restart=always
        RestartSec=5
        Environment=PYTHONUNBUFFERED=1
        
        [Install]
        WantedBy=multi-user.target
        """

    if not os.path.exists(local_ssh_py):
        return {"success": False, "message": f"本地文件 {local_ssh_py} 不存在"}

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
            results.append({
                "ip": ip,
                "status": "失败",
                "detail": "IP 格式错误"
            })
            fail_count += 1
            time.sleep(3)
            continue

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            ssh.connect(
                hostname=ip,
                username=username,
                password=password,
                timeout=15,
                allow_agent=False,
                look_for_keys=False
            )

            ssh.exec_command(f"mkdir -p {remote_dir}")

            sftp = ssh.open_sftp()
            sftp.put(local_ssh_py, remote_ssh_py)
            sftp.close()

            cat_cmd = f'''cat > {service_path} << 'EOF'
            {service_content}
            EOF'''
            ssh.exec_command(cat_cmd)

            commands = [
                "systemctl daemon-reexec",
                "systemctl daemon-reload",
                "systemctl enable ssh_client.service",
                "systemctl start ssh_client.service",
            ]
            for cmd in commands:
                ssh.exec_command(cmd)

            _, stdout, _ = ssh.exec_command("systemctl status ssh_client.service --no-pager")
            status_output = stdout.read().decode().strip()

            if "Active: active (running)" in status_output:
                results.append({
                    "ip": ip,
                    "status": "成功",
                    "detail": "Active: active (running)"
                })
                success_count += 1
            elif "Active: activating" in status_output:
                results.append({
                    "ip": ip,
                    "status": "成功",
                    "detail": "正在启动中 (activating)"
                })
                success_count += 1
            else:
                error_info = " | ".join(status_output.split("\n")[-5:]) if status_output else "无状态输出"
                results.append({
                    "ip": ip,
                    "status": "失败",
                    "detail": f"服务未启动 - {error_info}"
                })
                fail_count += 1

        except paramiko.AuthenticationException:
            results.append({
                "ip": ip,
                "status": "失败",
                "detail": "认证失败（密码错误）"
            })
            fail_count += 1
        except paramiko.SSHException as e:
            results.append({
                "ip": ip,
                "status": "失败",
                "detail": f"SSH 连接错误: {e}"
            })
            fail_count += 1
        except Exception as e:
            results.append({
                "ip": ip,
                "status": "失败",
                "detail": f"未知错误: {e}"
            })
            fail_count += 1
        finally:
            ssh.close()

        if i < len(ip_list):
            time.sleep(3)

    return {
        "success": True,
        "total": len(ip_list),
        "success_count": success_count,
        "fail_count": fail_count,
        "results": results
    }


class AddLicenseView(MethodView):
    """远程增加鉴权文件接口（POST）"""
    def post(self):
        try:
            data = request.get_json()
            device_ip = data.get('device_ip')
            password = data.get('password')
            if not device_ip:
                return create_response(
                    HTTPStatus.BAD_REQUEST,
                    "缺少必要参数：device_ip",
                    False
                )
            if not password:
                return create_response(
                    HTTPStatus.BAD_REQUEST,
                    "缺少必要参数：password",
                    False
                )
            
            result = upload_license(device_ip, password)
            
            # 保存到数据库
            save_license_log(device_ip, result['success'], result['message'], result.get('file_info'))
            
            if result['success']:
                return create_response(
                    HTTPStatus.OK,
                    result['message'],
                    True,
                    data={
                        "device_ip": device_ip,
                        "file_info": result.get('file_info', '')
                    }
                )
            else:
                return create_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    result['message'],
                    False
                )
        except Exception as e:
            return create_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"服务器错误: {str(e)}",
                False
            )


class BatchDeployView(MethodView):
    """批量部署SSH配置文件接口（POST）"""
    def post(self):
        try:
            data = request.get_json()
            ip_list = data.get('ip_list')
            
            if not ip_list:
                return create_response(
                    HTTPStatus.BAD_REQUEST,
                    "缺少必要参数：ip_list",
                    False
                )
            
            if not isinstance(ip_list, list):
                return create_response(
                    HTTPStatus.BAD_REQUEST,
                    "ip_list 必须是数组格式",
                    False
                )
            
            if len(ip_list) == 0:
                return create_response(
                    HTTPStatus.BAD_REQUEST,
                    "ip_list 不能为空",
                    False
                )
            
            result = batch_deploy_ssh(ip_list)
            
            # 保存到数据库
            save_batch_deploy_log(result['total'], result['success_count'], result['fail_count'], result['results'])
            
            return create_response(
                HTTPStatus.OK,
                f"批量部署完成！成功 {result['success_count']} 台，失败 {result['fail_count']} 台",
                True,
                data=result
            )
            
        except Exception as e:
            return create_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"服务器错误: {str(e)}",
                False
            )