# TSPU Monitor 2

[![CI](https://github.com/Fairen8/tspu-monitor/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Fairen8/tspu-monitor/actions/workflows/ci.yml)
[![Release](https://github.com/Fairen8/tspu-monitor/actions/workflows/release.yml/badge.svg)](https://github.com/Fairen8/tspu-monitor/releases)
[![Docker](https://img.shields.io/badge/docker-ghcr.io-blue)](https://github.com/Fairen8/tspu-monitor/pkgs/container/tspu-monitor)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Консольное приложение для диагностики DPI/TSPU-блокировок.**
Определяет уровень блокировок, типы фильтрации, уровень обрывов и причины;
тестирует популярные протоколы; автоматизируется через расписание, webhook,
JSON и exit-коды; разворачивается в Docker, LXC (Proxmox) и на bare-metal.

> **Статус:** 2.0.x — beta. CLI, конфигурация и JSON-схемы стабилизируются
> к 2.1; обратная связь и pull request'ы приветствуются.

---

## Возможности

**Диагностика**

* **Уровень блокировки**: 0–100 баллов и 5 градаций (нет / низкий / средний / высокий / полный).
* **Типы блокировок**: подмена DNS, фильтрация DNS/DoH, фильтрация по SNI,
  инъекция RST, вмешательство в TLS, блокировка IP/порта/UDP/ICMP, детект
  VPN-протокола, шейпинг, MTU-фильтр, страница-заглушка, блокировка QUIC,
  replay-cache, блокировка cover-ресурса, ошибка конфигурации.
* **Уровень обрывов**: редкие / периодические / частые / постоянные —
  по нескольким попыткам подключения (`samples`).
* **Причины и рекомендации**: человекочитаемый разбор каждой аномалии.

**Протоколы и пробы**

* Сеть: ICMP (потери, RTT, Path MTU), TCP connect, TCP-скан, UDP-зонды, DNS/DoH, QUIC (HTTP/3).
* Веб: HTTP/HTTPS, страницы-заглушки, дифференциальный SNI, TLS handshake, скорость загрузки.
* VPN: WireGuard handshake, AmneziaWG (junk-пакеты S1/S2), OpenVPN reset,
  Shadowsocks (энтропия + replay-cache), XRay/VLESS Reality (real/bogus/cover SNI).

**Автоматизация**

* Периодические проверки и еженедельный отчёт (встроенный планировщик).
* **Webhook** с JSON-payload при превышении порога блокировок.
* **JSON-вывод** и предсказуемые **exit-коды** (0/1/2/3) для скриптов и CI.
* Опциональный **Telegram-бот** с прокси/zapret (только Telegram; пробы всегда напрямую).

**Интерфейсы**

* Консоль с прогрессом проверок и цветным разбором аномалий.
* **Веб-дашборд** и REST API (`/api/summary`, `/api/runs`, `/api/check`, `/metrics`).

**Развёртывание**

* Docker / docker compose (healthcheck, CAP_NET_RAW).
* `.deb`-пакет, переносимый `.pyz`, wheel/sdist.
* LXC Proxmox, systemd-сервис, идемпотентный `install.sh`.

---

## Установка

### Одна команда (Linux и macOS)

Скрипт сам определит дистрибутив (Debian/Ubuntu, RHEL/Fedora, Alpine,
Arch, openSUSE, macOS), поставит зависимости, создаст venv, CLI и сервис:

```bash
curl -fsSL https://raw.githubusercontent.com/Fairen8/tspu-monitor/main/install.sh | sudo bash
```

Полезные варианты:

```bash
# конкретная версия
curl -fsSL .../install.sh | sudo bash -s -- --version v2.1.0
# сразу с веб-дашбордом (на 127.0.0.1:8787)
curl -fsSL .../install.sh | sudo bash -s -- --with-web
# без сервиса (только CLI)
curl -fsSL .../install.sh | sudo bash -s -- --no-service
# удаление
curl -fsSL .../install.sh | sudo bash -s -- --uninstall
```

Установщик также приложен к каждому релизу:
`https://github.com/Fairen8/tspu-monitor/releases/latest/download/install.sh`
(для конкретной версии добавьте `--version vX.Y.Z`).

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/Fairen8/tspu-monitor/main/install.ps1 | iex
```

Либо скачайте `install.cmd` со страницы [релиза](https://github.com/Fairen8/tspu-monitor/releases)
и запустите **двойным кликом** — окно останется открытым, установщик сам
поставит Python 3.11+ (winget, при необходимости — установщик python.org)
и всё настроит.

В режиме `irm | iex` параметры задаются переменными окружения:
`TSPU_PREFIX`, `TSPU_VERSION`, `TSPU_WITH_WEB=1`, `TSPU_NO_TELEMETRY=1`,
`TSPU_UNINSTALL=1`. Для автоматизации (CI): `-NoPause` или `TSPU_NO_PAUSE=1`.

> **Windows:** shell-скрипты (`install.sh`, `deploy/*`) и сетевые пробы
> **не работают** — нужны Linux-утилиты (`ping -M`, `traceroute`, `nmap`,
> `dig`) и `CAP_NET_RAW`. `install.ps1` даёт только CLI, конфигурацию,
> отчёты и дашборд. Для диагностики используйте Linux: Docker, LXC,
> `.deb` или `install.sh`.

### Docker

```bash
git clone https://github.com/Fairen8/tspu-monitor.git
cd tspu-monitor

nano config/secrets.yaml               # цели и, при необходимости, Telegram
docker compose up -d --build
docker compose exec tspu-monitor tspu-monitor check
```

Дашборд в Docker: включите `web.enabled: true`, раскомментируйте порт
`127.0.0.1:8787:8787` в `docker-compose.yml` и перезапустите.

### Debian / Ubuntu (.deb)

Готовый пакет — во вложениях [релиза](https://github.com/Fairen8/tspu-monitor/releases):

```bash
sudo apt install ./tspu-monitor_<версия>_all.deb
sudoedit /opt/tspu-monitor/config/secrets.yaml
sudo systemctl enable --now tspu-monitor
```

### Переносимый файл (zipapp)

Один файл, нужен только Python 3.11+:

```bash
curl -LO https://github.com/Fairen8/tspu-monitor/releases/latest/download/tspu-monitor-<версия>.pyz
chmod +x tspu-monitor-<версия>.pyz
./tspu-monitor-<версия>.pyz check
```

### pip / wheel

```bash
pip install ./tspu_monitor-<версия>-py3-none-any.whl
tspu-monitor check
```

### LXC (Proxmox)

На узле Proxmox:

```bash
bash deploy/lxc/proxmox-create.sh 210 tspu-monitor
```

Внутри контейнера:

```bash
cd /root/tspu-monitor
bash install.sh
nano /opt/tspu-monitor/config/secrets.yaml
tspu-monitor check
systemctl enable --now tspu-monitor
```

Подробнее: [`deploy/lxc/README.md`](deploy/lxc/README.md).

---

## Веб-дашборд

```bash
tspu-monitor web --open     # дашборд и открыть браузер
tspu-monitor daemon --web   # демон: расписание + отчёты + дашборд
```

Дашборд показывает уровень и историю блокировок, обрывы, типы, причины,
рекомендации, сценарии и детальные пробы и умеет запускать проверку
кнопкой. REST API: `/api/summary`, `/api/runs`, `/api/check`, `/metrics`
(Prometheus). По умолчанию слушает `127.0.0.1:8787`; для внешнего доступа
задайте `web.host` и токен `secrets.web.token`.

---

## Пример использования

```bash
# Все включённые сценарии (текстом)
tspu-monitor check

# Только WireGuard/Amnezia, больше повторов (оценка обрывов)
tspu-monitor check wireguard amnezia --samples 5

# Машиночитаемый вывод и код возврата для скриптов
tspu-monitor check --json
echo $?        # 0 — норма, 2 — деградация, 3 — блокировка

# Отчёт за 7 дней + отправка в Telegram
tspu-monitor report --send

# Webhook о блокировках
tspu-monitor check --webhook

# Управление сценариями и конфигом
tspu-monitor scenarios list
tspu-monitor scenarios enable xray
tspu-monitor config set scheduler.check_interval_minutes 30
tspu-monitor config validate

# Демон: расписание + отчёты + webhook + Telegram
tspu-monitor daemon
```

Пример вывода:

```text
Уровень блокировок: высокий (72/100), запуск 9f2c1a..., 14.3 с

HIGH   WEB / HTTPS — высокий (72/100)
   Типы: Инъекция RST; Фильтрация по SNI
   Цель: twitter.com
   Обрывы: периодические обрывы
   Причина: Инъекция RST со стороны DPI/TSPU
   Факт: TLS twitter.com:443[twitter.com]: handshake прерван (TLS alert)
   Рекомендация: смените SNI/домен или настройте технику Reality
```

---

## Конфигурация

Два файла:

* [`config/settings.yaml`](config/settings.yaml) — расписание, цели контроля,
  пороги классификации, webhook, отчёты;
* [`config/secrets.yaml`](config/secrets.yaml) — адреса VPN-серверов,
  Telegram-токен, токен webhook.

Полный справочник — в [DOCS.md](DOCS.md).

---

## Telegram за блокировкой

Bot API может быть недоступен из-за DPI. Монитор проксирует **только
Telegram**; сетевые пробы всегда идут напрямую. Варианты: zapret на
хосте/роутере (прозрачно) или `telegram.proxy` (HTTP/SOCKS5).
Инструкция: [`deploy/zapret/README.md`](deploy/zapret/README.md).

---

## Приватность

Анонимная статистика **включена по умолчанию** и отключается одной командой:

```bash
tspu-monitor telemetry disable    # или при установке: --no-telemetry
```

Отправляются только обезличенные метрики (версия, ОС, уровни, типы
блокировок); адреса, хосты и секреты не передаются. Сбои отправки
игнорируются молча. Подробнее — [DOCS.md](DOCS.md#17-анонимная-статистика).

---

## Документация

* **[DOCS.md](DOCS.md)** — архитектура, модель диагноза, все пробы и
  сценарии, CLI, конфигурация, отчёты, автоматизация, устранение
  неполадок.
* [deploy/lxc/README.md](deploy/lxc/README.md) — установка в LXC/Proxmox.
* [deploy/zapret/README.md](deploy/zapret/README.md) — Telegram за блокировкой.
* [CHANGELOG.md](CHANGELOG.md) — история изменений.

## Сообщество

* [CONTRIBUTING.md](CONTRIBUTING.md) — как участвовать в разработке.
* [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — кодекс поведения.
* [SECURITY.md](SECURITY.md) — как приватно сообщить об уязвимости.

## Лицензия

[MIT](LICENSE). Инструмент предназначен для диагностики собственных
сервисов и измерения фильтрации; используйте ответственно и в рамках закона.
