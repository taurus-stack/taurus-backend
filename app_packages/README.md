# 应用程序包目录

此目录用于存放 Taurus 应用程序安装包文件（如 executor、supervisor、monitor 等），供客户端自动下载和安装。

## 目录结构

```
app_packages/
├── executor/
│   ├── taurus-executor-1.0.0-linux-x86_64.tar.gz
│   └── taurus-executor-1.1.0-linux-x86_64.tar.gz
├── supervisor/
│   └── taurus-supervisor-1.0.0-linux-x86_64
├── monitor/
│   └── taurus-monitor-1.0.0-linux-x86_64.tar.gz
└── README.md
```

## 文件命名规范

安装包文件应遵循以下命名格式：

```
{prefix}-{version}-{platform}-{arch}{ext}
```

### 示例

```
taurus-executor-1.0.0-linux-x86_64.tar.gz   # Linux x86_64 架构
taurus-executor-1.0.0-linux-arm64.tar.gz    # Linux ARM64 架构
taurus-supervisor-1.0.0-linux-x86_64        # Supervisor 包
taurus-monitor-1.0.0-linux-x86_64.tar.gz    # Monitor 包
```

## 配置说明

在 `application/settings.py` 中已配置：

```python
TAURUS_PACKAGE_DIRS = {
    'executor': {
        'dir': os.path.join(BASE_DIR, "app_packages", "executor"),
        'prefix': 'taurus-executor',
        'ext': '.tar.gz',
    },
    'supervisor': {
        'dir': os.path.join(BASE_DIR, "app_packages", "supervisor"),
        'prefix': 'taurus-supervisor',
        'ext': '',
    },
    'monitor': {
        'dir': os.path.join(BASE_DIR, "app_packages", "monitor"),
        'prefix': 'taurus-monitor',
        'ext': '.tar.gz',
    },
}
```

可通过 `package_dirs.json` 配置文件覆盖默认设置。

## 如何获取安装包

1. 从对应项目打包：
   ```bash
   cd taurus-executor
   bash scripts/package.sh --version 1.0.0
   ```

2. 将生成的发布包复制到对应子目录：
   ```bash
   cp taurus-executor/dist/taurus-executor-1.0.0-linux-x86_64.tar.gz ../taurus-backend/app_packages/executor/
   ```

## 安全要求

1. 安装包文件权限应设置为 `644`（所有者可读写，其他人只读）
2. 此目录不应包含敏感信息
3. 建议定期清理旧版本安装包