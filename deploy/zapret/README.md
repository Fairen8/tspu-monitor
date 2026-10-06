# Telegram за блокировкой: zapret или прокси

Telegram Bot API (`api.telegram.org`) в РФ, как правило, недоступен
напрямую. При этом **сетевые пробы TSPU Monitor должны идти напрямую**
(именно их мы и измеряем) — проксировать их нельзя.

Решение: проксировать **только Telegram**. Два варианта.

---

## Вариант 1: zapret на узле/роутере (прозрачно)

`zapret` (nfqws) десинхронизирует TLS-трафик к api.telegram.org, не
меняя маршрутизацию. Контейнер ничего не знает о нём — в
`secrets.yaml` остаётся `proxy: null`.

### Установка на узел Proxmox (Debian)

```bash
git clone --depth=1 https://github.com/bol-van/zapret.git /opt/zapret
cd /opt/zapret
./install_easy.sh
```

В интерактивном установщике:

* режим firewall — `nfqws` (NFQUEUE);
* фильтрация — **только TCP/443**;
* список хостов — добавьте домены Telegram:

```text
api.telegram.org
core.telegram.org
telegram.org
t.me
```

### Обход контейнера (важно)

Если zapret запущен на **хосте**, трафик контейнера, выходящий через
хост, уже десинхронизируется — дополнительная настройка не нужна.
Проверьте с узла:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://api.telegram.org
```

### Если zapret в самом контейнере

LXC не всегда позволяет NFQUEUE. Используйте запуск на хосте либо
Вариант 2. Если всё же запускаете внутри LXC — понадобятся
`CAP_NET_ADMIN` и загруженный модуль `nfnetlink_queue` на хосте.

### Проверка Telegram-бота

```bash
tspu-monitor self-test        # строка "telegram: настроен"
curl -s "https://api.telegram.org/bot<TOKEN>/getMe"
```

После запуска демона отправьте боту `/start`.

---

## Вариант 2: прокси только для Telegram

Подойдёт любой HTTP/SOCKS5-прокси: SSH-туннель, sing-box, 3x-ui,
Outline и т.п.

### SSH-туннель (самый простой)

На узле/в контейнере:

```bash
ssh -N -D 127.0.0.1:1080 user@ваш-vps
```

В `config/secrets.yaml`:

```yaml
telegram:
  enabled: true
  bot_token: "123456:AA..."
  allowed_users: [123456789]
  proxy: "socks5://127.0.0.1:1080"
```

Для SOCKS5 установите extra-зависимость:

```bash
# Docker
docker compose exec tspu-monitor pip install "aiohttp-socks>=0.8"

# LXC/bare-metal
/opt/tspu-monitor/venv/bin/pip install "tspu-monitor[socks]"
```

### HTTP(S)-прокси

```yaml
telegram:
  proxy: "http://user:pass@10.0.0.5:3128"
```

HTTP-прокси поддерживается `aiohttp` из коробки, доп. пакеты не нужны.

### Зеркало Bot API

Если доступен прокси-сервер Bot API (релей), укажите его адрес:

```yaml
telegram:
  api_base: "https://tg-relay.example.com"
```

---

## Что именно проксируется

| Канал | Маршрут |
|---|---|
| Telegram Bot API | через `telegram.proxy` / zapret |
| Webhook (n8n, Zabbix…) | напрямую (по умолчанию) |
| Сетевые пробы (ICMP/TCP/UDP/TLS/HTTP) | **всегда напрямую** |
| Загрузка обновлений/пакетов | напрямую |

Прокси задаётся только в `telegram.proxy`; глобального `HTTP_PROXY`
монитор не использует и не требует.

---

## Проверка после настройки

```bash
tspu-monitor --log-level DEBUG daemon       # смотрите строки tspu.telegram
# в другом терминале:
tspu-monitor logs --file telegram.log -n 50
```

Ожидаемые строки:

```text
Telegram-бот @your_bot запущен (прокси: socks5://127.0.0.1:1080)
```

Если видите `getMe не удался` — проверьте токен, прокси и доступность
`api.telegram.org` с узла.
