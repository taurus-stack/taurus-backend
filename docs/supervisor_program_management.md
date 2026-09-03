# Supervisor 和 Program 管理文档

## 目录
- [概述](#概述)
- [核心数据模型](#核心数据模型)
- [管理流程](#管理流程)
- [API 接口文档](#api-接口文档)
- [使用示例](#使用示例)
- [常见问题](#常见问题)

---

## 概述

Taurus Ops 系统通过 **Supervisor** 管理主机上的多个程序（如 taurus-executor、taurus-monitor），实现程序的自动化安装、部署、监控和版本升级。

### 架构特点

- **心跳驱动**：通过心跳机制实现状态同步和指令下发
- **策略驱动**：通过策略实现大规模主机的自动化管理
- **版本管理**：支持程序版本升级和批量部署
- **状态追踪**：实时追踪程序运行状态

### 整体架构

```
┌─────────────────────────────────────────────────────┐
│                  Taurus Server                        │
│                                                      │
│  ┌──────────────────┐    ┌──────────────────────┐   │
│  │ ProgramInstall   │───▶│ ProgramInstall       │   │
│  │ Policy           │    │ Config               │   │
│  │ (策略定义)       │    │ (安装配置)           │   │
│  └──────────────────┘    └──────────┬───────────┘   │
│                                     │               │
│                                     ▼               │
│                          ┌──────────────────────┐   │
│                          │ supervisor_heartbeat │   │
│                          │ (心跳接收+指令下发)  │   │
│                          └──────────┬───────────┘   │
└─────────────────────────────────────┼───────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    │    Supervisor Heartbeat            │
                    │  (上报程序状态 + 接收安装指令)     │
                    └─────────────────┼─────────────────┘
                                      │
                    ┌─────────────────▼─────────────────┐
                    │        Taurus Supervisor            │
                    │  (管理多个 Programs)               │
                    │  - taurus-executor                  │
                    │  - taurus-monitor                   │
                    └───────────────────────────────────┘
```

---

## 核心数据模型

### 1. ManagedProgram（受管程序配置）

记录由 Supervisor 管理的程序实例信息。

**模型位置**：`taurus/models.py`

| 字段 | 类型 | 说明 |
|------|------|------|
| host | ForeignKey | 所属主机 |
| name | CharField | 程序名称（如 taurus-executor） |
| version | CharField | 当前版本 |
| status | CharField | 运行状态（见状态说明） |
| pid | IntegerField | 进程PID |
| port | IntegerField | 服务端口 |
| auto_start | BooleanField | 自动启动 |
| restart_on_crash | BooleanField | 崩溃自动重启 |
| config | JSONField | 程序特定配置 |
| last_heartbeat_at | DateTimeField | 最后心跳时间 |

**运行状态说明**：

| 状态 | 说明 |
|------|------|
| stopped | 已停止 |
| starting | 启动中 |
| running | 运行中 |
| stopping | 停止中 |
| crashed | 已崩溃 |
| upgrading | 升级中 |

### 2. ProgramInstallConfig（程序安装配置）

用于 Server 向 Supervisor 下发程序安装指令。

**模型位置**：`taurus/models.py`

| 字段 | 类型 | 说明 |
|------|------|------|
| host | ForeignKey | 所属主机 |
| program_name | CharField | 程序名称 |
| version | CharField | 目标版本 |
| config | JSONField | 程序配置（业务配置通过 env_vars 传递） |
| auto_start | BooleanField | 自动启动 |
| user | CharField | 运行用户（空表示使用当前用户） |
| group | CharField | 运行用户组 |
| installed | BooleanField | 是否已安装 |

### 3. ProgramInstallPolicy（程序安装策略）

规则驱动的大规模主机程序管理策略。

**模型位置**：`taurus/models.py`

| 字段 | 类型 | 说明 |
|------|------|------|
| name | CharField | 策略名称 |
| program_name | CharField | 程序名称 |
| version | CharField | 目标版本 |
| config | JSONField | 程序配置 |
| auto_start | BooleanField | 自动启动 |
| user | CharField | 运行用户 |
| group | CharField | 运行用户组 |
| match_rules | JSONField | 主机匹配规则 |
| status | IntegerField | 策略状态（0-未启用 1-已启用 2-已暂停） |
| auto_apply | BooleanField | 新主机注册时自动应用 |
| matched_hosts_count | IntegerField | 匹配主机数 |
| applied_hosts_count | IntegerField | 已应用主机数 |
| priority | IntegerField | 优先级（数字越小优先级越高） |

**匹配规则（match_rules）示例**：

```json
{
  "host_type": "linux",
  "status": 1,
  "host_ip_prefix": "10.0.",
  "extra_info": {
    "env": "production"
  }
}
```

支持的匹配规则：

| 规则 | 说明 | 示例 |
|------|------|------|
| host_type | 主机类型精确匹配 | `"linux"` |
| status | 主机状态精确匹配 | `1`（已批准） |
| online_status | 在线状态精确匹配 | `1`（在线） |
| host_ip_prefix | IP前缀匹配 | `"10.0."` |
| host_ip_range | IP范围匹配 | `["your-host-1", "your-host-2"]` |
| host_name_pattern | 主机名称模糊匹配 | `"web-server"` |
| extra_info | 自定义标签匹配 | `{"env": "production"}` |

---

## 管理流程

### 心跳机制说明

系统只有一个心跳接口，由 Supervisor 负责上报：

| 接口 | 职责 | 说明 |
|------|------|------|
| `POST /api/taurus/supervisor/heartbeat/` | 程序管理 + 主机监控 | Supervisor 统一上报心跳 |

> **注意**：Executor 本身不发送心跳，由 Supervisor 统一管理。

### 1. Supervisor 心跳机制

**接口**：`POST /api/taurus/supervisor/heartbeat/`

**流程说明**：

1. Supervisor 定期上报心跳，携带所有受管程序状态
2. Server 更新 `ManagedProgram` 记录（使用 `update_or_create`）
3. 检查是否有下行指令：
   - 证书吊销指令
   - 程序安装指令（从 `ProgramInstallConfig` 中查找未安装的配置）

**心跳请求示例**：

```json
{
  "host_id": "uuid-string",
  "supervisor_version": "1.0.0",
  "timestamp": "2025-11-25T10:00:00Z",
  "metrics": {
    "cpu_usage": 45.2,
    "memory_usage": 60.5,
    "disk_usage": 70.0
  },
  "programs": [
    {
      "name": "taurus-executor",
      "version": "1.2.0",
      "status": "running",
      "pid": 12345,
      "port": 8080
    },
    {
      "name": "taurus-monitor",
      "version": "1.1.0",
      "status": "running",
      "pid": 12346,
      "port": 9090
    }
  ]
}
```

**心跳响应示例**：

```json
{
  "code": 2000,
  "msg": "Supervisor心跳接收成功",
  "data": {
    "commands": [
      {
        "type": "install",
        "program_name": "taurus-executor",
        "version": "1.3.0",
        "config": {
          "env_vars": {
            "GRPC_PORT": "50051",
            "METRICS_PORT": "9090"
          }
        },
        "auto_start": true,
        "user": "taurus",
        "group": "taurus"
      }
    ],
    "server_time": "2025-11-25T10:00:01Z"
  }
}
```

### 2. 程序安装流程

```
1. 创建 ProgramInstallConfig 或 ProgramInstallPolicy
         │
         ▼
2. Supervisor 心跳时检查未安装配置
         │
         ▼
3. Server 下发 install 指令
         │
         ▼
4. Supervisor 接收指令并安装程序
         │
         ▼
5. 安装完成后，下次心跳上报程序状态
         │
         ▼
6. Server 更新 ManagedProgram 记录
         │
         ▼
7. 标记 ProgramInstallConfig.installed = True
```

### 3. 策略应用流程

```
1. 创建 ProgramInstallPolicy（定义匹配规则）
         │
         ▼
2. 手动触发 apply 或启用 auto_apply
         │
         ▼
3. 系统根据 match_rules 匹配主机
         │
         ▼
4. 为匹配的主机创建 ProgramInstallConfig
         │
         ▼
5. Supervisor 心跳时自动安装
```

---

## API 接口文档

### 1. Supervisor 心跳

**URL**：`POST /api/taurus/supervisor/heartbeat/`

**权限**：无需认证（AllowAny）

**职责**：程序级别管理 + 主机监控

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| host_id | UUID | 是 | 主机ID |
| supervisor_version | String | 否 | Supervisor版本 |
| timestamp | DateTime | 是 | 心跳时间戳 |
| metrics | Dict | 否 | 系统指标 |
| programs | List | 否 | 受管程序状态列表 |

**programs 字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| name | String | 程序名称 |
| version | String | 程序版本 |
| status | String | 运行状态（stopped/starting/running/stopping/crashed/upgrading） |
| pid | Integer | 进程PID |
| port | Integer | 服务端口 |

**响应**：

| 字段 | 类型 | 说明 |
|------|------|------|
| commands | List | 管理指令列表 |
| server_time | DateTime | 服务器时间 |

**管理指令类型**：

| 指令类型 | 说明 | 参数 |
|---------|------|------|
| certificate_revoked | 证书吊销 | message, revoked_at, reason |
| install | 安装程序 | program_name, version, config, auto_start, user, group |

### 2. 下载程序安装包

**URL**：`GET /api/taurus/supervisor/download/`

**权限**：无需认证（AllowAny）

**查询参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| package_type | String | 是 | - | 程序类型（如 executor, supervisor, monitor 等） |
| platform | String | 否 | linux | 操作系统（linux, macos, windows） |
| arch | String | 否 | x86_64 | 架构（x86_64, arm64） |

**配置方式**：

在 `package_dirs.json` 文件中配置（位于项目根目录）：

```json
{
  "executor": {
    "dir": "/path/to/executor",
    "prefix": "taurus-executor",
    "ext": ".tar.gz"
  },
  "supervisor": {
    "dir": "/path/to/supervisor",
    "prefix": "taurus-supervisor",
    "ext": ""
  },
  "monitor": {
    "dir": "/path/to/monitor",
    "prefix": "taurus-monitor",
    "ext": ".tar.gz"
  },
  "my-custom-app": {
    "dir": "/path/to/my-custom-app",
    "prefix": "my-custom-app",
    "ext": ".tar.gz"
  }
}
```

> **扩展说明**：如需支持新的程序类型，只需在 `package_dirs.json` 中添加配置，**无需修改代码或重启服务**。

**环境变量**：

可通过 `PACKAGE_DIRS_CONFIG` 环境变量指定配置文件路径：

```bash
export PACKAGE_DIRS_CONFIG=/etc/taurus/package_dirs.json
```

**响应**：
- 成功：返回二进制文件流
- 失败：返回错误信息

**使用示例**：

```bash
# 下载 Executor 安装包
curl -X GET "http://localhost:8000/api/taurus/supervisor/download/?package_type=executor&platform=linux&arch=x86_64" \
  -o taurus-executor.tar.gz

# 下载 Supervisor 安装包
curl -X GET "http://localhost:8000/api/taurus/supervisor/download/?package_type=supervisor&platform=linux&arch=x86_64" \
  -o taurus-supervisor

# 下载 Monitor 安装包
curl -X GET "http://localhost:8000/api/taurus/supervisor/download/?package_type=monitor&platform=linux&arch=arm64" \
  -o taurus-monitor.tar.gz

# 下载自定义程序安装包
curl -X GET "http://localhost:8000/api/taurus/supervisor/download/?package_type=my-custom-app&platform=linux&arch=x86_64" \
  -o my-custom-app.tar.gz
```
  -o taurus-monitor.tar.gz
```

### 3. 程序安装配置管理

**基础URL**：`/api/taurus/program-install-config/`

#### 3.1 查询列表

**URL**：`GET /api/taurus/program-install-config/`

**查询参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| host | Integer | 主机ID |
| program_name | String | 程序名称 |
| installed | Boolean | 安装状态 |
| auto_start | Boolean | 自动启动 |
| search | String | 搜索（程序名称、版本、主机名称、IP） |

#### 3.2 创建配置

**URL**：`POST /api/taurus/program-install-config/`

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| host | Integer | 是 | 主机ID |
| program_name | String | 是 | 程序名称 |
| version | String | 是 | 目标版本 |
| config | Dict | 否 | 程序配置 |
| auto_start | Boolean | 否 | 自动启动，默认true |
| user | String | 否 | 运行用户 |
| group | String | 否 | 运行用户组 |

#### 3.3 批量创建配置

**URL**：`POST /api/taurus/program-install-config/batch_create/`

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| host_ids | List<Integer> | 是 | 目标主机ID列表 |
| program_name | String | 是 | 程序名称 |
| version | String | 是 | 目标版本 |
| config | Dict | 否 | 程序配置 |
| auto_start | Boolean | 否 | 自动启动，默认true |
| user | String | 否 | 运行用户 |
| group | String | 否 | 运行用户组 |

**响应示例**：

```json
{
  "code": 2000,
  "msg": "批量创建成功：创建5条，跳过2条",
  "data": {
    "created": 5,
    "skipped": 2,
    "total": 7
  }
}
```

#### 3.4 更新配置

**URL**：`PUT /api/taurus/program-install-config/{id}/`

#### 3.5 删除配置

**URL**：`DELETE /api/taurus/program-install-config/{id}/`

### 4. 程序安装策略管理

**基础URL**：`/api/taurus/program-install-policy/`

#### 4.1 查询列表

**URL**：`GET /api/taurus/program-install-policy/`

**查询参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| status | Integer | 策略状态（0-未启用 1-已启用 2-已暂停） |
| auto_apply | Boolean | 自动应用 |
| program_name | String | 程序名称 |
| search | String | 搜索（策略名称、程序名称、版本） |

#### 4.2 创建策略

**URL**：`POST /api/taurus/program-install-policy/`

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| name | String | 是 | 策略名称 |
| program_name | String | 是 | 程序名称 |
| version | String | 是 | 目标版本 |
| match_rules | Dict | 是 | 主机匹配规则 |
| config | Dict | 否 | 程序配置 |
| auto_start | Boolean | 否 | 自动启动，默认true |
| user | String | 否 | 运行用户 |
| group | String | 否 | 运行用户组 |
| status | Integer | 否 | 策略状态，默认0 |
| auto_apply | Boolean | 否 | 自动应用，默认true |
| priority | Integer | 否 | 优先级，默认100 |

#### 4.3 应用策略

**URL**：`POST /api/taurus/program-install-policy/{id}/apply/`

**响应示例**：

```json
{
  "code": 2000,
  "msg": "策略应用完成：匹配50台主机，创建45条配置，跳过5条",
  "data": {
    "matched": 50,
    "created": 45,
    "skipped": 5
  }
}
```

#### 4.4 预览匹配主机

**URL**：`GET /api/taurus/program-install-policy/{id}/preview_hosts/`

**查询参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| page | Integer | 页码，默认1 |

**响应示例**：

```json
{
  "code": 2000,
  "msg": "共匹配50台主机",
  "data": {
    "total": 50,
    "hosts": [
      {
        "id": 1,
        "host_name": "web-server-01",
        "host_ip": "your-host-3",
        "host_type": "linux",
        "status": 1,
        "online_status": 1
      }
    ]
  }
}
```

#### 4.5 升级策略版本

**URL**：`POST /api/taurus/program-install-policy/{id}/upgrade_version/`

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| version | String | 是 | 新版本号 |

**响应示例**：

```json
{
  "code": 2000,
  "msg": "版本升级成功：1.2.0 -> 1.3.0，创建45条新配置",
  "data": {
    "old_version": "1.2.0",
    "new_version": "1.3.0",
    "created": 45
  }
}
```

---

## 使用示例

### 场景1：为单台主机安装程序

```bash
# 1. 创建安装配置
curl -X POST http://localhost:8000/api/taurus/program-install-config/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "host": 1,
    "program_name": "taurus-executor",
    "version": "1.3.0",
    "config": {
      "env_vars": {
        "GRPC_PORT": "50051",
        "METRICS_PORT": "9090"
      }
    },
    "auto_start": true,
    "user": "taurus",
    "group": "taurus"
  }'

# 2. Supervisor 心跳时自动接收安装指令并执行
```

### 场景2：批量为主机安装程序

```bash
# 批量创建安装配置
curl -X POST http://localhost:8000/api/taurus/program-install-config/batch_create/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "host_ids": [1, 2, 3, 4, 5],
    "program_name": "taurus-monitor",
    "version": "1.1.0",
    "config": {
      "env_vars": {
        "METRICS_PORT": "9090",
        "COLLECT_INTERVAL": "30"
      }
    },
    "auto_start": true
  }'
```

### 场景3：使用策略管理大规模主机

```bash
# 1. 创建策略（匹配所有Linux生产环境主机）
curl -X POST http://localhost:8000/api/taurus/program-install-policy/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "name": "生产环境Executor部署",
    "program_name": "taurus-executor",
    "version": "1.3.0",
    "match_rules": {
      "host_type": "linux",
      "status": 1,
      "host_ip_prefix": "10.0.",
      "extra_info": {
        "env": "production"
      }
    },
    "config": {
      "env_vars": {
        "GRPC_PORT": "50051",
        "METRICS_PORT": "9090"
      }
    },
    "auto_start": true,
    "auto_apply": true,
    "priority": 10
  }'

# 2. 预览匹配的主机
curl -X GET http://localhost:8000/api/taurus/program-install-policy/1/preview_hosts/ \
  -H "Authorization: Bearer <token>"

# 3. 手动应用策略
curl -X POST http://localhost:8000/api/taurus/program-install-policy/1/apply/ \
  -H "Authorization: Bearer <token>"

# 4. 升级版本
curl -X POST http://localhost:8000/api/taurus/program-install-policy/1/upgrade_version/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "version": "1.4.0"
  }'
```

### 场景4：查询程序安装状态

```bash
# 查询某主机的所有程序安装配置
curl -X GET "http://localhost:8000/api/taurus/program-install-config/?host=1" \
  -H "Authorization: Bearer <token>"

# 查询未安装的程序配置
curl -X GET "http://localhost:8000/api/taurus/program-install-config/?installed=false" \
  -H "Authorization: Bearer <token>"
```

---

## 常见问题

### Q1: Supervisor 如何接收安装指令？

**A**: Supervisor 通过定期调用 `supervisor_heartbeat` 接口上报程序状态，Server 在响应中返回 `commands` 字段包含安装指令。

### Q2: 如何确保程序不重复安装？

**A**: 
- `ProgramInstallConfig` 有 `installed` 字段标记安装状态
- Server 心跳接口只返回 `installed=False` 的配置
- 批量创建时会检查是否已存在相同配置并跳过

### Q3: 策略冲突时如何处理？

**A**: 通过 `priority` 字段控制优先级，数字越小优先级越高。建议在创建策略时合理设置优先级。

### Q4: 如何监控程序运行状态？

**A**: 
- 通过 `ManagedProgram` 模型查看程序实时状态
- 状态通过 Supervisor 心跳持续更新
- 可在心跳响应中检查程序是否正常运行

### Q5: 新主机注册后如何自动安装程序？

**A**: 
1. 创建 `ProgramInstallPolicy` 并设置 `auto_apply=true`
2. 新主机注册时，系统自动匹配策略
3. 自动创建 `ProgramInstallConfig`
4. Supervisor 心跳时自动接收安装指令

### Q6: 如何回滚程序版本？

**A**: 
1. 创建新的 `ProgramInstallConfig`，指定旧版本
2. 或使用策略的 `upgrade_version` 接口回退到旧版本
3. Supervisor 接收指令后执行版本切换

---

## 附录

### 相关代码文件

| 文件 | 说明 |
|------|------|
| `taurus/models.py` | 数据模型定义 |
| `taurus/views.py` | 视图和接口实现 |
| `taurus/serializers.py` | 序列化器定义 |
| `taurus/urls.py` | 路由配置 |

### 数据库表

| 表名 | 说明 |
|------|------|
| `taurus_managed_program` | 受管程序配置 |
| `taurus_program_install_config` | 程序安装配置 |
| `taurus_program_install_policy` | 程序安装策略 |

### 相关接口

| 接口 | 说明 |
|------|------|
| `POST /api/taurus/executor/register/` | Executor注册 |
| `POST /api/taurus/executor/certificate/` | Executor证书签发 |
| `GET /api/taurus/supervisor/download/?package_type=executor` | 下载Executor安装包 |
| `GET /api/taurus/supervisor/download/?package_type=supervisor` | 下载Supervisor安装包 |
| `GET /api/taurus/supervisor/download/?package_type=monitor` | 下载Monitor安装包 |
| `POST /api/taurus/supervisor/heartbeat/` | Supervisor心跳 |