# Настройка и защита репозитория

Чеклист владельца: публикация, защита `main`, сканеры, релизы.
Часть шагов автоматизирована — [`scripts/protect-repo.sh`](../scripts/protect-repo.sh).

> **Статус (2026-10-06):** репозиторий публичный; branch protection,
> secret scanning, push protection, Dependabot и защита тегов включены;
> CodeQL и Scorecard отработали успешно; открытых алертов нет.
> Осталось: сделать GHCR-пакет публичным (см. §5).

## 0. Предпосылки

* Установлен и авторизован [GitHub CLI](https://cli.github.com/): `gh auth login`.
* У аккаунта есть права **admin** на репозиторий.

## 1. Публикация

1. Settings → General → Danger Zone → **Change repository visibility** → Public.
2. Включите обязательную **2FA** для аккаунта/организации.
3. Перед публикацией убедитесь, что в истории нет секретов: история
   переписана в один чистый коммит без тестовых токенов и служебных авторов.
4. Добавьте описание и темы (Settings → General или `gh repo edit`):

   ```bash
   gh repo edit Fairen8/tspu-monitor \
     --description "Консольная диагностика DPI/TSPU-блокировок: уровень и типы блокировок, обрывы, причины" \
     --add-topic tspu --add-topic dpi --add-topic censorship --add-topic vpn \
     --add-topic wireguard --add-topic openvpn --add-topic xray --add-topic quic \
     --add-topic network-monitoring --add-topic docker --add-topic lxc --add-topic cli
   ```

## 2. Защита ветки `main`

После публикации:

```bash
bash scripts/protect-repo.sh Fairen8/tspu-monitor
```

Скрипт включает:

| Настройка | Значение |
|---|---|
| Обязательный Pull Request | да, 1 approve |
| Review от CODEOWNERS | да |
| Устаревшие approve сбрасываются | да |
| Обязательные status checks | `Python 3.11/3.12/3.13`, `Docker build`, `Secret scan (gitleaks)`, `Анализ Python` |
| Актуальная ветка (strict) | да |
| Линейная история | да |
| Разрешение обсуждений | да |
| Force-push / удаление ветки | запрещено |
| Администраторы | могут пушить напрямую (для релизного коммита) |

Вручную: Settings → Branches → Add branch protection rule — продублируйте
значения. Дополнительно рекомендуется:

* **Require signed commits** (подписанные коммиты);
* защита тегов `v*` (Settings → Tags → Add rule: запрет удаления/перезаписи).

## 3. Безопасность

| Функция | Как включить | Статус после публикации |
|---|---|---|
| Dependabot alerts | `scripts/protect-repo.sh` | включено (бесплатно) |
| Dependabot security updates | `scripts/protect-repo.sh` | включено |
| Dependabot version updates | `.github/dependabot.yml` | PR раз в неделю |
| Secret scanning | `scripts/protect-repo.sh` (бесплатно для public) | включено |
| Push protection | `scripts/protect-repo.sh` | включено |
| CodeQL | workflow `codeql.yml` (для public — бесплатно) | запускается на push/PR |
| Dependency review | workflow `dependency-review.yml` | запускается на PR |
| OpenSSF Scorecard | workflow `scorecard.yml` | еженедельно |
| gitleaks в CI | workflow `ci.yml`, job `Secret scan` | на каждый push/PR |
| pre-commit (ruff + gitleaks) | `.pre-commit-config.yaml` | локально у участников |

Локально:

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

## 4. Actions и секреты

* Settings → Actions → General → **Workflow permissions**: read-only по
  умолчанию; release-workflow запрашивает нужные права сам
  (`contents: write`, `packages: write`).
* **Allow GitHub Actions to create and approve pull requests** — выключить.
* Обязательных секретов нет: релиз использует встроенный `GITHUB_TOKEN`.
  Для уведомлений в Telegram добавьте `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`.
* Для деплоя используйте **Environments** с protection rules.

## 5. Пакет GHCR

По умолчанию контейнер-пакет остаётся приватным даже у публичного
репозитория. Сделайте его публичным: Profile → Packages →
`tspu-monitor` → Package settings → Change visibility → Public.

Через CLI (нужен токен с `read:packages`/`write:packages`):

```bash
gh auth refresh -s write:packages
gh api -X PATCH /user/packages/container/tspu-monitor \
  -f visibility=public
```

Проверка:

```bash
docker pull ghcr.io/fairen8/tspu-monitor:latest
docker run --rm ghcr.io/fairen8/tspu-monitor:latest version
```

## 6. Релизы

Версионирование — [SemVer](https://semver.org/lang/ru/); изменения — в
`CHANGELOG.md`.

```bash
bash scripts/release.sh 2.1.0            # боевой прогон
bash scripts/release.sh 2.1.0 --dry-run  # проверки без изменений
```

Тег `v*` запускает **Release**: `wheel`/`sdist`/`SHA256SUMS` в GitHub
Release и Docker-образ в GHCR. Ручная альтернатива:

```bash
git tag -a v2.0.1 -m "TSPU Monitor v2.0.1"
git push origin v2.0.1
```

## 7. Чеклист

- [x] Репозиторий публичный, есть описание и topics
- [x] Dependabot alerts + security updates
- [x] gitleaks в CI + pre-commit (работает и на PR)
- [x] Branch protection: PR + 1 approve + CODEOWNERS + обязательные checks
- [x] Secret scanning + push protection (0 алертов)
- [x] CodeQL (v4) и Dependency review настроены
- [x] OpenSSF Scorecard еженедельно
- [x] Workflow permissions — read-only, авто-approve выключен
- [x] Теги `v*` защищены ruleset «Protect release tags»
- [x] Раздел CHANGELOG и версия согласованы
- [x] Релиз v2.0.0: артефакты и Docker-образ собраны
- [ ] GHCR-пакет публичный (ожидает владельца, см. §5)
- [ ] Подписанные коммиты и обязательная 2FA (рекомендуется)

## 8. Инциденты

* **Утёк секрет:** отзовите токен (Telegram — BotFather, webhook — на
  стороне приёмника), удалите из истории (`git filter-repo`), заведите
  Security Advisory.
* **Компрометация CI:** отключите workflow, удалите self-hosted runner,
  ротируйте токены, проверьте Audit log.
* **Уязвимость в коде:** Private Vulnerability Reporting (см.
  [`SECURITY.md`](../SECURITY.md)).
