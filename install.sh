#!/usr/bin/env bash
#
# TSPU Monitor — установщик для Linux и macOS.
#
# Одна команда:
#   curl -fsSL https://raw.githubusercontent.com/Fairen8/tspu-monitor/main/install.sh | bash
#   (sudo не обязателен: без root ставится в ~/.local/share/tspu-monitor)
#
# Поддерживаемые ОС:
#   Debian/Ubuntu/Mint, RHEL/CentOS/Rocky/Alma/Fedora, Alpine,
#   Arch/Manjaro, openSUSE, macOS (Homebrew).
#
# Флаги:
#   --version REF    версия (тег vX.Y.Z) или main (по умолчанию)
#   --prefix DIR     каталог установки
#   --no-deps        не устанавливать системные пакеты
#   --no-service     не создавать сервис (systemd/OpenRC/launchd)
#   --with-web       включить веб-дашборд
#   --web-host HOST  адрес дашборда (по умолчанию 127.0.0.1)
#   --web-port PORT  порт дашборда (по умолчанию 8787)
#   --with-telemetry включить анонимную статистику (по умолчанию включена)
#   --no-telemetry   отключить анонимную статистику
#   --uninstall      удалить установку (данные сохраняются)
#   --purge          с --uninstall удалить также данные и журналы
#   --no-color       без цвета
#   -h, --help       справка
#
# Переменные окружения: TSPU_REPO, TSPU_VERSION, TSPU_PREFIX,
#   TSPU_NO_SERVICE=1, TSPU_WITH_WEB=1, TSPU_NO_TELEMETRY=1,
#   TSPU_UNINSTALL=1, TSPU_PURGE=1, NO_COLOR=1
#
set -Eeuo pipefail

INSTALLER_VERSION="2.2.2"
REPO="${TSPU_REPO:-Fairen8/tspu-monitor}"

# ---------------------------------------------------------------------------
# Оформление (цвет отключается, если вывод не терминал или задан NO_COLOR)
# ---------------------------------------------------------------------------
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YLW=$'\033[33m'
    C_CYN=$'\033[36m'; C_DIM=$'\033[2m'; C_RST=$'\033[0m'
else
    C_RED=""; C_GRN=""; C_YLW=""; C_CYN=""; C_DIM=""; C_RST=""
fi

STEP=0
STEP_TOTAL=8
step() { STEP=$((STEP + 1)); printf '%s[%d/%d]%s %s\n' "$C_CYN" "$STEP" "$STEP_TOTAL" "$C_RST" "$*"; }
info() { printf '      %s\n' "$*"; }
ok()   { printf '%s   ok%s %s\n' "$C_GRN" "$C_RST" "$*"; }
warn() { printf '%s   !!%s %s\n' "$C_YLW" "$C_RST" "$*"; }
err()  { printf '%s   xx%s %s\n' "$C_RED" "$C_RST" "$*" >&2; }
die()  { err "$*"; exit 1; }

LOG_FILE=""
on_error() {
    local code=$1 line=$2
    err "Установка прервана (код $code, строка $line)"
    [[ -n "$LOG_FILE" ]] && err "Полный лог: $LOG_FILE"
    exit "$code"
}
trap 'on_error $? $LINENO' ERR

usage() {
    sed -n '2,/^set -E/p' "$0" 2>/dev/null | sed '$d' | sed 's/^# \{0,1\}//' || true
}

# ---------------------------------------------------------------------------
# Аргументы
# ---------------------------------------------------------------------------
VERSION="${TSPU_VERSION:-main}"
PREFIX="${TSPU_PREFIX:-}"
WEB_HOST="127.0.0.1"
WEB_PORT="8787"
WITH_WEB="${TSPU_WITH_WEB:-0}"
WITH_TELEMETRY=1
WITH_DEPS=1
WITH_SERVICE=$([[ "${TSPU_NO_SERVICE:-0}" == "1" ]] && echo 0 || echo 1)
DO_UNINSTALL="${TSPU_UNINSTALL:-0}"
PURGE="${TSPU_PURGE:-0}"
if [[ -n "${TSPU_NO_TELEMETRY:-}" ]]; then WITH_TELEMETRY=0; fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version) VERSION="${2:?--version требует значение}"; shift 2 ;;
        --prefix) PREFIX="${2:?--prefix требует значение}"; shift 2 ;;
        --no-deps) WITH_DEPS=0; shift ;;
        --no-service) WITH_SERVICE=0; shift ;;
        --with-web) WITH_WEB=1; shift ;;
        --web-host) WEB_HOST="${2:?--web-host требует значение}"; shift 2 ;;
        --web-port) WEB_PORT="${2:?--web-port требует значение}"; shift 2 ;;
        --with-telemetry) WITH_TELEMETRY=1; shift ;;
        --no-telemetry) WITH_TELEMETRY=0; shift ;;
        --uninstall) DO_UNINSTALL=1; shift ;;
        --purge) PURGE=1; shift ;;
        --no-color) C_RED=""; C_GRN=""; C_YLW=""; C_CYN=""; C_DIM=""; C_RST=""; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Неизвестный аргумент: $1 (см. --help)" ;;
    esac
