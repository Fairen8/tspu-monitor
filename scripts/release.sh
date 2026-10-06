#!/usr/bin/env bash
#
# Подготовка релиза TSPU Monitor:
#   1. проверяет наличие раздела в CHANGELOG.md и корректность версии;
#   2. обновляет версию в pyproject.toml и src/tspu_monitor/__init__.py;
#   3. прогоняет линтер и тесты;
#   4. коммитит, ставит тег v<версия> и отправляет в origin.
#
# Использование:
#   bash scripts/release.sh 2.1.0
#   bash scripts/release.sh 2.1.0 --dry-run
#
set -euo pipefail

VERSION="${1:-}"
MODE="${2:-}"

red()  { printf '\033[31m%s\033[0m\n' "$*" >&2; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
info() { printf '[*] %s\n' "$*"; }

if [[ -z "$VERSION" ]]; then
    red "Использование: bash scripts/release.sh <X.Y.Z> [--dry-run]"
    exit 1
fi
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    red "Версия должна быть в формате X.Y.Z (SemVer), например 2.1.0"
    exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TAG="v${VERSION}"

if ! grep -q "^## \[${VERSION}\]" CHANGELOG.md; then
    red "В CHANGELOG.md нет раздела '## [${VERSION}]'. Опишите изменения и повторите."
    exit 1
fi
if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
    red "Тег ${TAG} уже существует."
    exit 1
fi

run() {
    if [[ "$MODE" == "--dry-run" ]]; then
        info "[dry-run] $*"
    else
        "$@"
    fi
}

info "Версия: ${VERSION} (тег ${TAG})"

run sed -i -E "s/^version = \"[0-9]+\.[0-9]+\.[0-9]+\"/version = \"${VERSION}\"/" pyproject.toml
run sed -i -E "s/^__version__ = \"[0-9]+\.[0-9]+\.[0-9]+\"/__version__ = \"${VERSION}\"/" src/tspu_monitor/__init__.py

info "Проверки"
run python3 -m ruff check src tests
run python3 -m pytest -q

info "Коммит и тег"
run git add pyproject.toml src/tspu_monitor/__init__.py CHANGELOG.md
run git commit -m "chore(release): ${TAG}"
run git tag -a "${TAG}" -m "TSPU Monitor ${TAG}"
run git push origin HEAD --follow-tags

if [[ "$MODE" == "--dry-run" ]]; then
    info "Dry-run завершён, ничего не изменено."
else
    grn "Релиз ${TAG} отправлен. Workflow 'Release' соберёт wheel/sdist и Docker-образ."
fi
