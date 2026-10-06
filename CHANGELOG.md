# Changelog

Все значимые изменения проекта документируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
проект следует [семантическому версионированию](https://semver.org/lang/ru/).

## [Unreleased]

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

[Unreleased]: https://github.com/Fairen8/tspu-monitor/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/Fairen8/tspu-monitor/releases/tag/v2.1.0
[2.0.0]: https://github.com/Fairen8/tspu-monitor/releases/tag/v2.0.0
