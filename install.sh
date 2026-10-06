#!/usr/bin/env bash
#
# TSPU Monitor — универсальный установщик для Linux и macOS.
#
# Одна команда:
#   curl -fsSL https://raw.githubusercontent.com/Fairen8/tspu-monitor/main/install.sh | sudo bash
#
# Поддерживаемые ОС:
#   Debian/Ubuntu/Mint, RHEL/CentOS/Rocky/Alma/Fedora, Alpine,
#   Arch/Manjaro, openSUSE, macOS (Homebrew).
#
# Флаги:
#   --version REF    версия (тег vX.Y.Z) или main (по умолчанию)
#   --prefix DIR     каталог установки (по умолчанию /opt/tspu-monitor)
#   --no-deps        не устанавливать системные пакеты
#   --no-service     не создавать сервис (systemd/OpenRC/launchd)
#   --with-web       включить веб-дашборд
#   --web-host HOST  адрес дашборда (по умолчанию 127.0.0.1)
#   --web-port PORT  порт дашборда (по умолчанию 8787)
#   --with-telemetry включить добровольную анонимную статистику
#   --uninstall      удалить установку (данные сохраняются)
#   -h, --help       справка
#
# Переменные окружения:
#   TSPU_REPO=owner/repo   репозиторий-источник
#   TSPU_VERSION=REF       версия по умолчанию
#   TSPU_PREFIX=DIR        каталог установки
#   TSPU_PURGE=1           при --uninstall удалить данные и журналы
#
set -euo pipefail

REPO="${TSPU_REPO:-Fairen8/tspu-monitor}"
PREFIX="${TSPU_PREFIX:-/opt/tspu-monitor}"
VERSION="${TSPU_VERSION:-main}"
WEB_HOST="127.0.0.1"
WEB_PORT="8787"
WITH_WEB=0
WITH_TELEMETRY=0
WITH_DEPS=1
WITH_SERVICE=1
DO_UNINSTALL=0
VENV="$PREFIX/venv"
CONFIG_DIR="$PREFIX/config"
DATA_DIR="/var/lib/tspu-monitor"
LOG_DIR="/var/log/tspu-monitor"
SERVICE_NAME="tspu-monitor"
WRAPPER="/usr/local/bin/tspu-monitor"

c_red() { printf '\033[31m%s\033[0m\n' "$*" >&2; }
c_grn() { printf '\033[32m%s\033[0m\n' "$*"; }
c_ylw() { printf '\033[33m%s\033[0m\n' "$*"; }
c_cyn() { printf '\033[36m[*]\033[0m %s\n' "$*"; }
c_ok()  { printf '\033[32m[+]\033[0m %s\n' "$*"; }
c_wrn() { printf '\033[33m[!]\033[0m %s\n' "$*"; }
c_err() { printf '\033[31m[x]\033[0m %s\n' "$*" >&2; }
die()   { c_err "$*"; exit 1; }

usage() {
    sed -n '2,34p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//' || true
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version) VERSION="${2:?--version требует значение}"; shift 2 ;;
        --prefix)  PREFIX="${2:?--prefix требует значение}"; shift 2 ;;
        --no-deps) WITH_DEPS=0; shift ;;
        --no-service) WITH_SERVICE=0; shift ;;
        --with-web) WITH_WEB=1; shift ;;
        --web-host) WEB_HOST="${2:?--web-host требует значение}"; shift 2 ;;
        --web-port) WEB_PORT="${2:?--web-port требует значение}"; shift 2 ;;
        --with-telemetry) WITH_TELEMETRY=1; shift ;;
        --uninstall) DO_UNINSTALL=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Неизвестный аргумент: $1 (см. --help)" ;;
    esac
done

VENV="$PREFIX/venv"
CONFIG_DIR="$PREFIX/config"

# ---------------------------------------------------------------------------
# Обнаружение ОС и пакетного менеджера
# ---------------------------------------------------------------------------
OS_KERNEL="$(uname -s)"
OS_ID="unknown"
OS_PRETTY="$(uname -srm)"
PKG=""

