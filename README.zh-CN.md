# Taurus Backend

Taurus Backend 是 Taurus Stack 平台的核心 API 服务，基于 Django 4.2 和 dvadmin 框架构建。提供主机管理、工作流执行、任务调度、程序部署和集中运维等能力。

## 功能特性

- **主机管理**: 主机注册、审批、心跳监控和生命周期管理
- **工作流引擎**: 多步骤工作流创建、执行和跟踪
- **任务调度**: 基于 Cron 的定时任务与执行历史
- **程序部署**: 远程程序安装、版本管理和策略分发
- **Supervisor 集成**: 主机守护进程通信、指令下发和状态同步
- **证书管理**: mTLS 客户端证书签发、吊销和 CRL 管理
- **运维中心**: 远程命令执行、脚本部署和文件传输
- **RBAC 权限**: 基于角色的访问控制，支持菜单、按钮和字段级权限
- **API 文档**: 通过 drf-spectacular 自动生成 Swagger/Redoc 文档

## 技术栈

| 组件 | 技术 |
|------|------|
| 框架 | Django 4.2 + Django REST Framework |
| 管理后台 | dvadmin（内置 RBAC、菜单、字典） |
| 数据库 | MySQL（生产）、SQLite（开发） |
| 缓存/消息代理 | Redis |
| 异步任务 | Celery + dvadmin-celery |
| WebSocket | Channels + Uvicorn |
| 鉴权 | JWT（djangorestframework-simplejwt） |
| API 文档 | drf-spectacular |
| 证书 | OpenSSL（mTLS） |
| 包管理 | Poetry |
| Python 版本 | 3.12.x |

## 项目结构

```
taurus-backend/
├── application/          # Django 项目配置（settings、urls、celery、ws）
├── conf/                 # 环境配置（env.py、env.example.py）
├── taurus/               # Taurus 核心应用
│   ├── models.py         # 数据模型（Host、Task、Workflow、Program 等）
│   ├── views.py          # ViewSet 和 API 端点
│   ├── urls.py           # API 路由（前缀：api/taurus/）
│   ├── serializers.py    # 请求/响应序列化器
│   ├── tasks.py          # Celery 异步任务
│   ├── websocket_async.py# WebSocket 异步处理器
│   ├── sdk/              # 内部 SDK（gRPC 客户端等）
│   ├── utils/            # 工具类（鉴权、gRPC 等）
│   ├── scripts/          # 部署脚本
│   └── management/       # Django 管理命令
├── dvadmin/              # dvadmin 框架（system、utils）
├── certs/                # TLS 证书（CA、客户端、CRL）
├── app_packages/         # 打包的二进制文件（executor、supervisor）
├── db/                   # 数据库备份和迁移脚本
├── docs/                 # 项目文档
├── static/               # 静态文件
├── templates/            # HTML 模板（前端构建产物）
├── logs/                 # 应用日志
├── pyproject.toml        # Poetry 依赖配置
└── manage.py             # Django 管理脚本
```

## 数据模型

### 核心模型

| 模型 | 说明 |
|------|------|
| `Host` | 受管主机信息（UUID、IP、状态、证书、心跳） |
| `RegistrationToken` | 一次性主机注册令牌，支持 IP 白名单 |
| `HeartbeatServer` | 心跳服务器实例，用于负载均衡 |
| `Template` | 脚本模板（Shell、Python 等） |
| `Workflow` | 多步骤工作流定义 |
| `WorkflowExecution` | 工作流执行记录 |
| `Schedule` | 基于 Cron 的定时任务定义 |
| `ScheduleExecution` | 定时任务执行历史 |
| `ProgramInstallTemplate` | 程序安装模板 |
| `ProgramInstallConfig` | 每台主机的程序部署配置 |
| `ProgramInstallPolicy` | 基于策略的程序分发规则 |
| `ProgramCommand` | 程序管理指令（启动/停止/重启） |
| `ManagedProgram` | Supervisor 在主机上管理的程序 |
| `HostLog` | 主机日志收集和存储 |
| `ProgramHostBinding` | 主机-程序绑定关系 |

### dvadmin 系统模型（继承）

用户、角色、菜单、部门、字典、系统配置、操作日志、登录日志、文件等。

## 快速开始

### 前置要求

- Python 3.12.x（通过 conda 环境 `taurus` 管理）
- MySQL 5.7+ 或 8.0+
- Redis 6.0+
- Poetry（包管理器）

### 安装步骤

1. **进入项目目录**

```bash
cd taurus-backend
```

2. **安装依赖**

```bash
poetry install
```

3. **配置环境**

```bash
cp conf/env.example.py conf/env.py
# 编辑 conf/env.py，配置数据库和 Redis 连接信息
```

4. **初始化数据库**

```bash
poetry run python manage.py migrate
poetry run python manage.py init
```

5. **启动服务**

```bash
# 开发模式
poetry run python manage.py runserver

# 生产模式（Gunicorn）
poetry run gunicorn -c gunicorn_conf.py application.wsgi:application
```

6. **启动 Celery Worker**（异步任务）

