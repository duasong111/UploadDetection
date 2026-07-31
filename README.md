# UploadDetection 设备上传检测系统

基于 FastAPI 的设备运行时长上报与管理系统，支持设备注册、运行时长统计、在线状态监控、AI 聊天、RAG 知识库等功能。

## 技术栈

- **后端框架**: FastAPI 0.109+ + Socket.IO
- **数据库**: PostgreSQL 15+
- **缓存**: Redis 7.x
- **向量数据库**: ChromaDB (RAG 知识库)
- **对象存储**: MinIO / RUSTFS (文件、头像、固件存储)
- **自动化运维**: Ansible (Playbook 批量部署)
- **AI 集成**: DeepSeek Chat + Tool Calling + RAG
- **文件上传**: 分片上传 + 断点续传 + ClamAV 病毒扫描
- **远程设备管理**: SSH + Paramiko + WebSocket Agent
- **容器化部署**: Docker + Uvicorn

## 功能特性

- ✅ 用户登录/注册 & 头像管理
- ✅ 设备运行时长上报 & 在线状态监控
- ✅ 设备列表管理 & 历史数据查询
- ✅ FRP 设备配置管理 & N2N 组网
- ✅ 远程 SSH 部署（授权文件、批量部署）
- ✅ Ansible 自动化运维（Playbook 批量执行）
- ✅ 运行时长统计 & 可视化
- ✅ 安全文件上传（分片上传、断点续传、ClamAV 病毒扫描）
- ✅ 固件管理（上传、下载、去重、版本管理）
- ✅ AI 智能聊天（Tool Calling / Function Call / Agent Loop）
- ✅ RAG 知识库（书籍 RAG + 设备知识库）
- ✅ WebSocket Agent 远程设备控制
- ✅ 用户操作频率限制（License 管理、全局速率控制）

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

1. 复制 `config.example.py` 为 `config.py`：
   ```bash
   cp config.example.py config.py
   ```

2. 修改 `config.py` 中的敏感配置（数据库、Redis、AI Key 等）

> ⚠️ **重要**: `config.py` 包含敏感信息，已被 `.gitignore` 排除，不会提交到 GitHub。

### 运行服务

```bash
# 开发模式
python app.py

# 生产模式（FastAPI + Socket.IO 是 ASGI 应用，用 uvicorn 启动）
uvicorn app:socket_app --host 0.0.0.0 --port 5000 --workers 1
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

> 容器内通过 `docker-entrypoint.sh` 一并启动了三个进程：
> 1. **uvicorn**（FastAPI + Socket.IO，ASGI 服务，端口 5000）
> 2. **设备上报消费者**（`python -m functions.device.device_report_consumer`，批量落库）
> 3. **AI 聊天消费者**（`python -m functions.ai.ai_chat_consumer`，DeepSeek Agent Loop）
>
> 日志查看：`docker logs -f uploaddetection-container`

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
├── app.py                    # FastAPI 主入口，纯路由层
├── config.example.py         # 配置文件模板（替代 config.py 提交到 Git）
├── requirements.txt          # 依赖列表
├── Dockerfile                # Docker 容器化部署
├── CLAUDE.md                 # AI 编码约束文档
│
├── functions/                # 业务逻辑模块（核心）
│   ├── user.py               # 用户注册/登录/改密
│   ├── avatar.py             # 用户头像上传（MinIO 对象存储）
│   ├── device_api.py         # 设备 API 管理
│   ├── device.py             # 设备数据处理
│   ├── device_query.py       # 设备高级查询
│   ├── frp_api.py            # FRP 代理配置 API
│   ├── frp.py                # FRP 核心业务逻辑（配置生成、推送到服务器）
│   ├── ssh_api.py            # SSH 远程操作（授权文件部署、批量部署）
│   ├── ssh_config.py         # SSH 配置管理（License 部署日志）
│   ├── ansible_tasks.py      # Ansible 自动化运维调度
│   ├── transmission.py       # 远程设备配置传输（N2N 组网、FRPC 部署）
│   ├── duration_stastic.py   # 运行时长统计（部署 duration_time 脚本）
│   │
│   ├── upload_manager.py     # 安全文件上传（分片上传、断点续传、病毒扫描）
│   ├── firmware.py           # 固件管理（上传/下载/去重/限流）
│   │
│   ├── ai_chat.py            # AI 智能聊天（流式响应 + Tool Calling）
│   ├── book_rag.py           # 书籍 RAG 知识库（PDF 切片 → ChromaDB 向量检索）
│   ├── rag_knowledge.py      # 设备知识库 RAG（TF-IDF + ChromaDB）
│   │
│   ├── check.py              # 密码强度校验
│   └── tools/                # AI Tool Calling 工具集
│       ├── devices_info_tools.py    # 设备信息查询工具（带 Redis 缓存）
│       ├── schema.py                # Tool Schema 统一构建器
│       └── tools_calling_export.py  # 工具统一导出
│
├── ansible/                  # Ansible 自动化运维
│   ├── ansible.cfg           # Ansible 配置
│   ├── inventory.ini         # 主机清单
│   ├── hosts_pass.txt        # 主机密码文件
│   └── playbooks/            # Playbook 剧本
│       ├── execute_command.yml     # 远程命令执行
│       ├── replace.yml             # 文件替换
│       └── systemd_service.yml     # Systemd 服务管理
│
├── Common/                   # 公共模块
│   ├── Response.py           # 统一响应封装
│   ├── ssh.py                # SSH 远程连接 2.0（WebSocket Agent）
│   └── duration_time.py      # 设备端运行时长上报脚本
│
├── database/                 # 数据库模块
│   ├── Postgresql.py         # PostgreSQL 连接池
│   └── operateFunction.py    # 数据库操作封装
│
├── chroma_data/              # ChromaDB 向量数据库存储（.gitignore 排除）
│
├── migrations/               # 数据库迁移脚本
│   ├── create_device_table.py       # 设备表创建
│   ├── config_setting.py            # 配置设置表
│   ├── 003_create_frp_device_tables.py  # FRP 设备表
│   └── 004_devices_revord.py        # 设备记录表
│
├── uploads/                  # 上传文件临时目录
│   ├── temp/                 # 分片上传临时存储
│   └── quarantine/           # ClamAV 隔离区
│
└── .github/                  # GitHub 工作流配置
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