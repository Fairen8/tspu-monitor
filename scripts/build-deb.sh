#!/usr/bin/env bash
#
# Сборка .deb-пакета (Architecture: all) без внешних инструментов сборки.
#
# Использование: bash scripts/build-deb.sh <версия> [каталог-вывода]
#
set -euo pipefail

VERSION="${1:?Укажите версию, например 2.1.0}"
OUTDIR="${2:-dist}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "dpkg-deb не найден (нужен Debian/Ubuntu)" >&2
    exit 1
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$OUTDIR"
OUTDIR="$(cd "$OUTDIR" && pwd)"

PKG="$STAGE/tspu-monitor"
mkdir -p "$PKG/opt/tspu-monitor" \
         "$PKG/usr/local/bin" \
         "$PKG/lib/systemd/system" \
         "$PKG/DEBIAN" \
         "$OUTDIR"

# Файлы приложения (запуск из исходников системным python3).
cp -r "$ROOT/src" "$PKG/opt/tspu-monitor/"
cp -r "$ROOT/config" "$PKG/opt/tspu-monitor/"
cp "$ROOT/pyproject.toml" "$ROOT/README.md" "$ROOT/DOCS.md" \
   "$ROOT/CHANGELOG.md" "$ROOT/LICENSE" "$PKG/opt/tspu-monitor/"

# Обёртка CLI.
cat > "$PKG/usr/local/bin/tspu-monitor" <<'EOF'
#!/usr/bin/env bash
export TSPU_CONFIG_DIR="${TSPU_CONFIG_DIR:-/opt/tspu-monitor/config}"
export PYTHONPATH="/opt/tspu-monitor/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m tspu_monitor "$@"
EOF
chmod 755 "$PKG/usr/local/bin/tspu-monitor"

# systemd unit (venv не используется — системный python3).
sed 's#/opt/tspu-monitor/venv/bin/tspu-monitor#/usr/local/bin/tspu-monitor#' \
    "$ROOT/deploy/systemd/tspu-monitor.service" \
    > "$PKG/lib/systemd/system/tspu-monitor.service"

cat > "$PKG/DEBIAN/control" <<EOF
Package: tspu-monitor
Version: ${VERSION}
Section: net
Priority: optional
Architecture: all
Depends: python3 (>= 3.11), python3-yaml, python3-aiohttp
Recommends: python3-scapy, iputils-ping, traceroute, nmap, dnsutils, openssl, ca-certificates
Maintainer: Fairen8 <110852853+Fairen8@users.noreply.github.com>
Homepage: https://github.com/Fairen8/tspu-monitor
Description: Диагностика DPI/TSPU-блокировок
 Определяет уровень и типы блокировок, обрывов и их причины; тестирует
 популярные протоколы; поддерживает расписание, webhook, JSON, веб-дашборд
 и Telegram.
EOF

cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
mkdir -p /var/lib/tspu-monitor/data /var/lib/tspu-monitor/reports /var/log/tspu-monitor
chmod 600 /opt/tspu-monitor/config/secrets.yaml 2>/dev/null || true
systemctl daemon-reload >/dev/null 2>&1 || true
exit 0
EOF
chmod 755 "$PKG/DEBIAN/postinst"

cat > "$PKG/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
systemctl stop tspu-monitor.service >/dev/null 2>&1 || true
exit 0
EOF
chmod 755 "$PKG/DEBIAN/prerm"

cat > "$PKG/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
systemctl daemon-reload >/dev/null 2>&1 || true
exit 0
EOF
chmod 755 "$PKG/DEBIAN/postrm"

DEB="$OUTDIR/tspu-monitor_${VERSION}_all.deb"
dpkg-deb --build "$PKG" "$DEB" >/dev/null
echo "Собран deb: $DEB"