detect_os() {
    if [[ "$OS_KERNEL" == "Darwin" ]]; then
        OS_ID="macos"
        OS_PRETTY="$(sw_vers -productName 2>/dev/null || echo macOS) $(sw_vers -productVersion 2>/dev/null || true)"
        if command -v brew >/dev/null 2>&1; then PKG="brew"; fi
        return
    fi
    if [[ -r /etc/os-release ]]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        OS_ID="${ID:-unknown}"
        OS_PRETTY="${PRETTY_NAME:-$OS_ID}"
    fi
    if command -v apt-get >/dev/null 2>&1; then PKG="apt"
    elif command -v dnf >/dev/null 2>&1; then PKG="dnf"
    elif command -v yum >/dev/null 2>&1; then PKG="yum"
    elif command -v apk >/dev/null 2>&1; then PKG="apk"
    elif command -v pacman >/dev/null 2>&1; then PKG="pacman"
    elif command -v zypper >/dev/null 2>&1; then PKG="zypper"
    fi
}

install_deps() {
    [[ "$WITH_DEPS" -eq 1 ]] || { c_wrn "Системные пакеты не устанавливаются (--no-deps)"; return 0; }

    local apt_pkgs=(python3 python3-pip python3-venv iputils-ping traceroute nmap
                    dnsutils openssl ca-certificates curl)
    local dnf_pkgs=(python3 python3-pip iputils traceroute nmap bind-utils
                    openssl ca-certificates curl)
    local apk_pkgs=(python3 py3-pip iputils traceroute nmap bind-tools
                    openssl ca-certificates curl)
    local pac_pkgs=(python python-pip iputils traceroute nmap bind
                    openssl ca-certificates curl)
    local zyp_pkgs=(python3 python3-pip iputils traceroute nmap bind-utils
                    openssl ca-certificates curl)

    case "$PKG" in
        apt)
            c_cyn "Устанавливаю пакеты (apt): ${apt_pkgs[*]}"
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -qq
            apt-get install -y -qq --no-install-recommends "${apt_pkgs[@]}" || true
            # Python 3.11+ на старых Ubuntu (22.04): пробуем отдельный пакет.
            find_python >/dev/null || apt-get install -y -qq python3.11 python3.11-venv || true
            ;;
        dnf)
            c_cyn "Устанавливаю пакеты (dnf)"
            dnf install -y -q "${dnf_pkgs[@]}" || true
            ;;
        yum)
            c_cyn "Устанавливаю пакеты (yum)"
            yum install -y -q "${dnf_pkgs[@]}" || true
            ;;
        apk)
            c_cyn "Устанавливаю пакеты (apk)"
            apk add --no-cache "${apk_pkgs[@]}" || true
            ;;
        pacman)
            c_cyn "Устанавливаю пакеты (pacman)"
            pacman -Sy --noconfirm --needed "${pac_pkgs[@]}" || true
            ;;
        zypper)
            c_cyn "Устанавливаю пакеты (zypper)"
            zypper --non-interactive install -y "${zyp_pkgs[@]}" || true
            ;;
        brew)
            c_cyn "Устанавливаю пакеты (brew): python@3.12 nmap bind"
            brew install python@3.12 nmap bind || true
            ;;
        *)
            c_wrn "Пакетный менеджер не распознан — ставьте зависимости вручную"
            c_wrn "Нужны: python3 (>=3.11), ping, traceroute, nmap, dig, openssl"
            ;;
    esac
    return 0
}

find_python() {
    local candidates=(python3.13 python3.12 python3.11 python3 python)
    if [[ "$PKG" == "brew" ]] && command -v brew >/dev/null 2>&1; then
        local bp
        bp="$(brew --prefix 2>/dev/null || true)"
        [[ -n "$bp" ]] && candidates=("$bp/opt/python@3.12/bin/python3.12" "${candidates[@]}")
    fi
    local py
    for py in "${candidates[@]}"; do
        if command -v "$py" >/dev/null 2>&1 \
            && "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            printf '%s' "$py"
            return 0
        fi
    done
    return 1
}

# ---------------------------------------------------------------------------
# Получение исходников
# ---------------------------------------------------------------------------
SCRIPT_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
fi

SRC_DIR=""

fetch_sources() {
    if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/pyproject.toml" && -d "$SCRIPT_DIR/src" ]]; then
        SRC_DIR="$SCRIPT_DIR"
        c_ok "Исходники: локальный каталог $SRC_DIR"
        return 0
    fi
    command -v tar >/dev/null 2>&1 || die "Нужен tar для распаковки"
    local tmp; tmp="$(mktemp -d)"
    local url="https://github.com/${REPO}/archive/${VERSION}.tar.gz"
    c_cyn "Скачиваю исходники: $url"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" -o "$tmp/src.tar.gz" || die "Не удалось скачать $url"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$tmp/src.tar.gz" "$url" || die "Не удалось скачать $url"
    else
        die "Нужен curl или wget"
    fi
    mkdir -p "$tmp/src"
    tar -xzf "$tmp/src.tar.gz" -C "$tmp/src" --strip-components=1 || die "Ошибка распаковки"
    SRC_DIR="$tmp/src"
    c_ok "Исходники распакованы в $SRC_DIR"
}

