# 主要的功能是上传当前运行时间，单独再服务器上运行
import time
import requests
import uuid
from datetime import datetime
import signal
import sys

# ==================== 配置 ====================
SERVER_URL = "http://8.134.128.64:7690/api/static_time/"  # ← 改为实际地址
DEVICE_SN = "YA_GY_3"                                 # ← 固定填写
INTERVAL    = 60          # 上报间隔（秒）
REQUEST_TIMEOUT = 10       # 秒
# =============================================

# 每次开机生成全新 uuid，代表一次上电会话
SESSION_UUID = str(uuid.uuid4())

start_monotonic = time.monotonic()
start_time_str  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

running = True


def graceful_exit(signum, frame):
    global running
    print("\n收到退出信号，正在安全退出...")
    running = False


signal.signal(signal.SIGINT, graceful_exit)
signal.signal(signal.SIGTERM, graceful_exit)


def report():
    elapsed = int(time.monotonic() - start_monotonic)

    payload = {
        "sn": DEVICE_SN,
        "uuid": SESSION_UUID,
        "runtime": elapsed
    }

    try:
        resp = requests.post(
            SERVER_URL,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()

        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
              f"上报成功 | 已运行 {elapsed:,} 秒 | "
              f"本次会话峰值 {data.get('session_max_runtime', elapsed):,} 秒")

    except requests.RequestException as e:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 上报失败: {e}")


def main():
    print("===== 运行时长上报服务启动 =====")
    print(f"启动时刻     : {start_time_str}")
    print(f"设备 SN       : {DEVICE_SN}")
    print(f"本次会话 UUID : {SESSION_UUID}")
    print(f"上报间隔      : {INTERVAL} 秒")
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
        print(f"主循环异常退出: {e}", file=sys.stderr)
        sys.exit(1)
