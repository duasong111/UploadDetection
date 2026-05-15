import websocket
import json
import time
import re
import os
import socket
import threading
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

# ==============================
# ssh 远程连接2.0
# ==============================
SERVER = "wss://connect.jyaitech.com/ws/remote_devices/"
SN_CONFIG_PATH = "/home/IMX477/client_for_camera/config.jsonc"
MAP_CONFIG_PATH = "/home/IMX477/client_for_camera/config_http.jsonc"
N2N_CONF_PATH = "/etc/supervisor/conf.d/n2n.conf"
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "intellicamera",
    "user": "postgres",
    "password": "gsm200818534"
}
TIMEOUT_SECONDS = 1200
LOG_PATH = "/var/log/agent.log"
POLL_INTERVAL = 1.0

# ==============================
# 日志配置
# ==============================
logger = logging.getLogger("WebSocketAgent")
logger.setLevel(logging.INFO)

handler = RotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

print = logger.info

console = logging.StreamHandler()
console.setFormatter(formatter)
logger.addHandler(console)

# ==============================
# 全局变量
# ==============================
ws_app = None
last_message_time = time.time()
timeout_thread = None
polling_thread = None
is_polling = False
last_log_time = None
target_map = {}


# ==============================
# 工具函数
# ==============================
def get_local_ip():
    """
    优先从 n2n supervisor 配置文件中读取 -a 10.10.X.X 参数作为本机 IP。
    fallback: socket 路由探测。
    """
    # ---- 策略1: 从 n2n 配置文件读取 -a 10.10.x.x ----
    try:
        if os.path.exists(N2N_CONF_PATH):
            with open(N2N_CONF_PATH, "r", encoding="utf-8") as f:
                content = f.read()
            match = re.search(r'-a\s+(10\.10\.\d+\.\d+)', content)
            if match:
                ip = match.group(1)
                print(f" 成功从 n2n 配置获取 IP: {ip}")
                return ip
            else:
                print(f" n2n 配置文件中未找到 10.10.x.x 格式的 IP")
        else:
            print(f" n2n 配置文件不存在: {N2N_CONF_PATH}")
    except Exception as e:
        print(f" 读取 n2n 配置失败: {e}")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                print(f" fallback: socket 路由探测 IP: {ip}")
                return ip
    except Exception as e:
        print(f" socket 路由探测失败: {e}")

    print(" 所有策略均失败，返回 0.0.0.0")
    return "0.0.0.0"