done

# ---------------------------------------------------------------------------
# ОС и режим (root / пользователь)
# ---------------------------------------------------------------------------
OS_KERNEL="$(uname -s)"
OS_ID="unknown"
OS_PRETTY="$(uname -srm)"
PKG=""

detect_os() {
    if [[ "$OS_KERNEL" == "Darwin" ]]; then
        OS_ID="macos"
        OS_PRETTY="$(sw_vers -productName 2>/dev/null || echo macOS) $(sw_vers -productVersion 2>/dev/null || true)"
        command -v brew >/dev/null 2>&1 && PKG="brew"
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

ROOT_MODE=1
SUDO=""
if [[ "$(id -u)" -ne 0 ]]; then
    ROOT_MODE=0
    SUDO="$(command -v sudo || true)"
fi

detect_os

if [[ "$ROOT_MODE" -eq 1 ]]; then
    PREFIX="${PREFIX:-/opt/tspu-monitor}"
    BIN_DIR="/usr/local/bin"
    DATA_DIR="/var/lib/tspu-monitor/data"
    REPORTS_DIR="/var/lib/tspu-monitor/reports"
    LOG_DIR="/var/log/tspu-monitor"
    LOG_FILE="/var/log/tspu-monitor-install.log"
else
    PREFIX="${PREFIX:-${XDG_DATA_HOME:-$HOME/.local/share}/tspu-monitor}"
    BIN_DIR="$HOME/.local/bin"
    DATA_DIR="$PREFIX/data"
    REPORTS_DIR="$PREFIX/reports"
    LOG_DIR="$PREFIX/logs"
    LOG_FILE="${TMPDIR:-/tmp}/tspu-monitor-install-$(id -u).log"
    if [[ "$WITH_SERVICE" -eq 1 ]]; then
        WITH_SERVICE=0
        info "Режим без root: сервис не создаётся (нужен sudo)."
    fi
fi

VENV="$PREFIX/venv"
CONFIG_DIR="$PREFIX/config"
WRAPPER="$BIN_DIR/tspu-monitor"
SERVICE_NAME="tspu-monitor"

# Лог: терминал получает цвет, файл — без ANSI-кодов.
touch "$LOG_FILE" 2>/dev/null || LOG_FILE="${TMPDIR:-/tmp}/tspu-monitor-install.log"
if [[ -n "$LOG_FILE" ]] && command -v sed >/dev/null 2>&1; then
    exec > >(tee >(sed -E 's/\x1b\[[0-9;]*[A-Za-z]//g' >>"$LOG_FILE")) 2>&1
fi

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------
run_priv() {
    # Выполнить команду с правами root (напрямую или через sudo).
    if [[ "$ROOT_MODE" -eq 1 ]]; then
        "$@"
    elif [[ -n "$SUDO" ]]; then
        "$SUDO" "$@"
    else
        return 127
    fi
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

install_deps() {
    [[ "$WITH_DEPS" -eq 1 ]] || { info "Системные пакеты не устанавливаются (--no-deps)"; return 0; }

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

    local rc=0
    case "$PKG" in
        apt)
            info "apt: ${apt_pkgs[*]}"
            export DEBIAN_FRONTEND=noninteractive
            run_priv apt-get update -qq || rc=$?
            run_priv apt-get install -y -qq --no-install-recommends "${apt_pkgs[@]}" || rc=$?
            find_python >/dev/null || run_priv apt-get install -y -qq python3.11 python3.11-venv || true
            ;;
        dnf)    info "dnf: установка пакетов"; run_priv dnf install -y -q "${dnf_pkgs[@]}" || rc=$? ;;
        yum)    info "yum: установка пакетов"; run_priv yum install -y -q "${dnf_pkgs[@]}" || rc=$? ;;
        apk)    info "apk: установка пакетов"; run_priv apk add --no-cache "${apk_pkgs[@]}" || rc=$? ;;
        pacman) info "pacman: установка пакетов"; run_priv pacman -Sy --noconfirm --needed "${pac_pkgs[@]}" || rc=$? ;;
        zypper) info "zypper: установка пакетов"; run_priv zypper --non-interactive install -y "${zyp_pkgs[@]}" || rc=$? ;;
        brew)   info "brew: python@3.12 nmap bind"; brew install python@3.12 nmap bind || rc=$? ;;
        *)      warn "Пакетный менеджер не распознан"; info "Нужны: python3 (>=3.11), ping, traceroute, nmap, dig, openssl"; return 0 ;;
    esac

    if [[ "$rc" -eq 127 ]]; then
        warn "Нет прав для установки пакетов (sudo недоступен) — пропускаю"
        info "Установите вручную: ping traceroute nmap dig openssl python3 (>=3.11)"
    elif [[ "$rc" -ne 0 ]]; then
        warn "Часть пакетов не установилась — продолжаю (проверка в конце)"
    else
        ok "Системные зависимости готовы"
    fi
}

