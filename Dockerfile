FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    pkg-config \
    default-libmysqlclient-dev \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
RUN pip config set global.trusted-host mirrors.aliyun.com

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

EXPOSE 5000

# 容器启动脚本：uvicorn + 设备上报消费者 + AI 聊天消费者 一并启动
# （FastAPI + Socket.IO 是 ASGI 应用，用 uvicorn；单 worker 保证 Socket.IO 稳定）
RUN chmod +x /app/docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]