def load_sn_from_config():
    try:
        if not os.path.exists(SN_CONFIG_PATH):
            print(f" SN 配置文件不存在: {SN_CONFIG_PATH}")
            return "UNKNOWN_SN"
        with open(SN_CONFIG_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        content = re.sub(r'//.*', '', content)
        content = re.sub(r'/\*[\s\S]*?\*/', '', content)
        content = ''.join(ch for ch in content if ord(ch) >= 32 or ch in '\t\n\r')
        data = json.loads(content)
        sn = data.get("device_sn", "UNKNOWN_SN")
        print(f" 成功读取 SN: {sn}")
        return sn
    except Exception as e:
        print(f" 读取 SN 文件失败: {e}")
        return "UNKNOWN_SN"


def load_target_map():
    global target_map
    target_map = {}
    try:
        if not os.path.exists(MAP_CONFIG_PATH):
            print(f" 映射配置文件不存在: {MAP_CONFIG_PATH}")
            return
        with open(MAP_CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
        target_map = config.get("target_map", {})
        print(f" 成功加载 target_map: {target_map}")
    except Exception as e:
        print(f" 加载 target_map 失败: {e}")


def apply_target_mapping(raw_data: dict) -> dict:
    if not isinstance(raw_data, dict) or not target_map:
        return raw_data
    mapped_data = {}
    for key, value in raw_data.items():
        if key.startswith("target") and key in target_map:
            mapped_data[target_map[key]] = value
        else:
            mapped_data[key] = value
    return mapped_data


def filter_displacement(data: dict) -> dict:
    """过滤所有嵌套 displacement 字段，只保留前两个值（X、Y）"""
    for item in data.values():
        if isinstance(item, dict) and 'displacement' in item:
            displacement = item['displacement']
            if isinstance(displacement, list) and len(displacement) >= 2:
                item['displacement'] = displacement[:2]
    return data


# ==============================
# 数据库轮询推送循环（线程本地连接）
# ==============================
def polling_loop():
    global is_polling, last_log_time
    print(f" 数据轮询已启动（每 {POLL_INTERVAL} 秒检查新记录）")

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
        print(" 轮询线程数据库连接成功")

        while is_polling:
            if last_log_time is None:
                time.sleep(POLL_INTERVAL)
                continue

            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT uuid, data, log_time, position, synced 
                           FROM monitor_log 
                           WHERE log_time > %s 
                           ORDER BY log_time ASC""",
                        (last_log_time,)
                    )
                    records = cur.fetchall()

                if records:
                    new_last = max(r['log_time'] for r in records)
                    for record in records:
                        try:
                            raw_data = record['data']
                            if isinstance(raw_data, str):
                                raw_data = json.loads(raw_data)
                            elif not isinstance(raw_data, dict):
                                raise ValueError(f"Invalid data type: {type(raw_data)}")

                            mapped_data = apply_target_mapping(raw_data)
                            filtered_data = filter_displacement(mapped_data)

                            msg = {
                                "uuid": str(record['uuid']),
                                "data": filtered_data,
                                "log_time": record['log_time'].isoformat(),
                                "position": record['position'],
                                "synced": record['synced']
                            }
                            ws_app.send(json.dumps(msg, ensure_ascii=False))
                            print(
                                f" 已推送记录: {json.dumps(filtered_data, ensure_ascii=False)} (uuid={msg['uuid']}, log_time={msg['log_time']})")
                        except Exception as e:
                            print(f" 处理并推送记录失败: {e}")
                    last_log_time = new_last
            except Exception as e:
                print(f" 轮询数据库出错: {e}")

            time.sleep(POLL_INTERVAL)
    except Exception as e:
        print(f" 轮询线程数据库连接失败: {e}")
    finally:
        if conn:
            conn.close()
            print(" 轮询线程数据库连接已关闭")
    print(" 数据轮询已停止")


# ==============================
# WebSocket 回调函数
# ==============================
def start_timeout_monitor(ws):
    global last_message_time
    while True:
        time.sleep(10)
        if time.time() - last_message_time > TIMEOUT_SECONDS:
            print(" 超过20分钟未收到服务器响应，主动断开连接")
            try:
                ws.close()
            except Exception as e:
                print(" 关闭连接出错:", e)
            break


def on_message(ws, message):
    global last_message_time, is_polling, polling_thread
    last_message_time = time.time()
    print(f" 收到来自服务器的消息: {message}")

    command = None
    if message.strip() in ["start_monitoring", "stop_monitoring"]:
        command = message.strip()
    else:
        try:
            data = json.loads(message)
            command = data.get("command")
        except json.JSONDecodeError:
            print(" 非JSON消息且非有效命令，忽略")
            return

    if not command:
        print(f" 消息无有效 command 字段，忽略: {message}")
        return

    if command == "start_monitoring":
        if not is_polling:
            is_polling = True
            polling_thread = threading.Thread(target=polling_loop, daemon=True)
            polling_thread.start()
            print(" 已启动数据轮询推送")
        else:
            print(" 数据轮询已在运行")
    elif command == "stop_monitoring":
        if is_polling:
            is_polling = False
            if polling_thread:
                polling_thread.join(timeout=5)
            print(" 已停止数据轮询推送")
        else:
            print(" 数据轮询未运行")


def on_open(ws):
    global last_message_time, timeout_thread, last_log_time

    sn = load_sn_from_config()
    ip = get_local_ip()
    load_target_map()

    # 临时连接，仅用于获取最新 log_time
    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
        print(" 主线程数据库连接成功（获取初始 log_time）")
        with conn.cursor() as cur:
            cur.execute("SELECT log_time FROM monitor_log ORDER BY log_time DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                last_log_time = row['log_time']
                print(f" 获取最新 log_time: {last_log_time}")
            else:
                print(" 数据库无记录，将从头开始推送")
                last_log_time = datetime.min
    except Exception as e:
        print(f" 获取最新 log_time 失败: {e}")
        last_log_time = datetime.min
    finally:
        if conn:
            conn.close()

    print(f" 连接到服务器成功: {SERVER}")
    print(f" SN={sn}  IP={ip}")

    ws.send(json.dumps({"hello": "raspberrypi", "sn": sn, "ip": ip}))
    last_message_time = time.time()

    timeout_thread = threading.Thread(target=start_timeout_monitor, args=(ws,), daemon=True)
    timeout_thread.start()


def on_error(ws, error):
    print(" WebSocket 错误:", error)


def on_close(ws, code, reason):
    global is_polling
    print(f" 连接关闭: {code} {reason}")
    is_polling = False
    if polling_thread:
        polling_thread.join(timeout=2)
    print(" 3秒后尝试重连...")
    time.sleep(3)
    run()


def run():
    global ws_app
    print(" 树莓派 WebSocket Agent（生产版）启动中...")
    ws_app = websocket.WebSocketApp(
        SERVER,
        on_message=on_message,
        on_open=on_open,
        on_error=on_error,
        on_close=on_close,
    )
    ws_app.run_forever(ping_interval=30, ping_timeout=10)


if __name__ == "__main__":
    run()