fetch_sources() {
    local script_dir=""
    if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
        script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
    fi
    if [[ -n "$script_dir" && -f "$script_dir/pyproject.toml" && -d "$script_dir/src" ]]; then
        SRC_DIR="$script_dir"
        info "Исходники: локальный каталог $SRC_DIR"
        return 0
    fi
    command -v tar >/dev/null 2>&1 || die "Нужен tar для распаковки"
    local tmp url
    tmp="$(mktemp -d)"
    url="https://github.com/${REPO}/archive/${VERSION}.tar.gz"
    info "Скачиваю исходники: $url"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" -o "$tmp/src.tar.gz" || die "Не удалось скачать $url"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$tmp/src.tar.gz" "$url" || die "Не удалось скачать $url"
    else
        die "Нужен curl или wget"
    fi
    mkdir -p "$tmp/src"
    tar -xzf "$tmp/src.tar.gz" -C "$tmp/src" --strip-components=1 || die "Ошибка распаковки архива"
    SRC_DIR="$tmp/src"
    ok "Исходники распакованы"
}

install_app() {
    local py
    py="$(find_python)" || die "Не найден Python >= 3.11. Установите его и повторите запуск."
    info "Python: $("$py" --version 2>&1)"

    mkdir -p "$PREFIX" "$BIN_DIR"
    if [[ "$SRC_DIR" != "$PREFIX" ]]; then
        info "Копирую файлы в $PREFIX"
        cp -a "$SRC_DIR/." "$PREFIX/"
    fi

    if [[ ! -x "$VENV/bin/python" ]]; then
        info "Создаю venv: $VENV"
        "$py" -m venv "$VENV" || die "Не удалось создать venv (нужен python3-venv)"
    fi
    info "Устанавливаю пакет и зависимости (pip)"
    "$VENV/bin/pip" install --upgrade --quiet pip wheel setuptools
    "$VENV/bin/pip" install --upgrade --quiet --no-cache-dir -e "${PREFIX}[raw]"
    ok "Приложение установлено"
}

configure() {
    mkdir -p "$CONFIG_DIR" "$DATA_DIR" "$REPORTS_DIR" "$LOG_DIR" 2>/dev/null || true
    if [[ ! -f "$CONFIG_DIR/settings.yaml" ]]; then
        cp "$PREFIX/config/settings.yaml" "$CONFIG_DIR/settings.yaml"
        ok "Создан settings.yaml"
    else
        info "settings.yaml уже существует — сохраняю"
    fi
    if [[ ! -f "$CONFIG_DIR/secrets.yaml" ]]; then
        cp "$PREFIX/config/secrets.yaml" "$CONFIG_DIR/secrets.yaml"
        ok "Создан secrets.yaml"
    else
        info "secrets.yaml уже существует — сохраняю"
    fi
    chmod 600 "$CONFIG_DIR/secrets.yaml" 2>/dev/null || true

    "$VENV/bin/python" - "$CONFIG_DIR" "$DATA_DIR" "$REPORTS_DIR" "$LOG_DIR" \
        "$WITH_WEB" "$WEB_HOST" "$WEB_PORT" "$WITH_TELEMETRY" <<'PY'
import sys
from pathlib import Path

import yaml

config_dir = Path(sys.argv[1])
data_dir, reports_dir, log_dir = sys.argv[2], sys.argv[3], sys.argv[4]
web_enabled, web_host, web_port = sys.argv[5] == "1", sys.argv[6], int(sys.argv[7])
telemetry_enabled = sys.argv[8] == "1"

settings_path = config_dir / "settings.yaml"
settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
settings.setdefault("general", {}).update(
    {"data_dir": data_dir, "reports_dir": reports_dir, "log_dir": log_dir}
)
settings.setdefault("web", {}).update(
    {"enabled": web_enabled, "host": web_host, "port": web_port}
)
settings.setdefault("telemetry", {})["enabled"] = telemetry_enabled
settings_path.write_text(
    yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding="utf-8"
)
PY

    info "Данные: $DATA_DIR | Отчёты: $REPORTS_DIR | Журналы: $LOG_DIR"
    if [[ "$WITH_WEB" -eq 1 ]]; then
        ok "Веб-дашборд включён: http://$WEB_HOST:$WEB_PORT/"
    fi
    if [[ "$WITH_TELEMETRY" -eq 1 ]]; then
        info "Анонимная статистика включена (выключить: tspu-monitor telemetry disable)"
    else
        info "Анонимная статистика отключена"
    fi
}

