#!/usr/bin/env bash
#
# Создание LXC-контейнера Proxmox VE для TSPU Monitor.
# Запускать НА УЗЛЕ Proxmox (не внутри контейнера).
#
# Использование:
#   bash proxmox-create.sh <CTID> [hostname] [template]
#
# Переменные окружения: STORAGE, MEMORY, CORES, DISK, BRIDGE.
#
set -euo pipefail

CTID="${1:-}"
HOSTNAME_ARG="${2:-tspu-monitor}"
TEMPLATE="${3:-local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst}"
STORAGE="${STORAGE:-local-lvm}"
MEMORY="${MEMORY:-1024}"
CORES="${CORES:-2}"
DISK="${DISK:-8}"
BRIDGE="${BRIDGE:-vmbr0}"

red()  { printf '\033[31m%s\033[0m\n' "$*"; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
info() { printf '[*] %s\n' "$*"; }

if [[ -z "${CTID}" ]]; then
    red "Использование: bash proxmox-create.sh <CTID> [hostname] [template]"
    exit 1
fi
if ! command -v pct >/dev/null 2>&1; then
    red "Команда pct не найдена — скрипт нужно запускать на узле Proxmox VE."
    exit 1
fi

if pct status "${CTID}" >/dev/null 2>&1; then
    red "Контейнер с ID ${CTID} уже существует."
    exit 1
fi

info "Создаю контейнер ${CTID} (${HOSTNAME_ARG})..."
pct create "${CTID}" "${TEMPLATE}" \
    --hostname "${HOSTNAME_ARG}" \
    --cores "${CORES}" \
    --memory "${MEMORY}" \
    --swap 512 \
    --rootfs "${STORAGE}:${DISK}" \
    --net0 "name=eth0,bridge=${BRIDGE},ip=dhcp" \
    --features nesting=1,keyctl=1 \
    --unprivileged 0 \
    --onboot 1

# CAP_NET_RAW/NET_ADMIN нужны scapy-пробам (packet.ttl_probe).
CONF="/etc/pve/lxc/${CTID}.conf"
if ! grep -q "^lxc.cap.keep" "${CONF}"; then
    info "Добавляю capabilities в ${CONF}..."
    cat >> "${CONF}" <<'EOF'

# TSPU Monitor: raw-пробы (scapy) требуют CAP_NET_RAW/NET_ADMIN.
lxc.cap.keep: net_raw net_admin net_bind_service
EOF
fi

info "Запускаю контейнер..."
pct start "${CTID}"

grn "================================================================="
grn "Контейнер ${CTID} создан и запущен."
grn "================================================================="
echo
echo "Дальнейшие шаги:"
echo "  1) Скопируйте проект в контейнер:"
echo "     pct push ${CTID} <путь-к-tspu-monitor.tar.gz> /root/tspu-monitor.tar.gz"
echo "     pct exec ${CTID} -- tar -xzf /root/tspu-monitor.tar.gz -C /root"
echo "  2) Установите (внутри контейнера):"
echo "     pct exec ${CTID} -- bash /root/tspu-monitor/install.sh"
echo "  3) Настройте секреты:"
echo "     pct exec ${CTID} -- nano /opt/tspu-monitor/config/secrets.yaml"
echo "  4) Запустите сервис:"
echo "     pct exec ${CTID} -- systemctl enable --now tspu-monitor"
echo "  5) Проверьте:"
echo "     pct exec ${CTID} -- tspu-monitor self-test"
echo
echo "Подробнее: deploy/lxc/README.md"
