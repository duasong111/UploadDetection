#!/bin/bash
# 容器启动脚本：同时启动 uvicorn + 设备上报消费者 + AI 聊天消费者
set -e

echo "[entrypoint] 启动 UploadDetection 服务..."

# 等待 RabbitMQ 可用（最多 60 秒）
if [ -n "$RABBITMQ_HOST" ] || [ -n "$RabbitMQ_HOST" ]; then
  echo "[entrypoint] 等待 RabbitMQ 就绪..."
  HOST="${RABBITMQ_HOST:-${RabbitMQ_HOST}}"
  PORT="${RABBITMQ_PORT:-5672}"
  for i in $(seq 1 60); do
    if python -c "
import socket, sys
try:
    socket.create_connection(('$HOST', $PORT), timeout=2).close()
    sys.exit(0)
except Exception:
    sys.exit(1)
"; then
      echo "[entrypoint] RabbitMQ 已就绪"
      break
    fi
    sleep 1
  done
fi

echo "[entrypoint] 启动设备上报消费者..."
python -m functions.device.device_report_consumer &
CONSUMER_DEVICE_PID=$!

echo "[entrypoint] 启动 AI 聊天消费者..."
python -m functions.ai.ai_chat_consumer &
CONSUMER_AI_PID=$!

# 优雅退出：容器停止时先停消费者再停 uvicorn
trap 'echo "[entrypoint] 停止服务..."; kill $CONSUMER_DEVICE_PID $CONSUMER_AI_PID 2>/dev/null || true; exit 0' SIGTERM SIGINT

echo "[entrypoint] 启动 uvicorn (ASGI)..."
exec uvicorn app:socket_app --host 0.0.0.0 --port 5000 --workers 1 --access-log --proxy-headers
