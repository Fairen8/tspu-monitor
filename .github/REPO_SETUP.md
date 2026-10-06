# Настройка и защита репозитория

Схема веток, защита `release`, сканеры и релизы. Автонастройка —
[`scripts/protect-repo.sh`](../scripts/protect-repo.sh) (идемпотентна).

> **Статус (2026-10-06):** `main` — без классической защиты (разработка);
> `release` — ruleset «Release branch: PR from main + Copilot review»;
> теги `v*` защищены; secret scanning, push protection и Dependabot
> включены; CI/CodeQL/Scorecard зелёные.

## 1. Ветки

| Ветка | Назначение | Правила |
|---|---|---|
| `main` | разработка | классической защиты нет — владелец пушит напрямую, CI на каждый push |
| `release` | релизы | ruleset: только PR (merge-commit), **только из `main`**, обязательно ревью Copilot |

## 2. Защита `release` (ruleset)

Ruleset «Release branch: PR from main + Copilot review»:

| Правило | Значение |
|---|---|
| Pull request | обязателен, разрешён только merge-commit |
| Источник PR | **только `main`** (обязательная проверка `Source is main`) |
| Обязательные проверки | `Source is main`, `Lint`, `Tests 3.11/3.12/3.13`, `Docker build`, `Secret scan` |
| Ревью Copilot | **обязательно** (Copilot автоматически ревьюит PR и пишет, что исправить) |
| Черновики | Copilot не ревьюит (`review_draft_pull_requests: false`) |
| Повторные пуши | Copilot не ревьюит заново (`review_on_push: false`) — экономия premium-запросов |
| Approve людей | не требуется (0) |
| Force-push / удаление | запрещено |
| Bypass | нет ни у кого (включая владельца) |

Как это работает: при открытии PR `main → release` ruleset запрашивает
ревью Copilot; до его завершения merge заблокирован. Для PR в `main`
никакого Copilot-ревью нет — токены не расходуются.

Если Copilot запросил правки: исправьте и нажмите **Re-request review**
(повторный пуш сам ревью не запускает — так экономнее premium-запросы),
либо отклоните ревью (dismiss) администратором и мержите. Для повторного
запуска вручную можно также снять/вернуть готовность PR (Ready for review).

> **Важно.** В Settings → Copilot → Code review **не включайте**
> «Automatically review pull requests»: эта настройка создаёт отдельный
> ruleset на ветку по умолчанию и ревьюит все PR. Скрипт
> `protect-repo.sh` удаляет такой авто-ruleset («Copilot review for
> default branch»), если он есть.

## 3. Скрипт настройки

```bash
bash scripts/protect-repo.sh Fairen8/tspu-monitor
```

Скрипт: Dependabot alerts → secret scanning/push protection → снимает
защиту `main` и классическую защиту `release` → создаёт/обновляет ruleset
release (с Copilot-ревью) → удаляет авто-Copilot ruleset для main →
создаёт ruleset тегов `v*`.

## 4. Безопасность

| Функция | Как включено |
|---|---|
| Dependabot alerts + security updates | `protect-repo.sh` |
| Dependabot version updates | `.github/dependabot.yml` |
| Secret scanning + push protection | `protect-repo.sh` |
| CodeQL | workflow `codeql.yml` |
| Dependency review | workflow `dependency-review.yml` |
| OpenSSF Scorecard (артефакт, без шума в PR) | workflow `scorecard.yml` |
| gitleaks в CI | workflow `ci.yml`, job `Secret scan` |
| Пиннинг actions по SHA | все workflow, обновляет Dependabot |

## 5. Actions, секреты, GHCR

* Workflow permissions — read-only; release-workflow запрашивает права сам.
* Обязательных секретов нет (используется `GITHUB_TOKEN`).
* GHCR-пакет `tspu-monitor` — публичный (проверка:
  `docker pull ghcr.io/fairen8/tspu-monitor:latest`).

## 6. Релизы

```bash
# добавить раздел [X.Y.Z] в CHANGELOG.md, затем:
bash scripts/release.sh 2.1.0
```

Скрипт бампает версию, гоняет ruff/pytest, коммитит в `main`, пушит,
открывает PR `main → release` и включает auto-merge. После мержа
**Publish release** создаёт тег, GitHub Release (wheel/sdist/.deb/.pyz/
SHA256SUMS) и мультиархитектурный образ. Повторная публикация версии
пропускается. Пересборка: `gh workflow run release.yml -f tag=vX.Y.Z`.

## 7. Чеклист

- [x] Публичный репозиторий, описание и topics
- [x] Dependabot alerts + security updates
- [x] Secret scanning + push protection (0 алертов)
- [x] CodeQL (v4), Dependency review, Scorecard
- [x] gitleaks в CI + pre-commit
- [x] `main` без защиты (прямые пуши владельца), CI на каждый push
- [x] `release` — ruleset: PR из `main` + обязательное ревью Copilot
- [x] Авто-ruleset Copilot для main удалён (экономия токенов)
- [x] Теги `v*` защищены
- [x] Релиз v2.1.0: артефакты, установщики, образ
- [x] GHCR-пакет публичный
- [ ] Подписанные коммиты и 2FA (рекомендуется)

## 8. Инциденты

* **Утёк секрет:** отзовите токен, удалите из истории (`git filter-repo`),
  заведите Security Advisory.
* **Компрометация CI:** отключите workflow, ротируйте токены, проверьте Audit log.
* **Уязвимость в коде:** Private Vulnerability Reporting (см.
  [`SECURITY.md`](../SECURITY.md)).
