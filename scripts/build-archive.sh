#!/usr/bin/env bash
#
# Сборка переносимого zipapp: один файл .pyz со всеми зависимостями.
# Требуется Python 3.11+ на машине запуска.
#
# Использование: bash scripts/build-archive.sh <версия> [каталог-вывода]
#
set -euo pipefail

VERSION="${1:?Укажите версию, например 2.1.0}"
OUTDIR="${2:-dist}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$OUTDIR"
OUTDIR="$(cd "$OUTDIR" && pwd)"

python3 -m pip install --quiet --no-cache-dir --target "$STAGE" "$ROOT"

cat > "$STAGE/__main__.py" <<'EOF'
"""Точка входа переносимого архива TSPU Monitor."""
import sys

from tspu_monitor.cli import main

sys.exit(main())
EOF

OUT="$OUTDIR/tspu-monitor-${VERSION}.pyz"
(cd "$STAGE" && python3 -m zipapp . -o "$OUT" -p "/usr/bin/env python3" -c)
chmod 755 "$OUT"
echo "Собран zipapp: $OUT"
