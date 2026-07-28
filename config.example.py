# 配置有关的静态文件
# 正式环境配置
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'upload_detection',
        'USER': 'YOUR_DB_USER',
        'PASSWORD': 'YOUR_DB_PASSWORD',
        'HOST': 'YOUR_DB_HOST',
        'PORT': '5432',
    }
}
# 查询数据的信息
DB_CONFIG = {
    'host': 'YOUR_DB_HOST',
    'port': '5432',
    'dbname': 'hawkair',
    'user': 'YOUR_DB_USER',
    'password': 'YOUR_DB_PASSWORD',
}

# 开发环境配置
# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.postgresql',
#         'NAME': 'intellicamera',
#         'USER': 'postgres',
#         'PASSWORD': 'gsm200818534',
#         'HOST': '10.1.1.136',
#         'PORT': '5432',
#     }
# }

securityCode = "rewcef10fSd08FDS3ADVTSSA"
CODE_ERROR = 400
CODE_SUCCESS = 200

# 正式环境配置
# REDIS_HOST = "1Panel-redis-vqLD"
# REDIS_PORT = 6379
# REDIS_PASSWORD = "redis_TbbK2F"
# REDIS_DB = 7


# 开发环境配置
REDIS_HOST = "10.1.1.136"
REDIS_PORT = 6379
REDIS_PASSWORD = "YOUR_REDIS_PASSWORD"
REDIS_DB = 7

REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"

# 服务器代理
FRPS_IP = "YOUR_FRPS_IP"
FRPS_PORT = 6000
MAX_WORKERS = 20
CONNECTION_TIMEOUT = 15
COMMAND_TIMEOUT = 15
CONFIG_FILE = "Common/config_frp.txt"

# RUSTFS 设置桶
BUCKET_IP = "YOUR_BUCKET_IP"
BUCKET_PORT = "9000"
RUSTFS_BUCKET_NAME = "rustfsadmin"
RUSTFS_SECRET = "YOUR_RUSTFS_SECRET"

# 用户头像桶（需要新建桶或在 RUSTFS 控制台创建）
AVATAR_BUCKET_NAME = "avatar"

# 书籍存储桶
BOOK_BUCKET_NAME = "books"

# AI AGENT模型的Key
AI_AGENT_KEY = 'YOUR_AI_AGENT_KEY'

# ==================== 文件上传配置 ====================

# 上传文件桶
UPLOAD_BUCKET_NAME = "uploads"

# 文件大小限制 (30MB)
UPLOAD_MAX_SIZE = 30 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 1 * 1024 * 1024  # 1MB/分片

# 允许上传的文件扩展名
ALLOWED_UPLOAD_EXTENSIONS = {
    '.sh', '.py', '.yaml', '.yml', '.toml', '.conf',
    '.bin', '.txt', '.json', '.ini', '.service', '.j2',
    '',  # 无后缀文件（Linux 可执行文件，如 AutoScript）
}

# ClamAV 病毒扫描
CLAMAV_ENABLED = True
CLAMAV_HOST = "172.18.0.6"   # Docker ClamAV 容器 IP
CLAMAV_PORT = 3310           # clamd TCP 端口
CLAMAV_TIMEOUT = 60
