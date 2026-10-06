#!/bin/sh
# Точка входа Docker-образа TSPU Monitor.
# Создаёт конфиги из шаблонов при первом запуске и передаёт управление CLI.
set -e

CONFIG_DIR="${TSPU_CONFIG_DIR:-/etc/tspu-monitor}"
TEMPLATES="${TEMPLATES:-/opt/tspu-monitor/config}"

mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/settings.yaml" ]; then
    cp "$TEMPLATES/settings.yaml" "$CONFIG_DIR/settings.yaml"
    echo "[entrypoint] создан $CONFIG_DIR/settings.yaml"
fi
if [ ! -f "$CONFIG_DIR/secrets.yaml" ]; then
    cp "$TEMPLATES/secrets.yaml" "$CONFIG_DIR/secrets.yaml"
    echo "[entrypoint] создан $CONFIG_DIR/secrets.yaml — заполните цели и Telegram"
fi
chmod 600 "$CONFIG_DIR/secrets.yaml" 2>/dev/null || true

exec tspu-monitor "$@"
