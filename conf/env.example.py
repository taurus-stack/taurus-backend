import os

from application.settings import BASE_DIR

# ================================================= #
# *************** Config Decryption Helpers *************** #
# ================================================= #
def _decrypt_if_needed(value):
    """Automatically decrypt config values (if encrypted)"""
    if not value or not isinstance(value, str):
        return value
    
    # Check if value is in encrypted format
    if not value.startswith('fernet:'):
        return value
    
    try:
        from taurus.config_crypto import auto_decrypt
        return auto_decrypt(value)
    except Exception as e:
        # Log warning on decryption failure, return original value
        import logging
        logging.getLogger(__name__).warning(f"Config decryption failed: {e}")
        return value

# ================================================= #
# *************** MySQL Database Config  *************** #
# ================================================= #
# Database ENGINE, default demo uses sqlite3 database, production environment recommends MySQL
# sqlite3 setting
# DATABASE_ENGINE = "django.db.backends.sqlite3"
# DATABASE_NAME = os.path.join(BASE_DIR, "db.sqlite3")

# When using MySQL, modify this config
DATABASE_ENGINE = os.environ.get('DATABASE_ENGINE', 'django.db.backends.mysql')
DATABASE_NAME = os.environ.get('DATABASE_NAME', 'taurus_backend')  # Used when using MySQL

# Database address - change to your database address
DATABASE_HOST = os.environ.get('DATABASE_HOST', '127.0.0.1')
# # Database port
DATABASE_PORT = int(os.environ.get('DATABASE_PORT', '3306'))
# # Database username
DATABASE_USER = os.environ.get('DATABASE_USER', 'root')
# # Database password (supports encrypted format, auto-decrypted on startup)
DATABASE_PASSWORD = _decrypt_if_needed(os.environ.get(
    'DATABASE_PASSWORD',
    'change_me'  # Dev env plaintext, use encrypted format in production
))

# Table prefix
TABLE_PREFIX = os.environ.get('TABLE_PREFIX', 'taurus_')
# ================================================= #
# ******** Redis Config, can skip if no Redis  ******** #
# ================================================= #
# REDIS_DB = 1
# CELERY_BROKER_DB = 3
# REDIS_PASSWORD = _decrypt_if_needed(os.environ.get('REDIS_PASSWORD', 'change_me'))
# REDIS_HOST = '127.0.0.1'
# REDIS_URL = f'redis://:{REDIS_PASSWORD or ""}@{REDIS_HOST}:6379'
# ================================================= #
# ****************** Feature Toggles  ******************* #
# ================================================= #
DEBUG = os.environ.get('DEBUG', 'true').lower() == 'true'
# Enable login detail fetching (calls API to fetch IP detailed address; if intranet, just disable)
ENABLE_LOGIN_ANALYSIS_LOG = os.environ.get('ENABLE_LOGIN_ANALYSIS_LOG', 'true').lower() == 'true'
# Login interface /api/token/ does not require captcha auth, for testing; production environment recommends disabling
LOGIN_NO_CAPTCHA_AUTH = os.environ.get('LOGIN_NO_CAPTCHA_AUTH', 'true').lower() == 'true'

# ================================================= #
# ***************** Workflow Engine Config *************** #
# ================================================= #
# List of ExecutableUnit adapters auto-imported on startup (just specify module path, triggers their @register_unit_adapter decorator)
# Adding a new adapter only requires appending one line to this list; no engine code changes needed
INSTALLED_UNIT_ADAPTERS: list[str] = [
    # Core built-in adapters (bundled with taurus code, implemented progressively during S3 phase)
    "taurus.workflow.units.ops_execution",       # S2-01/S2-02/S2-03: script + command + file_op adapters (reuse OpsExecution execution pipeline)
    "taurus.workflow.units.virtual",             # S2-07: start + end + noop virtual nodes
    "taurus.workflow.units.program",             # S2-04: Program management (install/upgrade/start/stop/restart/remove)
    "taurus.workflow.units.approval",          # S2-05: Manual approval (async node, is_asynchronous_human=True)
    "taurus.workflow.units.sub_workflow",      # S2-06: Sub-workflow (recursively triggers WorkflowExecution)
    "taurus.workflow.units.http_callback",     # S4-01: HTTP callback (externally driven async)
    "taurus.workflow.units.condition",        # S4-02: Condition branching (flow control)
    "taurus.workflow.units.transform",        # S4-03: Data transformation (JSONPath/Python/regex)
    "taurus.workflow.units.http",             # S4-04: Synchronous HTTP request
    "taurus.workflow.units.loop",             # S4-05: Loop control node
    "taurus.workflow.units.webhook_notification",  # S4-06: Webhook notification
    "taurus.workflow.units.email_notification",   # S4-07: Email notification
    "taurus.workflow.units.wait",                 # S4-08: Wait (delay / absolute time / daily schedule)
    # "taurus.workflow.units.schedule_gate",     # Schedule/window gate (e.g. only allow 00:00~05:00)
]

