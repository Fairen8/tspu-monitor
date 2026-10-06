#!/usr/bin/env bash
#
# Настройка защиты репозитория TSPU Monitor через GitHub CLI.
# Требуется: gh (авторизован с правами администратора репозитория).
#
# Что делает:
#   1. включает Dependabot-оповещения об уязвимостях;
#   2. включает secret scanning и push protection;
#   3. ставит branch protection на main:
#      - обязательный PR и 1 approve;
#      - обязательные зелёные проверки CI;
#      - запрет force-push и удаления ветки;
#      - обязательное разрешение обсуждений;
#   4. ставит branch protection на release (только PR из main,
#      прямые коммиты запрещены, обязательная проверка Source is main).
#
# Использование:
#   bash scripts/protect-repo.sh                 # репозиторий из gh repo view
#   bash scripts/protect-repo.sh Fairen8/tspu-monitor
#
set -euo pipefail

red()  { printf '\033[31m%s\033[0m\n' "$*" >&2; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
info() { printf '[*] %s\n' "$*"; }

if ! command -v gh >/dev/null 2>&1; then
    red "GitHub CLI (gh) не установлен: https://cli.github.com/"
    exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
    red "Выполните 'gh auth login' перед запуском."
    exit 1
fi

REPO="${1:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
OWNER="${REPO%%/*}"
NAME="${REPO##*/}"
info "Репозиторий: ${OWNER}/${NAME}"

info "1/3 Включаю Dependabot-оповещения об уязвимостях"
gh api -X PUT "repos/${OWNER}/${NAME}/vulnerability-alerts" >/dev/null

info "2/3 Включаю secret scanning и push protection"
gh api -X PATCH "repos/${OWNER}/${NAME}" --input - >/dev/null <<'JSON'
{
  "security_and_analysis": {
    "secret_scanning": { "status": "enabled" },
    "secret_scanning_push_protection": { "status": "enabled" },
    "dependabot_security_updates": { "status": "enabled" }
  }
}
JSON

info "3/4 Настраиваю branch protection для main"
gh api -X PUT "repos/${OWNER}/${NAME}/branches/main/protection" --input - >/dev/null <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "Lint",
      "Tests 3.11",
      "Tests 3.12",
      "Tests 3.13",
      "Docker build",
      "Secret scan",
      "Анализ Python"
    ]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON

info "4/4 Настраиваю branch protection для release (только PR из main)"
if gh api "repos/${OWNER}/${NAME}/branches/release" >/dev/null 2>&1; then
    gh api -X PUT "repos/${OWNER}/${NAME}/branches/release/protection" --input - >/dev/null <<'JSON'
{
  "required_status_checks": {
    "strict": false,
    "contexts": [
      "Source is main",
      "Lint",
      "Tests 3.11",
      "Tests 3.12",
      "Tests 3.13",
      "Docker build",
      "Secret scan"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "required_approving_review_count": 0
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON
else
    red "Ветка release не найдена. Создайте её: git push origin main:release"
fi

grn "Готово. Проверьте настройки: https://github.com/${OWNER}/${NAME}/settings/branches"
info "Дополнительные шаги (вручную): 2FA для участников, подписанные коммиты,"
info "разрешения Actions (read-only по умолчанию) — см. .github/REPO_SETUP.md"
