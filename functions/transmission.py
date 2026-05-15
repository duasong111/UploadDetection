import threading
import paramiko
import json
import time
from flask import request
from flask.views import MethodView
from http import HTTPStatus
from Common.Response import create_response
from database.operateFunction import execuFunction
from datetime import datetime
import redis
from config import REDIS_URL

redis_client = redis.from_url(REDIS_URL, decode_responses=True)

class Configuration(MethodView):
    def post(self):
        """接口入口"""
        try:
            data = request.get_json() or {}
            # 获取前端参数
            params = {
                "frpc_value": data.get('frpc_value'),
                "device_sn_value": data.get('device_sn'),
                "duration_sn": data.get('duration_sn'),
                "device_ip": data.get('device_ip'),
                "username": data.get('username', 'root'),
                "password": data.get('password','gsm200818534'),
                "n2n_command": data.get('n2n_command'),
                "ping_ip": data.get('ping_ip', '10.10.10.11'),
                "operator": data.get('operator', 'system_admin')
            }
            log_id = self._insert_initial_log(params)

            thread = threading.Thread(target=self.async_execute_task, args=(params, log_id))
            thread.start()
            return create_response(HTTPStatus.OK, "配置任务已下发", {"log_id": log_id})

        except Exception as e:
            return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"任务启动失败: {str(e)}", False)

    def _insert_initial_log(self, p):
        """在数据库记录初始状态并获取 ID"""
        sql = """
            INSERT INTO device_config_log (device_sn, device_ip, operator, status, config_details, step_results)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """
        initial_steps = json.dumps([False] * 9)
        config_json = json.dumps(p)
        res = execuFunction(sql, (
        p['device_sn_value'], p['device_ip'], p['operator'], 'running', config_json, initial_steps))
        return res[0][0] if res else None

    def async_execute_task(self, p, log_id):
        """异步执行逻辑：包含 9 个步骤"""
        steps_status = [False] * 9
        full_output = []
        redis_key = f"config_live_log:{log_id}"

        def update_progress(step_index, msg, success=True):
            steps_status[step_index] = success
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            formatted_msg = f"[{timestamp}] {msg}"
            full_output.append(formatted_msg)
            # 实时推送到 Redis (保留 1 小时)
            progress_data = {"steps": steps_status, "latest_msg": formatted_msg}
            redis_client.setex(redis_key, 3600, json.dumps(progress_data))

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            # Step 0: SSH 连接
            update_progress(0, f"正在连接设备 {p['device_ip']}...")
            ssh.connect(p['device_ip'], username=p['username'], password=p['password'], timeout=15)
            update_progress(0, "✓ SSH 连接成功", True)

            # Step 1: 更新 config.jsonc
            update_progress(1, "1. 更新 config.jsonc 中的 device_sn...")
            ssh.exec_command(
                f"sed -i 's/\"device_sn\"\\s*:\\s*\"[^\"]*\"/\"device_sn\": \"{p['device_sn_value']}\"/g' /home/client_for_camera/config.jsonc")
            update_progress(1, "✓ config.jsonc 更新完成")

            # Step 2: 更新 frpc.toml 并重启
            update_progress(2, "2. 更新 frpc.toml 并重启服务...")
            frp_cmd = f"sed -i 's/name\\s*=\\s*\".*\"/name = \"{p['frpc_value']}\"/' /home/frp_0.61.1_linux_arm/frpc.toml && systemctl restart frpc.service"
            ssh.exec_command(frp_cmd)
            update_progress(2, f"✓ frpc 重启完成 (Name: {p['frpc_value']})")

            # Step 3: 删除 ssh_config.py
            update_progress(3, "3. 删除 ssh_config.py...")
            ssh.exec_command("rm -f /home/shell/ssh_config.py")
            update_progress(3, "✓ ssh_config.py 已清理")

            # Step 4: 更新 duration_time.py
            update_progress(4, "4. 更新 duration_time.py (SERVER_URL + DEVICE_SN)...")
            duration_cmd = f"sed -i 's|DEVICE_SN\\s*=\\s*\".*\"|DEVICE_SN = \"{p['duration_sn']}\"|' /home/shell/duration_time.py"
            ssh.exec_command(duration_cmd)
            update_progress(4, "✓ duration_time.py 更新完成")

            # Step 5: 更新 n2n.conf
            update_progress(5, "5. 更新 n2n.conf...")
            ssh.exec_command(f"sed -i 's|^command=.*|command={p['n2n_command']}|' /etc/supervisor/conf.d/n2n.conf")
            update_progress(5, "✓ n2n.conf 更新完成")

            # Step 6 & 7: 修改 Supervisor 配置
            update_progress(6, "6. 修改 AutoScript.conf...")
            ssh.exec_command("sed -i 's/autostart=true/autostart=false/g' /etc/supervisor/conf.d/AutoScript.conf")
            update_progress(6, "✓ AutoScript 设置为非自启")

            update_progress(7, "7. 修改 captive_portal_setting.conf...")
            ssh.exec_command(
                "sed -i 's/autostart=true/autostart=false/g' /etc/supervisor/conf.d/captive_portal_setting.conf")
            update_progress(7, "✓ captive_portal 设置为非自启")

            # Step 8: 重载 Supervisor
            update_progress(8, "8. 重载 Supervisor 并测试网络...")
            ssh.exec_command("supervisorctl update && supervisorctl reread")
            # 最后的 Ping 测试
            stdin, stdout, stderr = ssh.exec_command(f"ping -c 2 {p['ping_ip']} | tail -n 2")
            ping_res = stdout.read().decode().strip()
            update_progress(8, f"✓ 配置完成。网络测试: {ping_res}")

            final_status = 'success'

        except Exception as e:
            final_status = 'failed'
            update_progress(min(sum(steps_status), 8), f"✗ 发生错误: {str(e)}", False)
        finally:
            ssh.close()
            # 任务结束，更新数据库
            update_sql = """
                UPDATE device_config_log 
                SET status=%s, full_log=%s, step_results=%s, finish_time=%s 
                WHERE id=%s
            """
            execuFunction(update_sql,
                          (final_status, "\n".join(full_output), json.dumps(steps_status), datetime.now(), log_id))