# TSPU Monitor 2 — техническая документация

Полное руководство: архитектура, модель диагноза, пробы и сценарии,
CLI, конфигурация, отчёты, автоматизация, развёртывание и разработка.

Версия: **2.0.0**.

## Содержание

1. [Обзор](#1-обзор)
2. [Архитектура](#2-архитектура)
3. [Модель диагноза](#3-модель-диагноза)
4. [Правила классификации](#4-правила-классификации)
5. [Пробы](#5-пробы)
6. [Сценарии](#6-сценарии)
7. [CLI](#7-cli)
8. [Конфигурация](#8-конфигурация)
9. [Отчёты](#9-отчёты)
10. [Автоматизация](#10-автоматизация)
11. [Telegram за блокировкой](#11-telegram-за-блокировкой)
12. [Развёртывание](#12-развёртывание)
13. [Разработка](#13-разработка)
14. [Устранение неполадок](#14-устранение-неполадок)
15. [FAQ](#15-faq)
16. [Веб-дашборд и API](#16-веб-дашборд-и-api)
17. [Анонимная статистика](#17-анонимная-статистика)

---

## 1. Обзор

**TSPU Monitor** — консольное приложение (CLI) для активной диагностики
сетевой фильтрации: DPI, TSPU, операторских заглушек, VPN-блокировок.
Приложение регулярно выполняет сетевые пробы, классифицирует аномалии и
выдаёт структурированный диагноз:

| Что определяется | Как отображается |
|---|---|
| Уровень блокировки | 0–100 баллов + градация (нет/низкий/средний/высокий/полный) |
| Типы блокировок | 17 типов: DNS-спуфинг, SNI-фильтрация, RST-инъекция, … |
| Уровень обрывов | редкие / периодические / частые / постоянные |
| Причины | человекочитаемые объяснения с доказательствами |
| Рекомендации | что предпринять (сменить SNI, MTU, порт, протокол…) |

Ключевые принципы:

* **Никаких секретов в коде** — только `settings.yaml` и `secrets.yaml`.
* **Пробы не проксируются** — измеряем реальный путь до сервиса.
* **Всё машиночитаемо** — JSON на выходе, предсказуемые exit-коды.
* **Модульность** — новый протокол = один файл в `scenarios/custom/`.

---

## 2. Архитектура

```
                    +---------------------------+
                    |        Пользователь       |
                    +------+-------------+------+
                           |             |
                     CLI (argparse)   Telegram-бот
                           |        (опционально)
                           v             v
              +--------------------------------------+
              |               ENGINE                 |
              |  run(profiles, samples)              |
              |    -> ScenarioManager                |
              |    -> asyncio.gather(scenario.run()) |
              |    -> save data/run-<id>.json        |
              +----+-------------+-------------------+
                   |             |
          +--------v---+   +-----v------------------+
          |  SCENARIOS |   |   DiagnosisEngine      |
          |  web/dns/  |   |   правила -> Finding   |
          |  quic/vpn… |   |   -> Analysis          |
          +--------+---+   +------------------------+
                   |
          +--------v-----------------------------+
          |              PROBES                  |
          |  icmp.ping  tcp.connect  udp.probe   |
          |  dns.resolve  dns.doh  tls.handshake |
          |  http.get  quic.initial  raw.ttl     |
          |  wireguard.handshake  openvpn.reset  |
          +--------------------------------------+
                   |
          +--------v-----------------------------+
          |              СЕТЬ                    |
          |  VPN-серверы, DNS, сайты, webhook    |
          +--------------------------------------+

Потребители результата:
  Reporter  -> reports/report-*.txt / JSON
  Notifier  -> webhook (JSON)
  Bot       -> Telegram-сообщения и отчёты
  Scheduler -> периодический запуск
```

### Компоненты

| Модуль | Ответственность |
|---|---|
| `cli.py` | консольный интерфейс, exit-коды, вывод |
| `engine.py` | выбор сценариев, параллельный запуск, сохранение run-файлов |
| `scenarios/` | сценарии: набор проб + специфичные находки |
| `probes/` | атомарные сетевые пробы |
| `classification.py` | правила: результаты проб → тип/уровень/причины |
| `models.py` | модели данных (Analysis, ProbeResult, BlockType, …) |
| `reporter.py` | TXT/JSON-отчёты, прогноз |
| `notifier.py` | webhook-уведомления |
| `scheduler.py` | периодические проверки и недельный отчёт |
| `telegram_bot.py` | минимальный Bot API клиент (long polling, прокси) |
| `config.py` | загрузка/merge/сохранение конфигурации |

### Поток данных

1. `tspu-monitor check` (или scheduler) создаёт `Engine`.
2. `ScenarioManager` отдаёт включённые сценарии из `settings.yaml`.
3. Каждый сценарий строит список проб и запускает их параллельно.
4. Все результаты прогоняются через `DiagnosisEngine` (набор правил).
5. Формируется `Analysis`: score, level, types, disconnect, causes, evidence, recommendations.
6. `RunRecord` сохраняется в `data/run-<id>.json`.
7. `report` строит TXT/JSON из сохранённых запусков; webhook/Telegram получают уведомления.

---

## 3. Модель диагноза

### 3.1. Уровень блокировки (`BlockLevel`)

Score — сумма весов сработавших правил, ограниченная 100. Границы
настраиваются в `classification.level_thresholds`:

| Score | Уровень | Значение |
|---|---|---|
| 0 | NONE | блокировок нет |
| 1–24 | LOW | единичные аномалии |
| 25–49 | MEDIUM | частичная фильтрация/деградация |
| 50–69 | HIGH | устойчивая блокировка сервиса |
| 70–100 | FULL | полная блокировка (IP/сервис недоступен) |

### 3.2. Типы блокировок (`BlockType`)

| Значение | Русское название |
|---|---|
| `dns_spoof` | Подмена DNS |
| `dns_filter` | Фильтрация DNS/DoH |
| `sni_filter` | Фильтрация по SNI |
| `rst_injection` | Инъекция RST |
| `tls_interference` | Вмешательство в TLS |
| `ip_block` | Блокировка IP |
| `port_block` | Блокировка порта |
| `udp_block` | Блокировка UDP |
| `protocol_detect` | Детект VPN-протокола |
| `throttle` | Шейпинг / ограничение скорости |
| `mtu_filter` | Фильтрация по размеру пакета (MTU) |
| `http_plug` | Страница-заглушка |
| `quic_block` | Блокировка QUIC (UDP/443) |
| `icmp_block` | Блокировка ICMP |
| `replay_cache` | Кэш повторов (replay-cache) |
| `cover_block` | Блокировка cover-ресурса |
| `misconfig` | Ошибка конфигурации (не блокировка) |
| `unknown` | Неизвестная аномалия |

### 3.3. Уровень обрывов (`DisconnectLevel`)

Оценивается по нескольким попыткам (`samples >= 2`). Если попытка одна,
уровень не определяется (нельзя судить о стабильности).

| Доля неудач | Уровень |
|---|---|
| 0 | обрывов не обнаружено |
| 0 < x < 0.25 | редкие |
| 0.25 ≤ x < 0.5 | периодические |
| 0.5 ≤ x < 0.75 | частые |
| ≥ 0.75 | постоянные |

### 3.4. Причины и доказательства

`causes` — объяснения (например, «RST-инъекция на этапе TLS handshake»),
`evidence` — конкретные факты проб («TCP 203.0.113.1:443: мгновенный RST
(1 из 2)»), `recommendations` — действия. Всё дублируется в JSON.

---

## 4. Правила классификации

Правила описаны в `classification.py`. Ниже — карта «сигнал → тип → вес».

| Сигнал | Тип | Вес |
|---|---|---|
| ICMP 100% потерь | `icmp_block` | 10 |
| ICMP потери 50–99% | — | 8, обрывы ≥ периодических |
| ICMP потери 1–49% | — | 4, обрывы ≥ редких |
| Path MTU < 1280 | `mtu_filter` | 25 |
| Path MTU < 1450 | — | 6 |
| ICMP и все TCP недоступны | `ip_block` | 70 |
| TCP: мгновенный RST (< 5 мс) | `rst_injection` | 45 |
| TCP: refused, ни одного успеха | `port_block` | 30 |
| TCP: таймауты, ни одного успеха | `port_block` | 25 |
| TCP: часть попыток неудачна | — | 10, обрывы по доле |
| DNS: спуфинг (приватный IP / нет пересечения) | `dns_spoof` | 45 |
| DNS: системный резолвер не отвечает | `dns_filter` | 25 |
| DoH недоступен при работающем DNS | `dns_filter` | 20 |
| TLS: RST | `rst_injection` | 40 |
| TLS: alert/прерванный handshake | `tls_interference` | 35 |
| TLS: таймаут | `port_block` | 20 |
| SNI-дифференциал (real fail, bogus ok) | `sni_filter` | 50 |
| real fail при работающем cover | `sni_filter` | 45 |
| cover-ресурс недоступен | `cover_block` | 25 |
| HTTP: страница-заглушка | `http_plug` | 40 |
| HTTP: сброс соединения | `rst_injection` | 35 |
| HTTP/HTTPS: запрос не выполнен | `tls_interference` / — | 18 |
| Скорость < 256 КБ/с при загрузке ≥ 64 КБ | `throttle` | 15 |
| QUIC не отвечает при работающем TCP/443 | `quic_block` | 25 |
| Контрольный UDP не отвечает | `udp_block` | 25 |
| Большие UDP-пакеты не проходят, малые проходят | `mtu_filter` | 30 |
| WireGuard: ICMP port unreachable | `port_block` | 35 |
| OpenVPN: молчание при живых UDP-контроле и TCP | `protocol_detect` | 35 |
| Shadowsocks: RST после шума | `protocol_detect` | 30 |
| Повтор шума рвётся вдвое быстрее | `replay_cache` | 20 |
| Сертификат Reality ≠ cover | `misconfig` | 8 |
| Нет TLS-cover на 443 (OpenVPN) | `misconfig` | 5 |
| Критичный результат без правила | `unknown` | 20 |

Замечания:

* результаты с `data.skipped = true` игнорируются;
* контрольные пробы (`data.control = true`) оцениваются отдельно;
* пробы с `data.expected_silent = true` не считаются сбоем при молчании.

---

## 5. Пробы

Пробы лежат в `probes/`. Каждая возвращает `ProbeResult`:

```python
ProbeResult(
    probe="tcp.connect",          # имя пробы
    target="203.0.113.1:443",     # цель
    success=True,                 # успех пробы как таковой
    severity=Severity.INFO,       # info / warning / critical
    duration_ms=12.5,
    data={...},                   # структурированные данные
    raw="...",                    # сырой вывод (для отладки)
    error=None,
    timestamp="2026-10-06T09:00:00Z",
)
```

Специальные ключи `data`:

| Ключ | Значение |
|---|---|
| `skipped` | проба не выполнялась (нет root/scapy/бинарника) |
| `control` | контрольная проба (заведомо рабочий узел) |
| `expected_silent` | молчание — нормальное поведение |

### 5.1. Список проб

| Имя | Класс | Что делает |
|---|---|---|
| `icmp.ping` | `PingProbe` | ping: потери, RTT min/avg/max/mdev, jitter |
| `icmp.trace` | `TraceProbe` | traceroute: «стены» из звёздочек (middlebox) |
| `icmp.mtu` | `PathMtuProbe` | Path MTU через ping с DF |
| `tcp.connect` | `TcpConnectProbe` | N попыток connect: успехи, RST/refused/timeout, TTL, RTT |
| `tcp.scan` | `PortScanProbe` | nmap-скан портов (fallback: connect) |
| `udp.probe` | `UdpProbe` | UDP-зонд с payload; контрольный режим |
| `wireguard.handshake` | `WireGuardHandshakeProbe` | настоящий WG Type-1 init (148 Б), junk-пакеты AmneziaWG |
| `openvpn.reset` | `OpenVpnResetProbe` | валидный P_CONTROL_HARD_RESET_CLIENT_V2 |
| `dns.resolve` | `DnsResolveProbe` | системный + публичные резолверы, детект спуфинга |
| `dns.doh` | `DohProbe` | Cloudflare/Google/Quad9 DNS-over-HTTPS |
| `tls.handshake` | `TlsHandshakeProbe` | TLS с произвольным SNI (openssl, fallback Python ssl) |
| `http.get` | `HttpProbe` | HTTP/HTTPS GET: статус, заголовки, заглушки, скорость |
| `quic.initial` | `QuicProbe` | QUIC Version Negotiation (UDP/443), детект блокировки HTTP/3 |
| `raw.ttl` | `RawTtlProbe` | raw SYN через scapy: TTL/IP-ID (root/CAP_NET_RAW) |

Полезные функции: `build_dns_query`, `build_wireguard_handshake`,
`build_openvpn_reset`, `build_version_negotiation_trigger`,
`classify_tls_error`, `detect_plug_page`, `detect_filter_header`.

---

## 6. Сценарии

Сценарий — группа проб под один протокол/сервис. Встроенные сценарии:

| Имя | Title | Цель (secrets) | Основные пробы |
|---|---|---|---|
| `web` | WEB / HTTPS | blocked/control хосты | `http.get`, `tls.handshake` (real/cover) |
| `dns` | DNS | control/blocked хосты | `dns.resolve`, `dns.doh`, `udp.probe` (control) |
| `quic` | QUIC / HTTP3 | control/blocked хосты | `quic.initial`, `tcp.connect:443` |
| `wireguard` | WIREGUARD | `wireguard_server` | ping, `wireguard.handshake`, fallback-порты, UDP-контроль, TCP |
| `amnezia` | AMNEZIA WG | `amnezia_server` | WG без/с junk, UDP 1200/64 Б, Path MTU, UDP-контроль, TCP |
| `openvpn` | OPENVPN | `openvpn_server` | `openvpn.reset`, TCP 443/1194/8443, TLS-cover |
| `shadowsocks` | SHADOWSOCKS / OUTLINE | `shadowsocks_server` | TCP, энтропийный зонд ×2 (replay-cache) |
| `xray` | XRAY / VLESS REALITY | `xray_server` | TCP, TLS real/bogus/cover, сверка сертификатов |

Особенности:

* **`web`** — запросы к контрольным и реестровым хостам, детект заглушек,
  дифференциальный SNI (real против cover).
* **`dns`** — сравнение системного резолвера с публичными; отдельно DoH и
  контроль UDP/53.
* **`quic`** — пакет с неподдерживаемой версией ≥ 1200 Б (RFC 9000):
  здоровый QUIC-сервер обязан ответить Version Negotiation.
* **`amnezia`** — junk-пакеты имитируют S1–S4; Path MTU ищет занижение.
* **`openvpn`** — реальный reset-пакет; признаки DPI-детекта фиксируются
  только при живых UDP-контроле и TCP.
* **`shadowsocks`** — один и тот же блок случайных байтов отправляется
  дважды: ускоренный разрыв второго соединения = replay-cache.
* **`xray`** — три handshake (real/bogus/cover) и сравнение сертификатов.

Включение/выключение:

```bash
tspu-monitor scenarios list
tspu-monitor scenarios enable quic
tspu-monitor scenarios disable shadowsocks
```

Параметры сценария переопределяются в `settings.yaml`:

```yaml
scenarios:
  options:
    wireguard:
      port: 51820
      alt_ports: [443, 1194, 53]
    web:
      samples: 3
```

---

## 7. CLI

Общий синтаксис:

```
tspu-monitor [--config-dir DIR] [--log-level LEVEL] [--no-color] <команда> [аргументы]
```

| Команда | Назначение |
|---|---|
| `check [профили…]` | запустить проверки (все включённые или указанные) |
| `report` | сформировать отчёт за период |
| `status` | состояние, расписание, последний запуск |
| `scenarios list\|enable\|disable` | управление сценариями |
| `config show\|get\|set\|validate` | конфигурация |
| `logs` | последние строки журнала |
| `self-test` | проверка окружения |
| `daemon` | демон: расписание, отчёты, webhook, Telegram |
| `web` | веб-дашборд и REST API |
| `telemetry` | добровольная анонимная статистика |
| `version` | версия |

### 7.1. `check`

```
tspu-monitor check [профили…] [--json] [--samples N] [--webhook] [--quiet]
```

* `профили` — имена сценариев (`wireguard amnezia`); пусто = все включённые.
* `--samples N` — повторов на пробу (влияет на оценку обрывов).
* `--json` — `RunRecord.to_dict()` в stdout.
* `--webhook` — отправить уведомление (если уровень ≥ порога).
* `--quiet` — не печатать отчёт, но вернуть код.

**Exit-коды:**

| Код | Значение |
|---|---|
| 0 | норма (NONE/LOW) |
| 1 | ошибка (конфиг, неизвестный сценарий, исключение) |
| 2 | деградация (MEDIUM) |
| 3 | блокировка (HIGH/FULL) |

### 7.2. `report`

```
tspu-monitor report [--hours N] [--json] [--output FILE] [--no-save] [--send]
```

* `--hours` — окно отчёта (по умолчанию `scheduler.report_interval_hours`).
* `--json` — машинный отчёт в stdout (без файла).
* `--output` — имя файла (по умолчанию `report-YYYYmmdd-HHMMSS.txt`).
* `--no-save` — только stdout.
* `--send` — отправить TXT в Telegram (нужен настроенный `secrets.telegram`).

### 7.3. `status`

```
tspu-monitor status [--json]
```

Показывает каталоги, включённые сценарии, ближайшие проверку/отчёт,
последний запуск.

### 7.4. `config`

```
tspu-monitor config show [--json]
tspu-monitor config get KEY
tspu-monitor config set KEY VALUE
tspu-monitor config validate
```

`set` работает только с whitelist-ключами:

| Ключ | Тип |
|---|---|
| `general.log_level` | str |
| `general.color` | str |
| `scheduler.check_interval_minutes` | int |
| `scheduler.report_interval_hours` | int |
| `scheduler.report_day` | str |
| `scheduler.report_time` | str |
| `classification.throttle_min_kbps` | int |
| `web.enabled` | bool |
| `web.host` | str |
| `web.port` | int |
| `web.refresh_seconds` | int |
| `telemetry.enabled` | bool |
| `telemetry.url` | str |
| `webhook.enabled` | bool |
| `webhook.url` | str |
| `webhook.min_level` | str |
| `reports.include_logs` | bool |
| `reports.max_log_lines` | int |

### 7.5. `logs`

```
tspu-monitor logs [-n N] [--file main.log|probes.log|telegram.log]
```

### 7.6. `self-test`

Проверяет Python, бинарники (`ping`, `traceroute`, `nmap`, `dig`,
`openssl`), scapy/root, наличие и записываемость каталогов, конфигурацию,
webhook/Telegram. JSON: `self-test --json`. Критические проблемы дают
код 1.

### 7.7. `daemon`

```
tspu-monitor daemon [--interval N] [--no-telegram] [--no-webhook] [--web]
```

Запускает планировщик (проверки каждые `check_interval_minutes`, отчёт
по `report_day`/`report_time`), webhook, веб-дашборд (`--web` или
`web.enabled: true`) и, при настройке, Telegram-бота. Корректно
обрабатывает SIGINT/SIGTERM.

### 7.8. `web`

```
tspu-monitor web [--host HOST] [--port PORT] [--open] [--allow-remote-no-auth]
```

Запускает веб-дашборд с REST API (по умолчанию `127.0.0.1:8787`).
Не-loopback адрес без `secrets.web.token` запрещён; обойти проверку можно
флагом `--allow-remote-no-auth` (не рекомендуется). `--open` открывает
браузер.

---

## 8. Конфигурация

### 8.1. `settings.yaml`

| Ключ | По умолчанию | Описание |
|---|---|---|
| `general.language` | `ru` | язык (резерв) |
| `general.log_level` | `INFO` | DEBUG/INFO/WARNING/ERROR |
| `general.color` | `auto` | auto/always/never |
| `general.data_dir` | `/var/lib/tspu-monitor/data` | JSON-снимки запусков |
| `general.reports_dir` | `/var/lib/tspu-monitor/reports` | TXT-отчёты |
| `general.log_dir` | `/var/log/tspu-monitor` | журналы |
| `scheduler.check_interval_minutes` | `60` | период проверок |
| `scheduler.startup_delay_seconds` | `15` | первая проверка после старта |
| `scheduler.report_interval_hours` | `168` | окно отчёта |
| `scheduler.report_day` | `monday` | день недельного отчёта |
| `scheduler.report_time` | `09:00` | время отчёта (UTC) |
| `scenarios.enabled` | список встроенных | активные сценарии |
| `scenarios.options.<name>` | `{}` | пер-сценарные параметры |
| `testing.control_hosts` | `[ya.ru, google.com]` | контрольные хосты |
| `testing.blocked_test_hosts` | `[twitter.com, facebook.com]` | реестровые хосты |
| `testing.dns_resolvers` | `[1.1.1.1, 8.8.8.8, 77.88.8.8]` | резолверы сравнения |
| `testing.ports_tcp` | `[80,443,8080,8443,22]` | TCP-порты |
| `testing.ports_udp` | `[53,443,51820,1194]` | UDP-порты |
| `testing.timeout_seconds` | `10` | таймаут пробы |
| `testing.ping_count` | `5` | пакетов на ping |
| `testing.max_ttl` | `30` | максимум TTL |
| `testing.samples` | `2` | повторов (обрывы) |
| `classification.level_thresholds` | `{medium:25, high:50, full:70}` | границы уровней |
| `classification.throttle_min_kbps` | `256` | порог шейпинга |
| `web.enabled` | `false` | запускать дашборд вместе с демоном |
| `web.host` | `127.0.0.1` | адрес дашборда (не-loopback требует токен) |
| `web.port` | `8787` | порт дашборда |
| `web.refresh_seconds` | `30` | автообновление дашборда |
| `telemetry.enabled` | `false` | добровольная анонимная статистика |
| `telemetry.url` | `https://statistics.fairen8.ru/api/v1/events` | приёмник статистики |
| `telemetry.timeout_seconds` | `3` | таймаут отправки |
| `webhook.enabled` | `false` | включить webhook |
| `webhook.url` | `""` | адрес |
| `webhook.min_level` | `medium` | минимальный уровень отправки |
| `webhook.timeout_seconds` | `10` | таймаут |
| `webhook.headers` | `{}` | доп. заголовки |
| `reports.include_logs` | `true` | секция аномалий журналов |
| `reports.max_log_lines` | `500` | лимит строк журналов |

### 8.2. `secrets.yaml`

| Ключ | Описание |
|---|---|
| `telegram.enabled` | включить бота |
| `telegram.bot_token` | токен @BotFather |
| `telegram.allowed_users` | список Telegram ID |
| `telegram.report_chat_id` | чат для отчётов (иначе первый из allowed_users) |
| `telegram.proxy` | `http://…` или `socks5://…` (только для Telegram) |
| `telegram.api_base` | адрес Bot API (зеркало) |
| `webhook.token` | Bearer-токен для webhook |
| `web.token` | токен веб-дашборда (обязателен вне loopback) |
| `targets.wireguard_server/port` | WireGuard |
| `targets.amnezia_server/port` | AmneziaWG |
| `targets.openvpn_server/port` | OpenVPN |
| `targets.shadowsocks_server/port` | Shadowsocks |
| `targets.xray_server/port/reality_sni` | XRay/Reality |

Пустой target = сценарий пропускается. Права на файл: `chmod 600`.

---

## 9. Отчёты

### 9.1. TXT-структура

```
==============================================================
TSPU MONITOR — ОТЧЁТ О БЛОКИРОВКАХ
Период: … — …
==============================================================

1. ИТОГОВАЯ ОЦЕНКА
   Максимальный уровень блокировок: …
   Типы блокировок: …
   Причины: …
   Сводка по сценариям: …

2. ДЕТАЛИ ПО СЦЕНАРИЯМ
   --- WEB / HTTPS (web) ---
   Уровень: … | обрывы: …
   Пробы: …

3. ПРОГНОЗ
   Тренд по среднему score первой/второй половины периода.

4. АНОМАЛИИ В ЖУРНАЛАХ
   WARNING/ERROR/CRITICAL из main.log / probes.log / telegram.log.
```

### 9.2. JSON-отчёт (`report --json`)

```json
{
  "generated_at": "2026-10-06T09:00:00Z",
  "source": "tspu-monitor",
  "period": {"start": "...", "end": "..."},
  "runs": 12,
  "summary": {
    "level": "medium",
    "level_title": "средний",
    "score": 45,
    "types": ["dns_spoof", "rst_injection"],
    "type_titles": ["Подмена DNS", "Инъекция RST"],
    "disconnect": "periodic",
    "disconnect_title": "периодические обрывы",
    "causes": ["..."],
    "recommendations": ["..."]
  },
  "forecast": {"trend": "stable", "text": "...", "details": ["..."]},
  "profiles": [ {"profile": "web", "level": "high", "score": 72, "...": "..."} ]
}
```

### 9.3. Webhook payload

```json
{
  "event": "tspu.blocking",
  "source": "tspu-monitor",
  "version": "2.0.0",
  "run_id": "9f2c1a...",
  "finished": "2026-10-06T09:00:00Z",
  "level": "high",
  "level_title": "высокий",
  "score": 72,
  "profiles": [
    {"profile": "web", "target": "twitter.com", "level": "high", "score": 72,
     "types": ["rst_injection"], "disconnect": "periodic", "causes": ["..."]}
  ],
  "recommendations": ["..."]
}
```

Отправка: POST с `Content-Type: application/json`, заголовок
`User-Agent: tspu-monitor/2.0.0`, при заданном токене —
`Authorization: Bearer <token>`. 3 попытки с backoff 1–2 с.

---

## 10. Автоматизация

### 10.1. Расписание

`daemon` поднимает встроенный планировщик: проверки каждые
`scheduler.check_interval_minutes` минут, недельный отчёт по
`scheduler.report_day`/`report_time` (UTC). Первая проверка — через
`scheduler.startup_delay_seconds` после старта.

### 10.2. Cron (разово, без демона)

```cron
*/30 * * * * /usr/local/bin/tspu-monitor check --quiet --webhook
0 9 * * 1    /usr/local/bin/tspu-monitor report --send
```

Exit-коды позволят уведомлять другие системы:

```bash
tspu-monitor check --quiet
case $? in
  0) echo "OK" ;;
  2) echo "WARN: деградация" ;;
  3) echo "BLOCKED" ;;
esac
```

### 10.3. JSON для интеграций

```bash
tspu-monitor check --json | jq '.max_level, .max_score'
tspu-monitor check --json | jq '[.analyses[] | {profile, level, types}]'
```

### 10.4. Webhook

Включение:

```yaml
webhook:
  enabled: true
  url: "https://n8n.example.com/webhook/tspu"
  min_level: medium
```

Проверка вручную: `tspu-monitor check --webhook`.
В демоне webhook отправляется автоматически после каждой проверки.

### 10.5. Telegram

Бот включается в `secrets.yaml` (`enabled`, `bot_token`, `allowed_users`).
Команды: `/status`, `/check [профиль]`, `/report`, `/scenarios`,
`/logs [N]`, `/config show|get|set`. Подробности маршрутизации —
раздел 11.

---

## 11. Telegram за блокировкой

Проксируется **только** Bot API. Пробы всегда идут напрямую.

Варианты:

1. **zapret на хосте/роутере** — прозрачная десинхронизация TLS к
   `api.telegram.org`, в конфиге `proxy: null`.
2. **Прокси** — `telegram.proxy: "socks5://127.0.0.1:1080"` (нужен
   `aiohttp-socks`, extra `[socks]`) или `http://…` (из коробки).

Полная инструкция с примерами: [`deploy/zapret/README.md`](deploy/zapret/README.md).

Диагностика:

```bash
tspu-monitor self-test
tspu-monitor logs --file telegram.log -n 50
```

---

## 12. Развёртывание

### 12.1. Требования

| Параметр | Значение |
|---|---|
| ОС | Debian 12/13, Ubuntu 22.04+ (контейнер/VM/железо) |
| Python | 3.11+ |
| RAM | ≥ 256 МБ |
| Сеть | исходящий доступ; Telegram — через прокси/zapret |
| Права | root/CAP_NET_RAW только для `raw.ttl` (scapy) |

### 12.2. Docker

```bash
docker compose up -d --build
docker compose exec tspu-monitor tspu-monitor self-test
```

Особенности:

* тома: `./config`, `tspu-data`, `tspu-logs`;
* `cap_add: NET_RAW, NET_ADMIN` — для raw-проб;
* `HEALTHCHECK` через `tspu-monitor status --json`;
* при первом запуске конфиги создаются из шаблонов (`entrypoint.sh`);
* если внутренние серверы доступны только с хоста — включите `network_mode: host`.

### 12.3. LXC (Proxmox)

```bash
bash deploy/lxc/proxmox-create.sh 210 tspu-monitor
```

Скрипт создаёт привилегированный контейнер с `lxc.cap.keep: net_raw
net_admin net_bind_service`. Подробности и частые проблемы —
[`deploy/lxc/README.md`](deploy/lxc/README.md).

### 12.4. Универсальный установщик (Linux и macOS)

Одна команда — сам определяет дистрибутив и пакетный менеджер:

```bash
curl -fsSL https://raw.githubusercontent.com/Fairen8/tspu-monitor/main/install.sh | sudo bash
```

Поддерживаются Debian/Ubuntu, RHEL/CentOS/Rocky/Alma/Fedora, Alpine,
Arch/Manjaro, openSUSE и macOS (Homebrew). Установщик:

1. ставит системные зависимости (ping, traceroute, nmap, dig, openssl);
2. находит Python ≥ 3.11, создаёт venv и ставит пакет;
3. создаёт CLI `/usr/local/bin/tspu-monitor` и сервис
   (systemd, OpenRC или без сервиса);
4. создаёт конфиги, не перезаписывая существующие.

| Флаг | Назначение |
|---|---|
| `--version REF` | версия (тег `vX.Y.Z`) или `main` |
| `--prefix DIR` | каталог установки (`/opt/tspu-monitor`) |
| `--no-deps` | не ставить системные пакеты |
| `--no-service` | не создавать сервис |
| `--with-web` | включить веб-дашборд |
| `--web-host HOST`, `--web-port PORT` | адрес и порт дашборда |
| `--uninstall` | удалить (данные сохраняются; `TSPU_PURGE=1` — удалить всё) |

Примеры:

```bash
curl -fsSL .../install.sh | sudo bash -s -- --version v2.1.0 --with-web
curl -fsSL .../install.sh | sudo bash -s -- --no-service
```

Windows (PowerShell; сетевые пробы ограничены, CLI/дашборд работают):

```powershell
irm https://raw.githubusercontent.com/Fairen8/tspu-monitor/main/install.ps1 | iex
```

Установщик идемпотентен: обновляет код и зависимости, не перезаписывая
конфиги и данные.

### 12.5. systemd

Unit: [`deploy/systemd/tspu-monitor.service`](deploy/systemd/tspu-monitor.service).
Команды:

```bash
systemctl status tspu-monitor
journalctl -u tspu-monitor -f
```

---

## 13. Разработка

### 13.1. Структура

```
src/tspu_monitor/
├── cli.py            # команды CLI
├── engine.py         # запуск сценариев, run-файлы
├── classification.py # правила классификации
├── models.py         # модели данных
├── config.py         # конфигурация
├── reporter.py       # отчёты
├── notifier.py       # webhook
├── scheduler.py      # расписание
├── telegram_bot.py   # Telegram
├── logging_setup.py  # журналы
├── utils.py          # утилиты
├── probes/           # пробы
└── scenarios/        # сценарии + custom/
```

### 13.2. Своя проба

```python
from tspu_monitor.models import ProbeResult, Severity
from tspu_monitor.probes.base import BaseProbe


class MyProbe(BaseProbe):
    name = "my.probe"
    title = "Моя проба"

    async def run(self) -> list[ProbeResult]:
        ip = await self.resolve(self.config.get("host", ""))
        return [self.make_result(
            success=ip is not None,
            target=str(self.config.get("host", "")),
            data={"host": self.config.get("host"), "ip": ip},
            severity=Severity.INFO if ip else Severity.WARNING,
            error=None if ip else "DNS не разрешился",
        )]
```

Если нужно классифицировать аномалию — добавьте правило в
`DiagnosisEngine._rules()`.

### 13.3. Свой сценарий

Скопируйте шаблон:

```bash
cp src/tspu_monitor/scenarios/custom/_example.py \
   src/tspu_monitor/scenarios/custom/my_service.py
```

Реализуйте `get_probes`, `get_default_config`, `target`; при
необходимости — `extra_findings` и `scenario_recommendations`.
Файл без ведущего подчёркивания подхватится автоматически; включите его:

```bash
tspu-monitor scenarios enable my_service
```

В Docker смонтируйте каталог со сценариями (образ собран с
editable-установкой, поэтому новые файлы видны без пересборки):

```yaml
volumes:
  - ./custom-scenarios:/opt/tspu-monitor/src/tspu_monitor/scenarios/custom
```

### 13.4. Тесты и стиль

```bash
pip install -e ".[dev]"
ruff check src tests
pytest -q
```

Требования: Python 3.11+, аннотации типов, docstrings по-русски,
асинхронные долгие операции, тесты без сети (сеть — маркер `network`).

---

## 14. Устранение неполадок

| Симптом | Причина / решение |
|---|---|
| `Нет активных сценариев` | `scenarios.enabled` пуст или имена неверны: `tspu-monitor scenarios list` |
| Сценарий пропущен | не задан `targets.*` в `secrets.yaml` |
| `ping: socket: Operation not permitted` | LXC: `net.ipv4.ping_group_range`; см. `deploy/lxc/README.md` |
| raw-пробы `skipped` | нет root/CAP_NET_RAW или scapy: `self-test` |
| Telegram не запускается | нет токена/allowed_users; блокировка API — zapret/прокси |
| Отчёт пуст | мало запусков: выполните `check` несколько раз |
| Webhook не приходит | `webhook.enabled`, `webhook.url`, журнал `probes.log`/`main.log` |
| `scapy import failed` | `pip install "tspu-monitor[raw]"` |
| Неизвестный сценарий | проверьте имя: `tspu-monitor scenarios list` |
| Нет доступа к VPN-серверу | контейнер вне нужной сети/VLAN; `network_mode: host` для Docker |

Журналы: `main.log` (общий), `probes.log` (пробы), `telegram.log`
(бот). Просмотр: `tspu-monitor logs --file probes.log -n 200`.

---

## 15. FAQ

**Можно ли запускать без root?** Да. Все пробы, кроме `raw.ttl`
(scapy), работают без привилегий; raw-проба автоматически пропускается.

**Проксируются ли пробы?** Нет. `telegram.proxy` применяется только к
Bot API.

**Как сравнить ситуацию до/после смены SNI?** Сделайте `check` до и
после — история лежит в `data/run-*.json`; отчёт покажет тренд.

**Как добавить контрольный хост?** `testing.control_hosts` в
`settings.yaml`.

**Как изменить границы уровней?** `classification.level_thresholds`.

**Где хранится история?** `data/run-*.json`; отчёты —
`reports/report-*.txt`.

**Поддерживается ли Windows?** Целевая платформа — Linux (Docker/LXC);
часть проб требует Linux-утилит и capabilities.

---

## 16. Веб-дашборд и API

### 16.1. Запуск

```bash
tspu-monitor web --open          # локально, с открытием браузера
tspu-monitor daemon --web        # демон + расписание + дашборд
```

Настройки: `web.enabled`, `web.host` (по умолчанию `127.0.0.1`),
`web.port` (`8787`), `web.refresh_seconds` (`30`), а также токен
`secrets.web.token`. Не-loopback адрес без токена отклоняется при запуске.

### 16.2. Возможности дашборда

* сводка: уровень блокировок, обрывы, активные сценарии, время запуска;
* история уровня по последним запускам (график);
* сценарии с типами, доказательствами и детальными пробами;
* агрегированные причины и рекомендации;
* кнопка «Проверить сейчас» и автообновление;
* токен можно ввести прямо в интерфейсе (хранится в localStorage).

### 16.3. REST API

| Метод | Путь | Описание |
|---|---|---|
| GET | `/` | HTML-дашборд |
| GET | `/api/health` | проверка живости (без авторизации) |
| GET | `/api/summary?limit=N` | сводка, история и последний запуск |
| GET | `/api/runs?limit=N` | список запусков |
| GET | `/api/runs/{run_id}` | детали запуска |
| POST | `/api/check` | запустить проверки (`profiles`, `samples`) |
| GET | `/metrics` | метрики Prometheus |

Авторизация: `Authorization: Bearer <web.token>` или `?token=<...>`.
Без токена API открыто только на loopback.

```bash
curl -s localhost:8787/api/summary | jq '.last_run.max_score'
curl -s -X POST localhost:8787/api/check -H 'Content-Type: application/json' -d '{}'
curl -s localhost:8787/metrics
```

### 16.4. Prometheus

`/metrics` отдаёт `tspu_monitor_score{profile,target,level}`,
`tspu_monitor_last_run_timestamp_seconds`, `tspu_monitor_runs_total` и
`tspu_monitor_checks_total{severity}`. Для Docker пробросьте порт
(`127.0.0.1:8787:8787`) и включите `web.enabled: true`.

---

## 17. Анонимная статистика

Добровольная отправка обезличенных метрик. **По умолчанию выключена.**

```bash
tspu-monitor telemetry enable     # включить
tspu-monitor telemetry status     # состояние и client_id
tspu-monitor telemetry disable    # выключить
tspu-monitor telemetry test       # ручная проверка приёмника
```

Установщики: `install.sh --with-telemetry`, `install.ps1 -WithTelemetry`.
Приёмник по умолчанию — `https://statistics.fairen8.ru/api/v1/events`
(меняется через `telemetry.url`).

### 17.1. Что отправляется

Только обезличенные технические метрики:

* анонимный `client_id` — случайный UUID, хранится локально
  (`data/telemetry.json`), не связан с пользователем;
* версия, ОС (идентификатор дистрибутива), архитектура, версия Python;
* по каждому сценарию: имя, уровень, баллы, типы блокировок, обрывы;
* счётчики (проверки, critical/warning) и включённые функции
  (дашборд/webhook/Telegram — булевы флаги).

События: `install` (однократно) и `run` (после каждой проверки).

### 17.2. Что НЕ отправляется

* IP-адреса, домены, имена хостов и цели проб;
* результаты проб, тексты ошибок, доказательства, причины, рекомендации;
* токены, ID Telegram, содержимое конфигов;
* персональные данные любого рода.

> Любой HTTPS-сервер видит IP соединения (это неизбежно); в самой нагрузке
> IP не передаётся. Нагрузка описана выше и проверяется тестами
> (`tests/test_telemetry.py`).

### 17.3. Поведение при сбоях

Если сайт недоступен, нет сети или истёк таймаут — событие молча
отбрасывается: пользователю ничего не выводится, в журнал попадает только
DEBUG-строка (`tspu.telemetry`). Отправка не задерживает проверки и не
влияет на их результат. Ручная команда `telemetry test` сообщает результат.

---

*Документация соответствует TSPU Monitor 2.1.0. При изменении кода
обновляйте её вместе с функциональностью. Лицензия — [MIT](LICENSE).*
