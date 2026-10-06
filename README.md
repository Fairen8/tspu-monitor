# TSPU Monitor 2

[![CI](https://github.com/Fairen8/tspu-monitor/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Fairen8/tspu-monitor/actions/workflows/ci.yml)
[![Release](https://github.com/Fairen8/tspu-monitor/actions/workflows/release.yml/badge.svg)](https://github.com/Fairen8/tspu-monitor/releases)
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

**Развёртывание**

* Docker / docker compose (healthcheck, CAP_NET_RAW).
* LXC Proxmox (скрипт создания + capabilities).
* systemd-сервис и идемпотентный `install.sh`.

---

## Быстрый старт: Docker

```bash
git clone https://github.com/Fairen8/tspu-monitor.git
cd tspu-monitor

# 1. Укажите серверы в config/secrets.yaml
nano config/secrets.yaml

# 2. Запустите
docker compose up -d --build

# 3. Проверьте
docker compose exec tspu-monitor tspu-monitor self-test
docker compose exec tspu-monitor tspu-monitor check
docker compose exec tspu-monitor tspu-monitor report
```

## Быстрый старт: LXC (Proxmox)

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