# ---------------------------------------------------------------------------
# Установка приложения
# ---------------------------------------------------------------------------
install_app() {
    local py
    py="$(find_python)" || die "Не найден Python >= 3.11. Установите его и повторите запуск."
    c_cyn "Python: $py ($("$py" --version 2>&1))"

    mkdir -p "$PREFIX"
    if [[ -d "$SRC_DIR" && "$SRC_DIR" != "$PREFIX" ]]; then
        c_cyn "Копирую файлы в $PREFIX"
        cp -a "$SRC_DIR/." "$PREFIX/" 2>/dev/null || true
    fi

    [[ -d "$VENV" ]] || { c_cyn "Создаю venv: $VENV"; "$py" -m venv "$VENV"; }
    c_cyn "Устанавливаю зависимости (включая scapy)"
    "$VENV/bin/pip" install --upgrade --quiet pip wheel setuptools
    "$VENV/bin/pip" install --upgrade --quiet --no-cache-dir -e "${PREFIX}[raw]"

    mkdir -p "$DATA_DIR/data" "$DATA_DIR/reports" "$LOG_DIR"
    c_ok "Приложение установлено в $PREFIX"
}

write_wrapper() {
    cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
# Авто-сгенерировано install.sh.
export TSPU_CONFIG_DIR="$CONFIG_DIR"
exec "$VENV/bin/tspu-monitor" "\$@"
EOF
    chmod 755 "$WRAPPER"
    c_ok "CLI: $WRAPPER"
}

configure() {
    mkdir -p "$CONFIG_DIR"
    if [[ ! -f "$CONFIG_DIR/settings.yaml" ]]; then
        cp "$PREFIX/config/settings.yaml" "$CONFIG_DIR/settings.yaml"
        c_ok "Создан $CONFIG_DIR/settings.yaml"
    else
        c_cyn "settings.yaml уже существует — не перезаписываю"
    fi
    if [[ ! -f "$CONFIG_DIR/secrets.yaml" ]]; then
        cp "$PREFIX/config/secrets.yaml" "$CONFIG_DIR/secrets.yaml"
        c_ok "Создан $CONFIG_DIR/secrets.yaml (chmod 600)"
    else
        c_cyn "secrets.yaml уже существует — не перезаписываю"
    fi
    chmod 600 "$CONFIG_DIR/secrets.yaml" 2>/dev/null || true

    if [[ "$WITH_WEB" -eq 1 ]]; then
        "$VENV/bin/python" - "$CONFIG_DIR" "$WEB_HOST" "$WEB_PORT" <<'PY'
import secrets
import sys
from pathlib import Path

import yaml

config_dir, host, port = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])

settings_path = config_dir / "settings.yaml"
settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
settings.setdefault("web", {})
settings["web"].update({"enabled": True, "host": host, "port": port})
settings_path.write_text(
    yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding="utf-8"
)

secrets_path = config_dir / "secrets.yaml"
secrets = yaml.safe_load(secrets_path.read_text(encoding="utf-8")) or {}
web_secrets = secrets.setdefault("web", {})
if not web_secrets.get("token"):
    web_secrets["token"] = secrets.token_hex(24)
    print("WEB_TOKEN=" + web_secrets["token"])
secrets_path.write_text(
    yaml.safe_dump(secrets, allow_unicode=True, sort_keys=False), encoding="utf-8"
)
PY
        WEB_TOKEN="$("$VENV/bin/python" - "$CONFIG_DIR" <<'PY'
import sys
from pathlib import Path

import yaml

path = Path(sys.argv[1]) / "secrets.yaml"
data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
print((data.get("web") or {}).get("token") or "")
PY
)"
        c_ok "Веб-дашборд включён: http://$WEB_HOST:$WEB_PORT/"
        if [[ "$WEB_HOST" != "127.0.0.1" && "$WEB_HOST" != "localhost" ]]; then
            c_ylw "Токен доступа: $WEB_TOKEN"
        fi
    fi

    if [[ "$WITH_TELEMETRY" -eq 1 ]]; then
        "$VENV/bin/python" - "$CONFIG_DIR" <<'PY'
