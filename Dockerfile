# =====================================================================
# TSPU Monitor 2 — образ Docker
#
# Сборка:  docker build -t tspu-monitor .
# Запуск:  docker compose up -d
# =====================================================================
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TSPU_CONFIG_DIR=/etc/tspu-monitor \
    TSPU_LOG_DIR=/var/log/tspu-monitor

# Системные утилиты для проб + tini для корректной обработки сигналов.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iputils-ping \
        traceroute \
        nmap \
        dnsutils \
        openssl \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/tspu-monitor

COPY pyproject.toml README.md ./
COPY src ./src
# Editable-установка: пользовательские сценарии в src/.../scenarios/custom/
# подхватываются без пересборки образа.
RUN pip install --no-cache-dir -e ".[raw]"

COPY config ./config
COPY deploy/docker/entrypoint.sh /usr/local/bin/tspu-entrypoint
RUN chmod +x /usr/local/bin/tspu-entrypoint \
    && mkdir -p /etc/tspu-monitor \
       /var/lib/tspu-monitor/data \
       /var/lib/tspu-monitor/reports \
       /var/log/tspu-monitor

VOLUME ["/etc/tspu-monitor", "/var/lib/tspu-monitor", "/var/log/tspu-monitor"]

# Веб-дашборд (tspu-monitor web / daemon --web).
EXPOSE 8787

HEALTHCHECK --interval=60s --timeout=20s --start-period=20s --retries=3 \
    CMD tspu-monitor status --json >/dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/tspu-entrypoint"]
CMD ["daemon"]