write_wrapper() {
    cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
# Авто-сгенерировано установщиком TSPU Monitor.
export TSPU_CONFIG_DIR="$CONFIG_DIR"
exec "$VENV/bin/tspu-monitor" "\$@"
EOF
    chmod 755 "$WRAPPER"
    ok "CLI: $WRAPPER"
}

install_service() {
    [[ "$WITH_SERVICE" -eq 1 ]] || { info "Сервис не создаётся (--no-service или режим без root)"; return 0; }

    if [[ "$OS_KERNEL" == "Darwin" ]]; then
        local plist="$HOME/Library/LaunchAgents/ru.fairen8.tspu-monitor.plist"
        info "launchd: $plist"
        mkdir -p "$HOME/Library/LaunchAgents"
        cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ru.fairen8.tspu-monitor</string>
  <key>ProgramArguments</key><array><string>$WRAPPER</string><string>daemon</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/launchd.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/launchd.log</string>
</dict></plist>
EOF
        launchctl unload "$plist" >/dev/null 2>&1 || true
        launchctl load -w "$plist" >/dev/null 2>&1 || warn "launchctl: не удалось загрузить агент"
        ok "launchd-агент загружен"
    elif command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
        local unit="/etc/systemd/system/${SERVICE_NAME}.service"
        info "systemd: $unit"
        run_priv tee "$unit" >/dev/null <<EOF
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
        run_priv systemctl daemon-reload
        run_priv systemctl enable --now "${SERVICE_NAME}.service" || warn "Не удалось запустить сервис"
        ok "Сервис запущен: systemctl status ${SERVICE_NAME}"
    elif command -v rc-service >/dev/null 2>&1; then
        local init="/etc/init.d/${SERVICE_NAME}"
        info "OpenRC: $init"
        run_priv tee "$init" >/dev/null <<EOF
#!/sbin/openrc-run
name="TSPU Monitor"
command="$WRAPPER"
command_args="daemon$([[ "$WITH_WEB" -eq 1 ]] && echo " --web")"
command_background=true
pidfile="/run/${SERVICE_NAME}.pid"
output_log="$LOG_DIR/service.log"
error_log="$LOG_DIR/service.log"
depend() { need net; }
EOF
        run_priv chmod 755 "$init"
        run_priv rc-update add "$SERVICE_NAME" default >/dev/null 2>&1 || true
        run_priv rc-service "$SERVICE_NAME" restart >/dev/null 2>&1 || warn "Не удалось запустить сервис"
        ok "Сервис запущен: rc-service $SERVICE_NAME status"
    else
        warn "systemd/OpenRC/launchd не найден — запускайте вручную: tspu-monitor daemon"
    fi
}

