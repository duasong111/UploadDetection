# ==================== 第一阶段：构建依赖 ====================
FROM python:3.12-slim AS builder

WORKDIR /app

# 安装编译依赖（仅用于编译 Python 包）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    pkg-config \
    default-libmysqlclient-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists*

# 升级 pip 并配置阿里镜像
RUN pip install --upgrade pip && \
    pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip config set global.trusted-host mirrors.aliyun.com

# 预先安装依赖（分层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ==================== 第二阶段：运行镜像 ====================
FROM python:3.12-slim

WORKDIR /app

# 仅安装运行时必需的库（去掉 libgl1 libglib2.0-0 等无依赖）
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists*

# 从 builder 复制已安装的包
COPY --from=builder /install /usr/local

# 复制应用代码
COPY . .

# 环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1

EXPOSE 5000

# 启动脚本
RUN chmod +x /app/docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]
