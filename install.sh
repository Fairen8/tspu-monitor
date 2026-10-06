#!/usr/bin/env bash
#
# TSPU Monitor 2 — установщик для Debian 12/13, LXC (Proxmox) и VM.
# Идемпотентен: повторный запуск не разрушает конфигурацию и данные.
#
# Использование:  sudo bash install.sh
#
set -euo pipefail

INSTALL_PREFIX="${INSTALL_PREFIX:-/opt/tspu-monitor}"
VENV_DIR="${INSTALL_PREFIX}/venv"
SERVICE_NAME="tspu-monitor.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/var/log/tspu-monitor-install.log"
if ! touch "$LOG_FILE" 2>/dev/null; then
    LOG_FILE="${SCRIPT_DIR}/tspu-monitor-install.log"
    touch "$LOG_FILE"
fi
exec > >(tee -a "$LOG_FILE") 2>&1

red()  { printf '\033[31m%s\033[0m\n' "$*"; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
ylw()  { printf '\033[33m%s\033[0m\n' "$*"; }
info() { printf '\033[36m[*]\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m[+]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[!]\033[0m %s\n' "$*"; }
err()  { printf '\033[31m[x]\033[0m %s\n' "$*" >&2; }

if [[ ${EUID} -ne 0 ]]; then
    err "Запустите с правами root: sudo bash install.sh"
    exit 1
fi

# ---------------------------------------------------------------------------
# 1. ОС
# ---------------------------------------------------------------------------
if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    info "ОС: ${PRETTY_NAME:-неизвестно}"
    if [[ "${ID:-}" != "debian" && "${ID_LIKE:-}" != *debian* ]]; then
        warn "Скрипт оптимизирован для Debian, продолжаю на свой риск."
    fi
else
    warn "/etc/os-release не найден."
fi

# ---------------------------------------------------------------------------
# 2. Системные пакеты
# ---------------------------------------------------------------------------
APT_PACKAGES=(
    python3 python3-pip python3-venv
    iputils-ping traceroute nmap dnsutils
    openssl ca-certificates curl
)
info "Устанавливаю системные пакеты: ${APT_PACKAGES[*]}"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends "${APT_PACKAGES[@]}"
ok "Системные пакеты установлены"

# ---------------------------------------------------------------------------
# 3. Копирование проекта
# ---------------------------------------------------------------------------
info "Копирую проект в ${INSTALL_PREFIX}"
mkdir -p "${INSTALL_PREFIX}"
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
        --exclude=".git" \
        --exclude="__pycache__" \
        --exclude="*.pyc" \
        --exclude=".venv" \
        --exclude="venv" \
        --exclude="data" \
        --exclude="logs/*" \
        --exclude="reports/*" \
        --exclude="tspu-monitor-install.log" \
        "${SCRIPT_DIR}/" "${INSTALL_PREFIX}/"
else
    cp -r "${SCRIPT_DIR}/." "${INSTALL_PREFIX}/"
    find "${INSTALL_PREFIX}" -type d -name "__pycache__" -prune -exec rm -rf {} + || true
fi

# ---------------------------------------------------------------------------
# 4. Python-окружение
# ---------------------------------------------------------------------------
if [[ ! -d "${VENV_DIR}" ]]; then
    info "Создаю virtualenv: ${VENV_DIR}"
    python3 -m venv "${VENV_DIR}"
fi
info "Устанавливаю TSPU Monitor и зависимости (включая scapy)"
"${VENV_DIR}/bin/pip" install --upgrade --quiet pip wheel setuptools
(
    cd "${INSTALL_PREFIX}"
    # Editable-установка: кастомные сценарии в src/.../scenarios/custom/
    # подхватываются без переустановки.
    "${VENV_DIR}/bin/pip" install --upgrade --quiet -e ".[raw]"
)
ok "Python-пакет установлен"

# ---------------------------------------------------------------------------
# 5. Каталоги данных
# ---------------------------------------------------------------------------
mkdir -p /var/lib/tspu-monitor/data /var/lib/tspu-monitor/reports /var/log/tspu-monitor
ok "Каталоги /var/lib/tspu-monitor и /var/log/tspu-monitor готовы"

# ---------------------------------------------------------------------------
# 6. Конфигурация
# ---------------------------------------------------------------------------
mkdir -p "${INSTALL_PREFIX}/config"
if [[ ! -f "${INSTALL_PREFIX}/config/settings.yaml" ]]; then
    cp "${SCRIPT_DIR}/config/settings.yaml" "${INSTALL_PREFIX}/config/settings.yaml"
    ok "Создан ${INSTALL_PREFIX}/config/settings.yaml"
else
    info "settings.yaml уже существует — не перезаписываю"
fi
if [[ ! -f "${INSTALL_PREFIX}/config/secrets.yaml" ]]; then
    cp "${SCRIPT_DIR}/config/secrets.yaml" "${INSTALL_PREFIX}/config/secrets.yaml"
    chmod 600 "${INSTALL_PREFIX}/config/secrets.yaml"
    ok "Создан ${INSTALL_PREFIX}/config/secrets.yaml (chmod 600)"
else
    info "secrets.yaml уже существует — не перезаписываю"
fi

# ---------------------------------------------------------------------------
# 7. CLI-обёртка
# ---------------------------------------------------------------------------
info "Создаю обёртку /usr/local/bin/tspu-monitor"
cat > /usr/local/bin/tspu-monitor <<EOF
#!/usr/bin/env bash
# Авто-сгенерировано install.sh: обёртка над venv с путём к конфигу.
export TSPU_CONFIG_DIR="${INSTALL_PREFIX}/config"
exec "${VENV_DIR}/bin/tspu-monitor" "\$@"
EOF
chmod 755 /usr/local/bin/tspu-monitor
ok "CLI доступен: /usr/local/bin/tspu-monitor"

# ---------------------------------------------------------------------------
# 8. systemd
# ---------------------------------------------------------------------------
if command -v systemctl >/dev/null 2>&1; then
    info "Устанавливаю systemd unit: ${SERVICE_PATH}"
    cat > "${SERVICE_PATH}" <<EOF
[Unit]
Description=TSPU Monitor 2 — диагностика блокировок DPI/TSPU
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_PREFIX}
Environment=TSPU_CONFIG_DIR=${INSTALL_PREFIX}/config
ExecStart=${VENV_DIR}/bin/tspu-monitor daemon
Restart=on-failure
RestartSec=10
AmbientCapabilities=CAP_NET_RAW CAP_NET_ADMIN
CapabilityBoundingSet=CAP_NET_RAW CAP_NET_ADMIN CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    ok "systemd unit установлен"
else
    warn "systemd не найден — запускайте вручную: tspu-monitor daemon"