# testing_* placeholder adapters load only in DEBUG dev environment, to avoid test nodes appearing on the production node panel
if DEBUG:
    INSTALLED_UNIT_ADAPTERS = [
        "taurus.workflow.units._testing_noop",     # Unit test only: always success / output examples
        "taurus.workflow.units._testing_fail",     # Unit test only: always fail / error code examples
    ] + INSTALLED_UNIT_ADAPTERS

# ================================================= #
# ****************** Email Notification Config ****************** #
# ================================================= #
# Dev env defaults to console backend (email content printed to backend logs, not actually sent), to avoid local SMTP restrictions
# Production env switches to real SMTP via environment variables:
#   EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
#   EMAIL_HOST=smtp.example.com  EMAIL_PORT=465  EMAIL_USE_SSL=true
#   EMAIL_HOST_USER=xxx  EMAIL_HOST_PASSWORD=xxx  DEFAULT_FROM_EMAIL=xxx
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend'
)
EMAIL_HOST = os.environ.get('EMAIL_HOST', '127.0.0.1')
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = _decrypt_if_needed(os.environ.get('EMAIL_HOST_PASSWORD', ''))
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '25'))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'false').lower() == 'true'
EMAIL_USE_SSL = os.environ.get('EMAIL_USE_SSL', 'false').lower() == 'true'
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'no-reply@example.com')

# ================================================= #
# ****************** Other Config  ******************* #
# ================================================= #

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '*').split(',')
# Exclude apps from column permissions
COLUMN_EXCLUDE_APPS = []

# ================================================= #
# ****************** Request Signing Config ****************** #
# ================================================= #
# Whether to enable request signature verification (Supervisor heartbeat and command reporting endpoints)
# When enabled, newly registered hosts auto-generate a signing key
# Enabled by default for API security
REQUEST_SIGNING_ENABLED = os.environ.get('REQUEST_SIGNING_ENABLED', 'true').lower() == 'true'

# Signing master key (supports encrypted format, auto-decrypted on startup)
# Production must set the encrypted key via environment variable
SIGNING_MASTER_KEY = _decrypt_if_needed(os.environ.get(
    'SIGNING_MASTER_KEY',
    'change-me-to-a-secure-master-key'
))

# ================================================= #
# ****************** SSL/TLS Config ****************** #
# ================================================= #
# Whether to enable SSL verification (validates server certificate on Supervisor heartbeat requests)
# Disabled by default, dev env uses HTTP
# Production must set to true and configure certificates
SUPERVISOR_SSL_VERIFY = os.environ.get('SUPERVISOR_SSL_VERIFY', 'false').lower() == 'true'

# Custom CA certificate path (optional, needed for self-signed certificates)
SUPERVISOR_SSL_CA_PATH = os.environ.get('SUPERVISOR_SSL_CA_PATH', '')

# ================================================= #
# ****************** Server Cert SAN Config ************ #
# ================================================= #
# Unified gRPC service name (server cert SAN + client ssl_target_name_override)
# Uses fixed DNS name + client target name override to bypass IP hostname verification (Option 2: bare IP direct connect)
GRPC_SERVER_CERT_SAN = os.environ.get('GRPC_SERVER_CERT_SAN', 'taurus-grpc-server')

# Server certificate SAN list config (deprecated, retained for backward compatibility)
# Now uniformly uses GRPC_SERVER_CERT_SAN, no longer writes IP/hostnames into SAN
# Format: comma-separated, e.g. "DNS:localhost,IP:127.0.0.1,DNS:server1.example.com"
SERVER_CERT_SAN_LIST = os.environ.get('SERVER_CERT_SAN_LIST', '')