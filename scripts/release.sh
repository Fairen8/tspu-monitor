#!/usr/bin/env bash
#
# Подготовка релиза TSPU Monitor.
#
# Поток:
#   1. проверяет CHANGELOG и отсутствие тега;
#   2. обновляет версию в pyproject.toml и src/tspu_monitor/__init__.py;
#   3. прогоняет линтер и тесты;
#   4. коммитит в main и пушит;
#   5. открывает PR main -> release и включает auto-merge.
#
# После мержа PR workflow "Publish release" автоматически создаст тег,
# GitHub Release (wheel/sdist/SHA256SUMS) и Docker-образ в GHCR.
#
# Использование:
#   bash scripts/release.sh 2.1.0
#   bash scripts/release.sh 2.1.0 --dry-run
#
set -euo pipefail

VERSION="${1:-}"
MODE="${2:-}"
RELEASE_BRANCH="release"

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
if ! command -v gh >/dev/null 2>&1; then
    red "Нужен GitHub CLI (gh): https://cli.github.com/"
    exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TAG="v${VERSION}"

if ! grep -q "^## \[${VERSION}\]" CHANGELOG.md; then
    red "В CHANGELOG.md нет раздела '## [${VERSION}]'. Опишите изменения и повторите."
    exit 1
fi
if git ls-remote --exit-code --tags origin "refs/tags/${TAG}" >/dev/null 2>&1; then
    red "Тег ${TAG} уже существует в origin."
    exit 1
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
    red "Рабочее дерево не чистое. Закоммитьте или отложите изменения."
    exit 1
fi
if [[ "$(git rev-parse --abbrev-ref HEAD)" != "main" ]]; then
    red "Релиз готовится из ветки main (сейчас: $(git rev-parse --abbrev-ref HEAD))."
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

info "Обновляю версии"
run sed -i -E "s/^version = \"[0-9]+\.[0-9]+\.[0-9]+\"/version = \"${VERSION}\"/" pyproject.toml
run sed -i -E "s/^__version__ = \"[0-9]+\.[0-9]+\.[0-9]+\"/__version__ = \"${VERSION}\"/" src/tspu_monitor/__init__.py

info "Проверки"
run python3 -m ruff check src tests
run python3 -m pytest -q

info "Коммит в main"
run git add pyproject.toml src/tspu_monitor/__init__.py CHANGELOG.md
run git commit -m "chore(release): ${TAG}"
run git push origin main

info "PR ${RELEASE_BRANCH} <- main"
run gh pr create \
    --base "${RELEASE_BRANCH}" \
    --head main \
    --title "release: ${TAG}" \
    --body "Автоматический релиз ${TAG}. После мержа будет создан тег, GitHub Release и Docker-образ."

info "Включаю auto-merge (если поддерживается репозиторием)"
if [[ "$MODE" == "--dry-run" ]]; then
    info "[dry-run] gh pr merge --auto --merge"
else
    gh pr merge --auto --merge || \
        info "Auto-merge недоступен: влейте PR вручную после зелёных проверок."
fi

if [[ "$MODE" == "--dry-run" ]]; then
    info "Dry-run завершён, ничего не изменено."
else
    grn "Готово. После мержа PR в ${RELEASE_BRANCH} релиз опубликуется автоматически."
fi