import sys
from pathlib import Path

import yaml

path = Path(sys.argv[1]) / "settings.yaml"
data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
data.setdefault("telemetry", {})["enabled"] = True
path.write_text(
    yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
)
PY
        c_ok "Анонимная статистика включена (выключить: tspu-monitor telemetry disable)"
    fi
}

install_service() {
    [[ "$WITH_SERVICE" -eq 1 ]] || { c_wrn "Сервис не создаётся (--no-service)"; return 0; }

    if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
        local unit="/etc/systemd/system/${SERVICE_NAME}.service"
        c_cyn "Создаю systemd-юнит: $unit"
        cat > "$unit" <<EOF
[Unit]
Description=TSPU Monitor — диагностика блокировок DPI/TSPU
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$PREFIX
Environment=TSPU_CONFIG_DIR=$CONFIG_DIR
ExecStart=$VENV/bin/tspu-monitor daemon$([[ "$WITH_WEB" -eq 1 ]] && echo " --web")
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
        systemctl enable --now "${SERVICE_NAME}.service" || c_wrn "Не удалось запустить сервис"
        c_ok "Сервис запущен: systemctl status ${SERVICE_NAME}"
    elif command -v rc-service >/dev/null 2>&1; then
        local init="/etc/init.d/${SERVICE_NAME}"
        c_cyn "Создаю OpenRC-сервис: $init"
        cat > "$init" <<EOF
#!/sbin/openrc-run
name="TSPU Monitor"
command="$WRAPPER"
command_args="daemon$([[ "$WITH_WEB" -eq 1 ]] && echo " --web")"
command_background=true
pidfile="/run/${SERVICE_NAME}.pid"
output_log="/var/log/tspu-monitor/service.log"
error_log="/var/log/tspu-monitor/service.log"
depend() { need net; }
EOF
        chmod 755 "$init"
        rc-update add "$SERVICE_NAME" default >/dev/null 2>&1 || true
        rc-service "$SERVICE_NAME" restart >/dev/null 2>&1 || c_wrn "Не удалось запустить сервис"
        c_ok "Сервис запущен: rc-service $SERVICE_NAME status"
    else
        c_wrn "systemd/OpenRC не найден — запускайте вручную: tspu-monitor daemon"
    fi
}

uninstall() {
    c_cyn "Удаление TSPU Monitor"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl disable --now "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
        rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi
    if command -v rc-service >/dev/null 2>&1; then
        rc-service "$SERVICE_NAME" stop >/dev/null 2>&1 || true
        rc-update del "$SERVICE_NAME" default >/dev/null 2>&1 || true
        rm -f "/etc/init.d/${SERVICE_NAME}"
    fi
    rm -f "$WRAPPER"
    rm -rf "$PREFIX"
    if [[ "${TSPU_PURGE:-0}" == "1" ]]; then
        rm -rf "$DATA_DIR" "$LOG_DIR"
        c_ok "Удалено вместе с данными и журналами"
    else
        c_ok "Удалено (данные сохранены: $DATA_DIR; полное удаление: TSPU_PURGE=1)"
    fi
}

summary() {
    echo
    c_grn "================================================================="
    c_grn " TSPU Monitor установлен (${OS_PRETTY})"
    c_grn "================================================================="
    echo
    echo "Дальнейшие шаги:"
    echo "  1) Укажите цели:     sudoedit $CONFIG_DIR/secrets.yaml"
    echo "  2) Проверьте:        tspu-monitor self-test"
    echo "  3) Запустите:        tspu-monitor check"
    echo "  4) Отчёт:            tspu-monitor report"
    echo "  5) Дашборд:          tspu-monitor web --open"
    echo
    echo "Сервис:  systemctl status ${SERVICE_NAME}    (или rc-service)"
    echo "Удаление: bash install.sh --uninstall [--purge через TSPU_PURGE=1]"
    echo
}

main() {
    detect_os
    if [[ "$DO_UNINSTALL" -eq 1 ]]; then
        [[ "$(id -u)" -eq 0 ]] || die "Для удаления нужен root"
        uninstall
        exit 0
    fi
    [[ "$(id -u)" -eq 0 ]] || die "Запустите с правами root: sudo bash install.sh (или curl … | sudo bash)"
    c_cyn "ОС: $OS_PRETTY (пакетный менеджер: ${PKG:-нет})"

    install_deps
    fetch_sources
    install_app
    write_wrapper
    configure
    install_service
    summary
}

main "$@"
