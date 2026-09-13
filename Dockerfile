FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=2.0.1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com \
    TZ=Asia/Shanghai

WORKDIR /app

RUN sed -i 's|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        default-libmysqlclient-dev \
        pkg-config \
        curl \
    && pip install --no-cache-dir "poetry==$POETRY_VERSION" \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock ./

RUN poetry install --no-interaction --no-ansi --only main --no-root

COPY . .

RUN if [ -f conf/env.example.py ] && [ ! -f conf/env.py ]; then cp conf/env.example.py conf/env.py; fi

RUN useradd -m -u 1000 taurus \
    && mkdir -p /app/logs /app/media /app/staticfiles /app/certs /app/certs/newcerts \
    && chmod +x /app/docker-entrypoint.sh \
    && chown -R taurus:taurus /app

USER taurus

EXPOSE 8000

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

ENTRYPOINT ["bash", "docker-entrypoint.sh"]