uninstall_all() {
    info "Удаление TSPU Monitor"
    if [[ "$OS_KERNEL" == "Darwin" ]]; then
        local plist="$HOME/Library/LaunchAgents/ru.fairen8.tspu-monitor.plist"
        launchctl unload "$plist" >/dev/null 2>&1 || true
        rm -f "$plist"
    fi
    if command -v systemctl >/dev/null 2>&1; then
        run_priv systemctl disable --now "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
        run_priv rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
        run_priv systemctl daemon-reload >/dev/null 2>&1 || true
    fi
    if command -v rc-service >/dev/null 2>&1; then
        run_priv rc-service "$SERVICE_NAME" stop >/dev/null 2>&1 || true
        run_priv rc-update del "$SERVICE_NAME" default >/dev/null 2>&1 || true
        run_priv rm -f "/etc/init.d/${SERVICE_NAME}"
    fi
    rm -f "$WRAPPER"
    rm -rf "$PREFIX"
    if [[ "$PURGE" -eq 1 && "$ROOT_MODE" -eq 1 ]]; then
        run_priv rm -rf /var/lib/tspu-monitor /var/log/tspu-monitor
        ok "Удалено вместе с данными и журналами (--purge)"
    else
        ok "Удалено (данные сохранены; полное удаление: --uninstall --purge)"
    fi
}

summary() {
    local installed_version="?"
    installed_version="$("$VENV/bin/tspu-monitor" version 2>/dev/null | awk '{print $NF}' || true)"
    local service_line="не создан"
    if [[ "$WITH_SERVICE" -eq 1 ]]; then
        if [[ "$OS_KERNEL" == "Darwin" ]]; then
            service_line="launchd-агент (ru.fairen8.tspu-monitor)"
        elif command -v systemctl >/dev/null 2>&1; then
            service_line="systemctl status ${SERVICE_NAME}"
        elif command -v rc-service >/dev/null 2>&1; then
            service_line="rc-service ${SERVICE_NAME} status"
        fi
    fi

    echo
    printf '%s=================================================================%s\n' "$C_GRN" "$C_RST"
    printf '%s TSPU Monitor %s установлен%s\n' "$C_GRN" "$installed_version" "$C_RST"
    printf '%s=================================================================%s\n' "$C_GRN" "$C_RST"
    printf ' Режим:   %s\n' "$([[ "$ROOT_MODE" -eq 1 ]] && echo root || echo 'пользователь (без root)')"
    printf ' Версия:  %s\n' "$installed_version"
    printf ' Каталог: %s\n' "$PREFIX"
    printf ' CLI:     %s\n' "$WRAPPER"
    printf ' Конфиг:  %s/secrets.yaml\n' "$CONFIG_DIR"
    printf ' Данные:  %s\n' "$DATA_DIR"
    printf ' Сервис:  %s\n' "$service_line"
    printf ' Лог:     %s\n' "$LOG_FILE"
    echo
    echo " Что дальше:"
    echo "   1) Заполнить цели:  ${EDITOR:-nano} $CONFIG_DIR/secrets.yaml"
    echo "   2) Проверить:       tspu-monitor self-test"
    echo "   3) Диагностика:     tspu-monitor check"
    echo "   4) Отчёт:           tspu-monitor report"
    echo "   5) Дашборд:         tspu-monitor web --open"
    echo "   Обновление:         повторите запуск установщика"
    echo "   Удаление:           bash install.sh --uninstall [--purge]"
    if [[ "$ROOT_MODE" -eq 0 && ":$PATH:" != *":$BIN_DIR:"* ]]; then
        echo
        warn "$BIN_DIR отсутствует в PATH. Добавьте:"
        echo "     export PATH=\"$BIN_DIR:\$PATH\""
    fi
    echo
}

# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------
printf '%sTSPU Monitor Installer %s%s\n' "$C_CYN" "$INSTALLER_VERSION" "$C_RST"
echo "$C_DIM ОС: $OS_PRETTY | пакетный менеджер: ${PKG:-нет} | режим: $([[ "$ROOT_MODE" -eq 1 ]] && echo root || echo user)$C_RST"

step "Проверка окружения"
info "Каталог установки: $PREFIX"
info "Версия для установки: $VERSION"
if [[ -d "$PREFIX" ]]; then
    info "Найдена предыдущая установка — обновляю (конфиги сохраняются)"
fi

if [[ "$DO_UNINSTALL" -eq 1 ]]; then
    STEP_TOTAL=2
    step "Удаление"
    uninstall_all
    exit 0
fi

step "Установка системных зависимостей"
install_deps

step "Поиск Python (>= 3.11)"
find_python >/dev/null || die "Python >= 3.11 не найден. Установите: apt install python3 python3-venv | brew install python@3.12"

step "Получение исходников"
fetch_sources

step "Установка приложения"
install_app

step "Настройка конфигурации"
configure
write_wrapper

step "Сервис"
install_service

step "Проверка установки"
if "$VENV/bin/tspu-monitor" self-test; then
    ok "Самопроверка пройдена"
else
    warn "Самопроверка нашла замечания (см. вывод выше)"
fi

summary
