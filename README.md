# UploadDetection 设备上传检测系统

基于 Flask 的设备运行时长上报与管理系统，支持设备注册、运行时长统计、在线状态监控等功能。

## 技术栈

- **后端框架**: Flask 3.x
- **数据库**: PostgreSQL 15+
- **缓存**: Redis 7.x
- **部署**: Docker + Gunicorn
- **其他**: Flask-CORS, Flask-SocketIO, psycopg2, bcrypt, paramiko

## 功能特性

- ✅ 用户登录/注册
- ✅ 设备运行时长上报
- ✅ 设备在线状态监控
- ✅ 设备列表管理
- ✅ FRP 设备配置管理
- ✅ 运行时长统计

## 快速开始

### 环境要求

- Python 3.12+
- PostgreSQL 15+
- Redis 7+

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置文件

修改 `config.py` 文件，配置数据库和 Redis 连接：

```python
# 数据库配置
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'postgres',
        'USER': 'postgres',
        'PASSWORD': 'your_password',
        'HOST': '10.1.1.127',
        'PORT': '5432',
    }
}

# Redis 配置
REDIS_HOST = "10.1.1.197"
REDIS_PORT = 6379
REDIS_PASSWORD = "your_password"
REDIS_DB = 5
REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
```

### 运行服务

```bash
# 开发模式
python app.py

# 生产模式
gunicorn app:app --bind 0.0.0.0:5000 --workers 3
```

## Docker 部署

### 构建镜像

```bash
# 构建镜像
docker build -t uploaddetection .

# 查看镜像
docker images | findstr uploaddetection

# 导出镜像
docker save uploaddetection:latest -o uploaddetection.tar

# 清理无用镜像
docker image prune -f
```

### 运行容器

```bash
docker run -d -p 5000:5000 --name uploaddetection-container uploaddetection
```

## API 接口

### 1. 用户登录

**POST** `/api/login/`

请求体：
```json
{
    "username": "admin",
    "password": "password"
}
```

响应：
```json
{
    "status_code": 200,
    "message": "登录成功",
    "success": true,
    "data": {
        "token": "abc123"
    }
}
```

### 2. 设备运行时长上报

**POST** `/api/static_time/`

请求体：
```json
{
    "sn": "device_serial_number",
    "uuid": "session_uuid",
    "runtime": 3600
}
```

响应：
```json
{
    "status_code": 200,
    "message": "上报成功",
    "success": true,
    "data": {
        "status": "ok",
        "session_max_runtime": 3600,
        "session_first_report": "2024-01-01T00:00:00",
        "session_last_report": "2024-01-01T01:00:00"
    }
}
```

### 3. 设备在线历史查询

**POST** `/api/device_online_history/`

请求体：
```json
{
    "sn": "device_serial_number"
}
```

### 4. FRP设备运行时长查询

**POST** `/api/device_uptime/`

请求体：
```json
{
    "sn": "device_serial_number"
}
```

## 项目结构

```
├── app.py                 # 主应用入口
├── config.py              # 配置文件
├── requirements.txt       # 依赖列表
├── Dockerfile             # Docker 配置
├── database/              # 数据库模块
│   ├── Postgresql.py      # PostgreSQL 连接
│   └── operateFunction.py # 数据库操作封装
├── functions/             # 业务逻辑模块
│   ├── user.py            # 用户相关功能
│   ├── device.py          # 设备相关功能
│   ├── frp.py             # FRP 相关功能
│   └── check.py           # 密码校验
├── Common/                # 公共模块
│   └── Response.py        # 统一响应封装
└── migrations/            # 数据库迁移脚本
```

## 数据库表结构

### device 表
- id: 主键
- sn: 设备序列号（唯一）
- created_at: 创建时间

### device_run_session 表
- id: 主键
- device_id: 设备ID（外键）
- uuid: 会话UUID
- first_report_time: 首次上报时间
- last_report_time: 最后上报时间
- max_runtime_seconds: 最大运行时长（秒）
- created_at: 创建时间

## 注意事项

1. 首次部署需要创建数据库表，可运行 `migrations/` 目录下的脚本
2. Redis 作为缓存使用，写入失败不影响主流程
3. 设备运行时长上报采用 UPSERT 模式，自动去重更新
4. 建议使用 Docker 进行生产环境部署

## License

MIT License