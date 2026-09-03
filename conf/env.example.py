import os

from application.settings import BASE_DIR

# ================================================= #
# *************** MySQL Database Config  *************** #
# ================================================= #
# Database ENGINE, default demo uses sqlite3 database, production environment recommends MySQL
# sqlite3 setting
# DATABASE_ENGINE = "django.db.backends.sqlite3"
# DATABASE_NAME = os.path.join(BASE_DIR, "db.sqlite3")

# When using MySQL, modify this config
DATABASE_ENGINE = "django.db.backends.mysql"
DATABASE_NAME = 'taurus_backend'  # Used when using MySQL

# Database address - change to your database address
DATABASE_HOST = '127.0.0.1'
# # Database port
DATABASE_PORT = 3306
# # Database username
DATABASE_USER = "root"
# # Database password
DATABASE_PASSWORD = 'AOADMIN3'

# Table prefix
TABLE_PREFIX = "taurus_"
# ================================================= #
# ******** Redis Config, can skip if no Redis  ******** #
# ================================================= #
REDIS_DB = 1
CELERY_BROKER_DB = 3
REDIS_PASSWORD = 'AOADMIN3'
REDIS_HOST = '127.0.0.1'
REDIS_URL = f'redis://:{REDIS_PASSWORD or ""}@{REDIS_HOST}:6379'
# ================================================= #
# ****************** Feature Toggles  ******************* #
# ================================================= #
DEBUG = True
# Enable login detail fetching (calls API to fetch IP detailed address; if intranet, just disable)
ENABLE_LOGIN_ANALYSIS_LOG = True
# Login interface /api/token/ does not require captcha auth, for testing; production environment recommends disabling
LOGIN_NO_CAPTCHA_AUTH = True
# ================================================= #
# ****************** Other Config  ******************* #
# ================================================= #

ALLOWED_HOSTS = ["*"]
# Exclude apps from column permissions
COLUMN_EXCLUDE_APPS = []