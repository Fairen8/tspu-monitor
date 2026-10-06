#!/usr/bin/env bash
#
# Настройка защиты репозитория TSPU Monitor (идемпотентно).
#
# Итоговая схема:
#   main    — разработка, классической защиты нет (прямые пуши владельца);
#   release — ruleset: только PR (merge-commit), обязательная проверка
#             «Source is main» и CI-checks, обязательное ревью Copilot
#             (review_on_push=false, черновики не ревьюятся), запрет
#             force-push и удаления;
#   теги v* — запрет удаления и перезаписи;
#   Dependabot alerts + security updates, secret scanning + push protection.
#
# Требуется: gh (авторизован с правами администратора репозитория).
#
# Использование:
#   bash scripts/protect-repo.sh                     # репозиторий из gh
#   bash scripts/protect-repo.sh Fairen8/tspu-monitor
#
set -euo pipefail

red()  { printf '\033[31m%s\033[0m\n' "$*" >&2; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
info() { printf '[*] %s\n' "$*"; }
warn() { printf '\033[33m[!]\033[0m %s\n' "$*"; }

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

# ---------------------------------------------------------------------------
info "1/6 Включаю Dependabot-оповещения об уязвимостях"
gh api -X PUT "repos/${OWNER}/${NAME}/vulnerability-alerts" >/dev/null || \
    warn "Не удалось включить Dependabot alerts"

info "2/6 Включаю secret scanning и push protection"
gh api -X PATCH "repos/${OWNER}/${NAME}" --input - >/dev/null <<'JSON' || \
    warn "Secret scanning недоступен на текущем тарифе"
{
  "security_and_analysis": {
    "secret_scanning": { "status": "enabled" },
    "secret_scanning_push_protection": { "status": "enabled" },
    "dependabot_security_updates": { "status": "enabled" }
  }
}
JSON

info "3/6 Снимаю классическую защиту ветки main (разработка)"
gh api -X DELETE "repos/${OWNER}/${NAME}/branches/main/protection" >/dev/null 2>&1 || true

info "4/6 Снимаю классическую защиту release (заменяется ruleset)"
gh api -X DELETE "repos/${OWNER}/${NAME}/branches/release/protection" >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
info "5/6 Ruleset для release: PR из main + ревью Copilot"
RELEASE_NAME="Release branch: PR from main + Copilot review"
RELEASE_ID="$(gh api "repos/${OWNER}/${NAME}/rulesets" \
    --jq ".[] | select(.name == \"${RELEASE_NAME}\") | .id" 2>/dev/null | head -n1 || true)"

apply_release_ruleset() {
    gh api -X "$1" "$2" --input - >/dev/null <<'JSON'
{
  "name": "Release branch: PR from main + Copilot review",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [],
  "conditions": {
    "ref_name": { "include": ["refs/heads/release"], "exclude": [] }
  },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "pull_request",
      "parameters": {
        "allowed_merge_methods": ["merge"],
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_approving_review_count": 0,
        "required_review_thread_resolution": false
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": false,
        "required_status_checks": [
          { "context": "Source is main" },
          { "context": "Lint" },
          { "context": "Tests 3.11" },
          { "context": "Tests 3.12" },
          { "context": "Tests 3.13" },
          { "context": "Docker build" },
          { "context": "Secret scan" }
        ]
      }
    },
    {
      "type": "copilot_code_review",
      "parameters": {
        "review_draft_pull_requests": false,
        "review_on_push": false
      }
    }
  ]
}
JSON
}

if [[ -n "${RELEASE_ID}" ]]; then
    apply_release_ruleset PUT "repos/${OWNER}/${NAME}/rulesets/${RELEASE_ID}"
else
    apply_release_ruleset POST "repos/${OWNER}/${NAME}/rulesets"
fi

# ---------------------------------------------------------------------------
info "6/6 Rulesets: авто-ревью Copilot для main и защита тегов"
AUTO_ID="$(gh api "repos/${OWNER}/${NAME}/rulesets" \
    --jq '.[] | select(.name == "Copilot review for default branch") | .id' \
    2>/dev/null | head -n1 || true)"
if [[ -n "${AUTO_ID}" ]]; then
    gh api -X DELETE "repos/${OWNER}/${NAME}/rulesets/${AUTO_ID}" >/dev/null || true
    warn "Удалён авто-ruleset Copilot для main (экономия токенов)"
fi

TAG_ID="$(gh api "repos/${OWNER}/${NAME}/rulesets" \
    --jq '.[] | select(.name == "Protect release tags") | .id' \
    2>/dev/null | head -n1 || true)"
if [[ -z "${TAG_ID}" ]]; then
    gh api -X POST "repos/${OWNER}/${NAME}/rulesets" --input - >/dev/null <<'JSON'
{
  "name": "Protect release tags",
  "target": "tag",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/tags/v*"], "exclude": [] } },
  "rules": [ { "type": "deletion" }, { "type": "non_fast_forward" } ]
}
JSON
fi

grn "Готово."
info "Проверьте: https://github.com/${OWNER}/${NAME}/settings/rules"
info "Важно: в Settings → Copilot → Code review не включайте глобальное"
info "авто-ревью всех PR — ревью настроено только для release (ruleset)."
