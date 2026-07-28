# CLAUDE.md - AI 编码约束

## 项目概述

UploadDetection 是一个基于 FastAPI 的设备运行时长上报与管理系统，支持设备注册、运行时长统计、在线状态监控、AI 聊天、RAG 知识库查询等功能。

## 技术栈

- **后端框架**: FastAPI 0.109+ + Socket.IO
- **数据库**: PostgreSQL 15+
- **缓存**: Redis 7.x
- **向量数据库**: ChromaDB
- **部署**: Docker + Uvicorn

## 开发约束

### 1. 敏感信息处理（强制）

- **`config.py` 是敏感文件**，包含数据库密码、Redis 密码、AI API Key 等
- **永远不要**在代码中硬编码敏感信息（密码、API Key、Token 等）
- 所有敏感配置必须从 `config.py` 读取，或使用环境变量
- 如果需要测试配置，使用 `config.example.py` 作为模板

### 2. Git 提交规范

- **禁止**将 `config.py` 提交到 Git
- **禁止**将 `.env`, `*.env`, `config.local.py` 提交到 Git
- 敏感文件已配置在 `.gitignore` 中，提交前检查 `git status`
- 提交前确保没有打印敏感信息到日志或响应中

### 3. API 安全

- 所有 API 响应避免暴露内部错误细节
- 用户密码必须加密存储（bcrypt）
- API 认证 Token 不得明文出现在日志中
- CORS 配置生产环境应指定具体域名，不使用 `*`

### 4. 数据库操作

- 使用参数化查询，防止 SQL 注入
- 批量操作注意事务处理
- 敏感字段（如密码）在查询结果中应屏蔽

### 5. 文件上传安全

- 严格校验文件扩展名白名单（见 `config.py` 中的 `ALLOWED_UPLOAD_EXTENSIONS`）
- 文件大小限制在 30MB 以内
- 上传文件应进行病毒扫描（ClamAV）

### 6. AI 功能约束

- AI 聊天响应需过滤敏感信息
- RAG 知识库检索结果需校验权限
- AI Agent Key 不得出现在前端代码或日志中

## 项目结构说明

```
app.py                 # FastAPI 主入口，纯路由层
config.example.py      # 配置模板（git 可追踪）
config.py              # 实际配置（git 忽略）
functions/             # 业务逻辑模块
  - user.py            # 用户注册/登录
  - device_api.py      # 设备管理
  - frp_api.py         # FRP 配置管理
  - ssh_api.py         # SSH 远程部署
  - ai_chat.py         # AI 聊天（流式响应）
  - book_rag.py        # 书籍 RAG 知识库
  - firmware.py        # 固件管理
Common/                # 公共模块
database/              # 数据库操作封装
chroma_data/           # ChromaDB 向量数据（git 忽略）
```

## 代码风格

- 使用中文注释（项目主要面向中文用户）
- FastAPI 路由函数放在 `app.py`，业务逻辑放在 `functions/`
- 统一响应格式使用 `Common/Response.py` 中的封装
- 异步优先，使用 `async/await`

## 常用命令

```bash
# 安装依赖
pip install -r requirements.txt

# 开发模式运行
python app.py

# Docker 构建
docker build -t uploaddetection .
docker run -d -p 5000:5000 uploaddetection
```