fi

# ---------------------------------------------------------------------------
# 9. Self-test
# ---------------------------------------------------------------------------
info "Проверка окружения"
if "${VENV_DIR}/bin/tspu-monitor" --config-dir "${INSTALL_PREFIX}/config" self-test; then
    ok "Self-test пройден"
else
    warn "Self-test нашёл замечания (см. вывод выше)"
fi

# ---------------------------------------------------------------------------
# 10. Итог
# ---------------------------------------------------------------------------
echo
grn "================================================================="
grn "TSPU Monitor 2 установлен в ${INSTALL_PREFIX}"
grn "================================================================="
echo
echo "Дальнейшие шаги:"
echo "  1) Заполните секреты:        sudoedit ${INSTALL_PREFIX}/config/secrets.yaml"
echo "     - targets.wireguard_server / amnezia_server / openvpn_server / ..."
echo "     - при необходимости — Telegram (см. DOCS.md, раздел про zapret)"
echo "  2) Проверьте настройки:      tspu-monitor config validate"
echo "  3) Запустите сервис:         systemctl enable --now ${SERVICE_NAME}"
echo "  4) Запустите проверку:       tspu-monitor check"
echo "  5) Отчёт:                    tspu-monitor report"
echo
echo "Для LXC: контейнеру нужны capabilities net_raw/net_admin (deploy/lxc/README.md)."
echo "Журнал установки: ${LOG_FILE}"
echo
exit 0
