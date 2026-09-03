# Taurus Backend

Taurus Backend is the core API service for the Taurus Stack platform, built on Django 4.2 and the dvadmin framework. It provides host management, workflow execution, task scheduling, program deployment, and centralized operations capabilities.

## Features

- **Host Management**: Host registration, approval, heartbeat monitoring, and lifecycle management
- **Workflow Engine**: Multi-step workflow creation, execution, and tracking
- **Task Scheduling**: Cron-based scheduled tasks with execution history
- **Program Deployment**: Remote program installation, version management, and policy-based distribution
- **Supervisor Integration**: Host daemon communication, command dispatch, and state synchronization
- **Certificate Management**: mTLS client certificate issuance, revocation, and CRL management
- **Operations Center**: Remote command execution, script deployment, and file transfer
- **RBAC**: Role-based access control with menu, button, and field-level permissions
- **API Documentation**: Auto-generated Swagger/Redoc docs via drf-spectacular

## Tech Stack

| Component | Technology |
|-----------|------------|
| Framework | Django 4.2 + Django REST Framework |
| Admin Base | dvadmin (built-in RBAC, menu, dictionary) |
| Database | MySQL (primary), SQLite (dev) |
| Cache/Broker | Redis |
| Async Tasks | Celery + dvadmin-celery |
| WebSocket | Channels + Uvicorn |
| Auth | JWT (djangorestframework-simplejwt) |
| API Docs | drf-spectacular |
| Certificates | OpenSSL (mTLS) |
| Package Manager | Poetry |
| Python Version | 3.12.x |

## Project Structure

```
taurus-backend/
├── application/          # Django project config (settings, urls, celery, ws)
├── conf/                 # Environment configuration (env.py, env.example.py)
├── taurus/               # Core Taurus app
│   ├── models.py         # Data models (Host, Task, Workflow, Program, etc.)
│   ├── views.py          # ViewSets and API endpoints
│   ├── urls.py           # API routing (prefix: api/taurus/)
│   ├── serializers.py    # Request/response serializers
│   ├── tasks.py          # Celery async tasks
│   ├── websocket_async.py# WebSocket async handlers
│   ├── sdk/              # Internal SDK (gRPC client, etc.)
│   ├── utils/            # Utilities (auth, gRPC, etc.)
│   ├── scripts/          # Deployment scripts
│   └── management/       # Django management commands
├── dvadmin/              # dvadmin framework (system, utils)
├── certs/                # TLS certificates (CA, client, CRL)
├── app_packages/         # Packaged binaries (executor, supervisor)
├── db/                   # Database dumps and migration scripts
├── docs/                 # Project documentation
├── static/               # Static files
├── templates/            # HTML templates (frontend build output)
├── logs/                 # Application logs
├── pyproject.toml        # Poetry dependencies
└── manage.py             # Django management script
```

## Data Models

### Core Models

| Model | Description |
|-------|-------------|
| `Host` | Managed host info (UUID, IP, status, certificate, heartbeat) |
| `RegistrationToken` | One-time host registration tokens with IP whitelist |
| `HeartbeatServer` | Heartbeat server instances for load distribution |
| `Template` | Script templates (Shell, Python, etc.) |
| `Workflow` | Multi-step workflow definitions |
| `WorkflowExecution` | Workflow execution records |
| `Schedule` | Cron-based scheduled task definitions |
| `ScheduleExecution` | Scheduled task execution history |
| `ProgramInstallTemplate` | Program installation templates |
| `ProgramInstallConfig` | Program deployment configurations per host |
| `ProgramInstallPolicy` | Policy-based program distribution rules |
| `ProgramCommand` | Program management commands (start/stop/restart) |
| `ManagedProgram` | Programs managed by supervisor on hosts |
| `HostLog` | Host log collection and storage |
| `ProgramHostBinding` | Host-program binding relationships |

### dvadmin System Models (inherited)

Users, Roles, Menus, Departments, Dictionaries, System Configs, Operation Logs, Login Logs, Files, etc.

## Quick Start

### Prerequisites

- Python 3.12.x (managed via conda environment `taurus`)
- MySQL 5.7+ or 8.0+
- Redis 6.0+
- Poetry (package manager)

### Installation

1. **Clone and enter the project**

```bash
cd taurus-backend
```

2. **Install dependencies**

```bash
poetry install
```

3. **Configure environment**

```bash
cp conf/env.example.py conf/env.py
# Edit conf/env.py with your database and Redis settings
```

4. **Initialize database**

```bash
poetry run python manage.py migrate
poetry run python manage.py init
```

5. **Start the server**

```bash
# Development mode
poetry run python manage.py runserver

# Production mode (Gunicorn)
poetry run gunicorn -c gunicorn_conf.py application.wsgi:application
```

6. **Start Celery worker** (for async tasks)