```bash
poetry run celery -A application.celery worker -l info
```

7. **启动 WebSocket 服务**（可选）

```bash
poetry run python manage/run_websocket_server.py
```

### 访问地址

- **API 基础地址**: `http://localhost:8000`
- **Swagger UI**: `http://localhost:8000/api/schema/swagger-ui/`
- **Redoc**: `http://localhost:8000/api/schema/redoc/`
- **API Schema**: `http://localhost:8000/api/schema/`

## API 端点

所有 Taurus API 端点均以 `/api/taurus/` 为前缀：

| 端点 | 说明 |
|------|------|
| `/api/taurus/host/` | 主机 CRUD 和管理 |
| `/api/taurus/registration-token/` | 注册令牌管理 |
| `/api/taurus/workflow/` | 工作流定义 |
| `/api/taurus/workflow-execution/` | 工作流执行记录 |
| `/api/taurus/schedule/` | 定时任务定义 |
| `/api/taurus/schedule-execution/` | 定时任务执行历史 |
| `/api/taurus/program-install-template/` | 程序安装模板 |
| `/api/taurus/program-install-config/` | 程序部署配置 |
| `/api/taurus/program-install-policy/` | 程序分发策略 |
| `/api/taurus/program-command/` | 程序管理指令 |
| `/api/taurus/managed-program/` | 受管程序实例 |
| `/api/taurus/host-log/` | 主机日志收集 |
| `/api/taurus/program-host-binding/` | 主机-程序绑定 |
| `/api/taurus/heartbeat/` | 主机心跳记录 |
| `/api/taurus/heartbeat-server/` | 心跳服务器管理 |
| `/api/taurus/supervisor/` | Supervisor 通信（无需认证） |
| `/api/taurus/ops/` | 运维中心（命令、脚本、文件） |

系统 API 端点位于 `/api/system/`（用户、角色、菜单等）。

## 架构设计

### 服务间通信

```
taurus-web ──HTTP/REST+JWT──► taurus-backend ──gRPC+mTLS──► taurus-executor
                                  │  ──HTTP+签名──► taurus-supervisor
                                  └──HTTP+JWT──► taurus-auth ◄──HTTP── executor
```

### 安全机制

- **mTLS**: Executor 与 Backend 之间通过双向 TLS 认证通信
- **证书吊销**: 基于 CRL 的客户端证书吊销机制
- **签名验证**: Supervisor 与 Backend 之间使用共享密钥签名，防重放攻击
- **JWT**: 前端与 Backend 之间通过 JWT Token 认证
- **注册令牌**: 一次性令牌用于主机注册，支持 IP 白名单
- **加密存储**: 敏感配置使用 Fernet 对称加密

## 开发指南

### 代码规范

- 模型继承 `CoreModel`，表名使用 `db_table = table_prefix + "model_name"`
- 序列化器继承 `CustomModelSerializer`（Create/Update 使用不同序列化器）
- 视图集继承 `CustomModelViewSet`
- 外键使用 `db_constraint=False`
- 字段 `verbose_name` 使用中文
- API 前缀：`api/taurus/`

完整开发规范请参考 [taurus-stack-rules.md](../.trae/rules/taurus-stack-rules.md)。

### 测试

```bash
# 运行测试
poetry run python -m pytest

# 运行测试并生成覆盖率报告
poetry run python -m pytest --cov=taurus --cov-report=html
```

### 数据库管理

```bash
# 导出数据库
bash db/dump_export_backend.sh

# 导入数据库
bash db/dump_import_backend.sh
```

## 配置说明

`conf/env.py` 中的关键配置项：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `DATABASE_ENGINE` | 数据库引擎 | `django.db.backends.mysql` |
| `DATABASE_NAME` | 数据库名称 | `taurus_backend` |
| `DATABASE_HOST` | 数据库主机 | `127.0.0.1` |
| `DATABASE_PORT` | 数据库端口 | `3306` |
| `DATABASE_USER` | 数据库用户名 | `root` |
| `DATABASE_PASSWORD` | 数据库密码 | - |
| `TABLE_PREFIX` | 表名前缀 | `taurus_` |
| `REDIS_HOST` | Redis 主机 | `127.0.0.1` |
| `REDIS_PASSWORD` | Redis 密码 | - |
| `REDIS_DB` | Redis 数据库编号 | `1` |
| `CELERY_BROKER_DB` | Celery Broker 数据库编号 | `3` |
| `DEBUG` | 调试模式 | `True` |
| `LOGIN_NO_CAPTCHA_AUTH` | 登录免验证码 | `True` |

## 相关项目

| 项目 | 说明 |
|------|------|
| [taurus-web](../taurus-web/) | 前端管理界面（Vue3 + TypeScript + Element Plus） |
| [taurus-executor](../taurus-executor/) | 客户端执行器（gRPC + Python） |
| [taurus-supervisor](../taurus-supervisor/) | 主机守护进程（asyncio + Python） |
| [taurus-auth](../taurus-auth/) | 票据鉴权服务（Django，端口 8001） |

## 许可证

详见 [LICENSE](LICENSE) 文件。