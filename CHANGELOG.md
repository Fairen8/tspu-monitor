# Changelog

Все значимые изменения проекта документируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
проект следует [семантическому версионированию](https://semver.org/lang/ru/).

## [Unreleased]

## [2.2.1] - 2026-10-06

### Fixed

* Windows-установщик: устранён фатальный сбой «Установка прервана: Python»
  при заглушке Python из Microsoft Store. В PowerShell 5.1 запись native-
  команды в stderr при `ErrorActionPreference=Stop` считалась ошибкой —
  теперь все внешние вызовы (python, pip, tar, winget) идут через
  безопасную обёртку, а сама заглушка `WindowsApps` пропускается.
* CI: регрессионный тест «битого python» (stderr + код 9009).

## [2.2.0] - 2026-10-06

### Changed

* Полностью переработаны установщики и их UX:
  * `install.sh` — 8 шагов с понятным выводом, работа **без root**
    (user-режим в `~/.local/share/tspu-monitor`, CLI в `~/.local/bin`,
    данные внутри каталога), поддержка **launchd** для macOS, лог
    установки, самопроверка в конце, флаги `--purge`, `--no-color`,
    аккуратные ошибки с номером строки и путём к логу;
  * `install.ps1` — 6 шагов, баннер с версией, лог
    `%TEMP%\tspu-monitor-install.log`, проверка `tspu-monitor version`
    после установки, корректные коды выхода и пауза;
  * CI: установка проверяется на чистом Debian (root **и** user-режим),
    на macOS (user-режим) и в трёх режимах Windows.

## [2.1.3] - 2026-10-06

### Changed

* Релизные установщики названы по платформам: `install-linux-macos.sh`,
  `install-windows.ps1`, `install-windows.cmd` (файлы в репозитории
  остаются `install.sh` / `install.ps1` / `install.cmd`, чтобы не ломать
  короткие ссылки). Таблица артефактов и ссылки в README/DOCS обновлены.

## [2.1.2] - 2026-10-06

### Added

* `install.cmd` — установщик Windows для запуска двойным кликом: сам
  находит/скачивает `install.ps1`, обходит политику выполнения и держит
  окно открытым.
* Документация: спецификация сервиса статистики
  `docs/statistics-service-spec.md` — контракт приёма, схемы событий
  `install`/`run`, словари значений, модель данных, публичный API,
  требования к дашборду и приватности.

### Fixed

* `install.ps1` больше не закрывает окно PowerShell: интерактивная пауза в
  конце (и при ошибке), корректный код выхода для скриптов, флаг
  `-NoPause` / переменная `TSPU_NO_PAUSE=1`.
* CI: смоук-тест установки через `install.cmd` (третий режим Windows).

## [2.1.1] - 2026-10-06

### Changed

* Защита веток: `main` — обязательный PR и CI-проверки, прямой push только
  администраторам (`enforce_admins=false`), write/maintain и внешние — через
  PR; `release` — ruleset с обязательным PR из `main`, CI-проверками и
  обязательным ревью Copilot (только для релизных PR; `review_on_push` и
  ревью черновиков отключены — экономия premium-запросов).
* Добавлены `.github/copilot-instructions.md` (фокус ревью) и обновлён
  `scripts/protect-repo.sh` под новую схему; авто-ruleset Copilot для
  ветки по умолчанию удаляется.

### Added

* Релизы: заметки содержат таблицу «Артефакты и платформы» с указанием ОС
  для каждого файла; Windows явно помечен как неподдерживаемый для
  shell-скриптов и сетевых проб (`scripts/release_platforms.md`).
* Анонимная статистика (`telemetry.enabled`, включена по умолчанию,
  отключается командой `tspu-monitor telemetry disable` или флагами
  установщиков `--no-telemetry`/`-NoTelemetry`). Отправляются
  только обезличенные метрики; сбои игнорируются молча.
* Универсальный установщик `install.sh` одной командой: автоопределение
  дистрибутива (Debian/Ubuntu, RHEL/Fedora, Alpine, Arch, openSUSE, macOS),
  установка зависимостей, venv, CLI и сервиса (systemd/OpenRC); флаги
  `--with-web`, `--version`, `--no-service`, `--uninstall`.
* Установщик `install.ps1` для Windows (CLI, конфигурация, дашборд).
* CI: shellcheck и bash-синтаксис, смоук-тесты установки на чистом
  Debian 12 и Windows; установщики прикладываются к релизу.

### Fixed

* Windows-установщик: авто-установка Python 3.11+ (winget, при
  необходимости — установщик python.org), безопасные ошибки в режиме
  `irm | iex` (терминал не закрывается), поддержка переменных окружения
  `TSPU_PREFIX`, `TSPU_VERSION`, `TSPU_REPO`, `TSPU_SRC`,
  `TSPU_WITH_WEB`, `TSPU_NO_TELEMETRY`, `TSPU_UNINSTALL`.
* CI: смоук-тест `install.ps1` в режиме `irm | iex`.

## [2.1.0] - 2026-10-06

### Added

* Веб-дашборд и REST API (`tspu-monitor web`, `daemon --web`): сводка,
  история уровня, сценарии с пробами, запуск проверки, метрики Prometheus.
* Артефакты релиза: `.deb`-пакет (`scripts/build-deb.sh`) и переносимый
  zipapp (`scripts/build-archive.sh`) вместе с wheel/sdist.
* Скрипты `scripts/changelog_section.py` (заметки релиза из CHANGELOG) и
  `scripts/pytest_summary.py` (markdown-сводка тестов), покрытые тестами.
* Авто-публикация релизов: ветка `release` (только PR из `main`),
  workflow-хранитель источника `release-guard` и `Publish release` после
  мержа (тег, GitHub Release, Docker-образ).
* Мультиархитектурные Docker-образы (`linux/amd64`, `linux/arm64`)
  с SBOM и provenance; pre-release для версий с суффиксом (`-rc.1`).
* Прогресс проверок в консоли (сценарии печатаются по мере завершения).

### Changed

* CI: отдельные job'ы Lint/Tests/Docker/Secret scan, отмена устаревших
  прогонов, кэш Docker-слоёв, JUnit-артефакты и сводки в Step Summary.
* Все GitHub Actions закреплены по commit SHA (Dependabot обновляет).
* Scorecard публикует отчёт только артефактом — без шума в PR.
* Dependabot: групповые обновления GitHub Actions.

### Fixed

* Безопасность: минимальная версия TLS 1.2 в python-fallback TLS-пробы;
  токены и учётные данные прокси редактируются в логах.

## [2.0.0] - 2026-10-06

Полная переработка проекта: консольное приложение с классификацией
блокировок и развёртыванием в Docker/LXC.

### Added

* Модель диагноза: уровень блокировки (0–100, 5 градаций), 17 типов
  блокировок, уровень обрывов, причины, доказательства, рекомендации.
* Движок классификации с набором детерминированных правил и тестами.
* Пробы: `icmp.ping`, `icmp.trace`, `icmp.mtu` (Path MTU), `tcp.connect`,
  `tcp.scan`, `udp.probe`, `wireguard.handshake`, `openvpn.reset`,
  `dns.resolve`, `dns.doh`, `tls.handshake`, `http.get`, `quic.initial`,
  `raw.ttl` (scapy, опционально).
* Сценарии: `web`, `dns`, `quic`, `wireguard`, `amnezia`, `openvpn`,
  `shadowsocks`, `xray` + шаблон пользовательского сценария.
* CLI: `check`, `report`, `status`, `scenarios`, `config`, `logs`,
  `self-test`, `daemon`, `version`; JSON-вывод и exit-коды 0/1/2/3.
* Автоматизация: встроенный планировщик, webhook-уведомления,
  TXT/JSON-отчёты с трендовым прогнозом.
* Опциональный Telegram-бот (собственный клиент Bot API) с поддержкой
  HTTP/SOCKS5-прокси; прокси применяется только к Telegram.
* Развёртывание: Dockerfile, docker-compose (healthcheck, CAP_NET_RAW),
  обвязка LXC Proxmox, systemd-юнит, идемпотентный `install.sh`.
* Документация: README, DOCS.md, руководства `deploy/lxc`,
  `deploy/zapret`, политика безопасности, contributing, шаблоны issue/PR.
* Инфраструктура: CI (ruff + pytest + сборка Docker), release-workflow
  (sdist/wheel + GHCR), локальные интеграционные тесты проб на мок-серверах.

### Changed

* Архитектура: пакет `src/tspu_monitor/`, конфигурация со значениями по
  умолчанию и merge YAML, ротация журналов (`main.log`, `probes.log`,
  `telegram.log`).
* Целевые каталоги: `/var/lib/tspu-monitor` (данные, отчёты),
  `/var/log/tspu-monitor` (журналы), `/etc/tspu-monitor` (Docker-конфиг).

### Removed

* Модуль `src/checks/`, `src/telegram/` и старый CLI-каркас v1.
* Зависимости `python-telegram-bot`, `apscheduler`, `requests`,
  `dnspython` (заменены собственными реализациями).
* Устаревшие `DOCS.docx` и `DOCS.pdf` (источник истины — DOCS.md).

### Security

* Секреты только в `secrets.yaml` (chmod 600), в репозитории —
  плейсхолдеры.
* Webhook-авторизация через Bearer-токен.
* Whitelist Telegram-пользователей и логирование неавторизованных попыток.

[Unreleased]: https://github.com/Fairen8/tspu-monitor/compare/v2.2.1...HEAD
[2.2.1]: https://github.com/Fairen8/tspu-monitor/releases/tag/v2.2.1
[2.2.0]: https://github.com/Fairen8/tspu-monitor/releases/tag/v2.2.0
[2.1.3]: https://github.com/Fairen8/tspu-monitor/releases/tag/v2.1.3
[2.1.2]: https://github.com/Fairen8/tspu-monitor/releases/tag/v2.1.2
[2.1.1]: https://github.com/Fairen8/tspu-monitor/releases/tag/v2.1.1
[2.1.0]: https://github.com/Fairen8/tspu-monitor/releases/tag/v2.1.0
[2.0.0]: https://github.com/Fairen8/tspu-monitor/releases/tag/v2.0.0