```bash
poetry run celery -A application.celery worker -l info
```

7. **Start WebSocket server** (optional)

```bash
poetry run python manage/run_websocket_server.py
```

### Access

- **API Base URL**: `http://localhost:8000`
- **Swagger UI**: `http://localhost:8000/api/schema/swagger-ui/`
- **Redoc**: `http://localhost:8000/api/schema/redoc/`
- **API Schema**: `http://localhost:8000/api/schema/`

## API Endpoints

All Taurus API endpoints are prefixed with `/api/taurus/`:

| Endpoint | Description |
|----------|-------------|
| `/api/taurus/host/` | Host CRUD and management |
| `/api/taurus/registration-token/` | Registration token management |
| `/api/taurus/workflow/` | Workflow definitions |
| `/api/taurus/workflow-execution/` | Workflow execution records |
| `/api/taurus/schedule/` | Scheduled task definitions |
| `/api/taurus/schedule-execution/` | Scheduled task execution history |
| `/api/taurus/program-install-template/` | Program install templates |
| `/api/taurus/program-install-config/` | Program deployment configs |
| `/api/taurus/program-install-policy/` | Program distribution policies |
| `/api/taurus/program-command/` | Program management commands |
| `/api/taurus/managed-program/` | Managed program instances |
| `/api/taurus/host-log/` | Host log collection |
| `/api/taurus/program-host-binding/` | Host-program bindings |
| `/api/taurus/heartbeat/` | Host heartbeat records |
| `/api/taurus/heartbeat-server/` | Heartbeat server management |
| `/api/taurus/supervisor/` | Supervisor communication (no auth) |
| `/api/taurus/ops/` | Operations center (commands, scripts, files) |

System API endpoints are under `/api/system/` (users, roles, menus, etc.).

## Architecture

### Inter-Service Communication

```
taurus-web ──HTTP/REST+JWT──► taurus-backend ──gRPC+mTLS──► taurus-executor
                                  │  ──HTTP+Signature──► taurus-supervisor
                                  └──HTTP+JWT──► taurus-auth ◄──HTTP── executor
```

### Security

- **mTLS**: Executor and backend communicate via mutual TLS
- **Certificate Revocation**: CRL-based client certificate revocation
- **Signature Verification**: Supervisor-backend communication uses shared-key signature with anti-replay
- **JWT**: Frontend-backend authentication via JWT tokens
- **Registration Tokens**: One-time tokens for host registration with IP whitelist support
- **Encrypted Secrets**: Sensitive configurations use Fernet symmetric encryption

## Development

### Code Conventions

- Models inherit `CoreModel` with `db_table = table_prefix + "model_name"`
- Serializers inherit `CustomModelSerializer` (separate Create/Update serializers)
- ViewSets inherit `CustomModelViewSet`
- Foreign keys use `db_constraint=False`
- Field `verbose_name` in Chinese
- API prefix: `api/taurus/`

See [taurus-stack-rules.md](../.trae/rules/taurus-stack-rules.md) for full development guidelines.

### Testing

```bash
# Run tests
poetry run python -m pytest

# Run with coverage
poetry run python -m pytest --cov=taurus --cov-report=html
```

### Database Management

```bash
# Export database
bash db/dump_export_backend.sh

# Import database
bash db/dump_import_backend.sh
```

## Configuration

Key configuration options in `conf/env.py`:

| Config | Description | Default |
|--------|-------------|---------|
| `DATABASE_ENGINE` | Database backend | `django.db.backends.mysql` |
| `DATABASE_NAME` | Database name | `taurus_backend` |
| `DATABASE_HOST` | Database host | `127.0.0.1` |
| `DATABASE_PORT` | Database port | `3306` |
| `DATABASE_USER` | Database user | `root` |
| `DATABASE_PASSWORD` | Database password | - |
| `TABLE_PREFIX` | Table name prefix | `taurus_` |
| `REDIS_HOST` | Redis host | `127.0.0.1` |
| `REDIS_PASSWORD` | Redis password | - |
| `REDIS_DB` | Redis database number | `1` |
| `CELERY_BROKER_DB` | Celery broker database | `3` |
| `DEBUG` | Debug mode | `True` |
| `LOGIN_NO_CAPTCHA_AUTH` | Disable captcha for login | `True` |

## Related Projects

| Project | Description |
|---------|-------------|
| [taurus-web](../taurus-web/) | Frontend admin (Vue3 + TypeScript + Element Plus) |
| [taurus-executor](../taurus-executor/) | Client executor (gRPC + Python) |
| [taurus-supervisor](../taurus-supervisor/) | Host daemon (asyncio + Python) |
| [taurus-auth](../taurus-auth/) | Ticket auth service (Django, port 8001) |

## License

See [LICENSE](LICENSE) for details.