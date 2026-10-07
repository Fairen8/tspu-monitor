# Спецификация сервиса статистики statistics.fairen8.ru

Документ для агента, который пишет сайт и бэкенд статистики TSPU Monitor.
Описывает **точный контракт** приёма данных, поведение клиента, модель
хранения, публичный API, дашборд и требования к приватности.

Источник истины по клиенту: [`src/tspu_monitor/telemetry.py`](../src/tspu_monitor/telemetry.py)
(версия клиента на момент написания — 2.1.1).

---

## 1. Что и зачем

TSPU Monitor (консольное приложение) отправляет **анонимную телеметрию**
на `https://statistics.fairen8.ru`. Сервис должен:

1. принимать события по HTTP(S);
2. хранить их в БД;
3. публиковать агрегаты и визуализировать их на публичном дашборде.

Телеметрия **включена по умолчанию** в приложении и отключается командой
`tspu-monitor telemetry disable`. Персональных данных в полезной нагрузке
нет (см. §9).

---

## 2. Контракт приёма (ingest)

| Параметр | Значение |
|---|---|
| Метод | `POST` |
| URL | `https://statistics.fairen8.ru/api/v1/events` |
| Content-Type | `application/json; charset=utf-8` (клиент всегда aiohttp `json=`) |
| Аутентификация | нет (публичный приём, защита — rate limit) |
| Таймаут клиента | 3 секунды **на весь запрос**; сервер обязан отвечать быстро |
| User-Agent | `tspu-monitor/<версия>`, например `tspu-monitor/2.1.1` |
| Тело | один JSON-объект события (`install` или `run`) |
| Размер | реально ≈ 0.4 КБ (install) и ≈ 1–3 КБ (run); лимит приёма 16 КБ |
| Ретраи клиента | **нет** (кроме повторной отправки `install` — см. §3) |
| Редиректы | сервер НЕ должен редиректить: отвечать 2xx напрямую |

### Ответы сервера

| Код | Что делает клиент |
|---|---|
| `200`, `201`, `202`, `204` (любой 2xx) | считает событие доставленным; тело ответа игнорируется (можно пустое) |
| `400`, `413`, `422` | событие теряется; `install` будет повторён при следующем запуске; в лог клиента — только DEBUG |
| `429`, `503` и прочие non-2xx | то же: молчаливый отказ, без ожидания `Retry-After` |
| `3xx` | не использовать (поведение зависит от метода) |

**Рекомендуется отвечать `202 Accepted` с пустым телом** — быстрее всего.

### Обязательные требования к ingest

1. **Скорость**: p95 < 200 мс; жёсткий предел — 3 с (таймаут клиента).
2. **Валидация**: строгая по схемам §4–§5; неизвестные поля **игнорировать**
   (прямая совместимость: клиент может добавлять поля), неизвестный
   `event` — `400`.
3. **Идемпотентность `install`**: upsert по `client_id` (обновить
   `last_seen`/`last_version`/`installs_count`), не создавать дубли.
   Повторные `install` — норма (клиент повторяет до первого 2xx).
4. **`run` — append-only события** (без дедупликации). При желании —
   защита от точных дублей по `(client_id, sent_at, duration_seconds)`.
5. **Виды спорта**: rate limit на IP, например 120 событий/мин и
   10 install/мин; при превышении — `429`.
6. **Никаких тел запросов в логах** (или маскирование).
7. HTTPS обязателен (Let's Encrypt), HSTS; HTTP/1.1 и HTTP/2.

---

## 3. Поведение клиента (точная семантика)

Клиент — `tspu_monitor.telemetry.Telemetry`, вызывается движком
**после каждого запуска проверок** (`engine.run()`), в том числе:
CLI `check`, веб-API `POST /api/check`, демон по расписанию.

1. Если телеметрия выключена (`telemetry.enabled: false`) или URL пуст —
   **ничего не отправляется**.
2. Локальное состояние: `<data_dir>/telemetry.json`
   (`{"client_id": "<32 hex>", "install_sent": <bool>}`).
   `client_id` — `uuid4().hex`, создаётся один раз и не меняется.
   Переустановка без удаления `data/` сохраняет `client_id`.
3. При первом запуске (пока `install_sent != true`):
   отправляется событие **`install`**; при 2xx клиент ставит
   `install_sent=true`. При сбое — повторит `install` при следующем
   запуске (таким образом, `install` может прийти несколько раз, иногда
   спустя дни — upsert обязателен).
4. Затем **всегда** (если в запуске есть хотя бы один сценарий)
   отправляется событие **`run`**. Если `run` не доставлен — он
   **теряется навсегда** (следующий запуск создаст новое событие).
5. События отправляются **по одному**; при первом успешном запуске это
   два POST подряд (`install`, затем `run`).
6. Ошибки не влияют на работу приложения и не показываются пользователю:
   в журнале клиента только DEBUG (`tspu.telemetry`).
7. Ручная команда `tspu-monitor telemetry test` отправляет `install`
   (даже при выключенной телеметрии) и сообщает результат пользователю —
   может использоваться для проверки бэкенда.

### Частота и объёмы

* Демон по умолчанию запускает проверки **раз в час**
  (`scheduler.check_interval_minutes: 60`) + ручные запуски.
* Одна установка: ~24 `run`/сутки + 1 `install` (плюс повторные до
  первого успеха).
* 1 000 активных установок ≈ 24 000 событий/сутки (~0.3 rps в среднем),
  но возможны **пики на границе часа** — приёмник должен выдерживать
  всплески (например, асинхронная запись/очередь).

---

## 4. Событие `install`

```json
{
  "event": "install",
  "client_id": "f9a24616123448b0a8d68cfd205aecce",
  "version": "2.1.1",
  "sent_at": "2026-10-06T14:48:17Z",
  "os": "debian",
  "arch": "x86_64",
  "python": "3.11.17"
}
```

| Поле | Тип | Обязательно | Описание |
|---|---|---|---|
| `event` | string | да | всегда `"install"` |
| `client_id` | string | да | 32 символа `[0-9a-f]`, анонимный UUID установки |
| `version` | string | да | версия приложения (`X.Y.Z`, возможен суффикс `-rc.1`) |
| `sent_at` | string | да | ISO-8601 UTC с `Z`, часы клиента (могут быть сбиты) |
| `os` | string | да | ID дистрибутива (`debian`, `ubuntu`, `fedora`, `alpine`, `arch`, `macos`, `windows`, `unknown`) |
| `arch` | string | да | `x86_64`, `aarch64`, `arm64`, `AMD64`, … |
| `python` | string | да | версия Python (`3.11.17`) |

## 5. Событие `run`

```json
{
  "event": "run",
  "client_id": "f9a24616123448b0a8d68cfd205aecce",
  "version": "2.1.1",
  "sent_at": "2026-10-06T15:00:03Z",
  "duration_seconds": 14.32,
  "max_level": "high",
  "max_score": 72,
  "scenarios": [
    {
      "profile": "web",
      "level": "high",
      "score": 72,
      "types": ["rst_injection", "sni_filter"],
      "disconnect": "periodic"
    },
    {
      "profile": "wireguard",
      "level": "none",
      "score": 0,
      "types": [],
      "disconnect": "none"
    }
  ],
  "counts": { "scenarios": 2, "checks": 41, "critical": 3, "warning": 7 },
  "features": { "web": true, "webhook": false, "telegram": true }
}
```

| Поле | Тип | Обязательно | Описание |
|---|---|---|---|
| `event` | string | да | всегда `"run"` |
| `client_id` | string | да | как в `install` |
| `version` | string | да | версия приложения |
| `sent_at` | string | да | ISO-8601 UTC с `Z` |
| `duration_seconds` | number | да | длительность запуска, округление до 0.01 |
| `max_level` | enum | да | `none` / `low` / `medium` / `high` / `full` |
| `max_score` | int | да | 0–100 |
| `scenarios` | array | да | ≥ 1 элемент, см. ниже |
| `counts.scenarios` | int | да | число сценариев (= длине массива) |
| `counts.checks` | int | да | всего проб |
| `counts.critical` | int | да | проб с severity=critical |
| `counts.warning` | int | да | проб с severity=warning |
| `features.web` | bool | да | включён ли веб-дашборд |
| `features.webhook` | bool | да | настроен ли webhook |
| `features.telegram` | bool | да | включён ли Telegram-бот |

### Элемент `scenarios[]`

| Поле | Тип | Описание |
|---|---|---|
| `profile` | string | id сценария: `web`, `dns`, `quic`, `wireguard`, `amnezia`, `openvpn`, `shadowsocks`, `xray` или имя кастомного (`^[a-z0-9_]{1,64}$`) |
| `level` | enum | `none` / `low` / `medium` / `high` / `full` |
| `score` | int | 0–100 |
| `types` | string[] | типы блокировок, 0..n (значения из §6) |
| `disconnect` | enum | `none` / `rare` / `periodic` / `frequent` / `constant` |

---

## 6. Словари значений

**Уровень блокировки** (`max_level`, `scenarios[].level`):
`none` (нет), `low` (низкий), `medium` (средний), `high` (высокий),
`full` (полный).

**Обрывы** (`disconnect`): `none`, `rare`, `periodic`, `frequent`, `constant`.

**Типы блокировок** (`types[]`, полный список):

| Код | Русское название |
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
| `mtu_filter` | Фильтрация по размеру пакета |
| `http_plug` | Страница-заглушка |
| `quic_block` | Блокировка QUIC |
| `icmp_block` | Блокировка ICMP |
| `replay_cache` | Кэш повторов (replay-cache) |
| `cover_block` | Блокировка cover-ресурса |
| `misconfig` | Ошибка конфигурации (не блокировка) |
| `unknown` | Неизвестная аномалия |

**ОС** (`os`): значения из `/etc/os-release` (`ID`), `macos`, `windows`,
`unknown`. Список открытый — не должна падать вставка новых значений.

**Что НЕ приходит никогда** (гарантия клиента): IP-адреса, домены, имена
хостов, цели проб, тексты ошибок, доказательства/причины/рекомендации,
токены, Telegram ID, содержимое конфигов, любые ПДн.

---

## 7. Модель данных (рекомендация: PostgreSQL)

```sql
CREATE TABLE clients (
    client_id        TEXT PRIMARY KEY,              -- 32 hex
    first_seen       TIMESTAMPTZ NOT NULL,
    last_seen        TIMESTAMPTZ NOT NULL,
    os               TEXT,
    arch             TEXT,
    python           TEXT,
    first_version    TEXT,
    last_version     TEXT,
    installs_count   INT NOT NULL DEFAULT 0,
    runs_count       BIGINT NOT NULL DEFAULT 0,
    country          TEXT                            -- ISO-3166, из GeoIP (опция)
);

CREATE TABLE installs (
    id           BIGSERIAL PRIMARY KEY,
    client_id    TEXT NOT NULL REFERENCES clients(client_id),
    version      TEXT NOT NULL,
    os           TEXT,
    arch         TEXT,
    python       TEXT,
    sent_at      TIMESTAMPTZ NOT NULL,               -- часы клиента
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now()  -- часы сервера
);

CREATE TABLE runs (
    id                BIGSERIAL PRIMARY KEY,
    client_id         TEXT NOT NULL REFERENCES clients(client_id),
    version           TEXT NOT NULL,
    sent_at           TIMESTAMPTZ NOT NULL,
    received_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    duration_seconds  REAL NOT NULL,
    max_level         TEXT NOT NULL,
    max_score         INT NOT NULL,
    stats             JSONB NOT NULL,   -- counts + features как пришло
    country           TEXT
);
CREATE INDEX runs_client_time_idx ON runs (client_id, sent_at DESC);
CREATE INDEX runs_time_idx        ON runs (received_at DESC);

CREATE TABLE run_scenarios (
    run_id      BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    profile     TEXT NOT NULL,
    level       TEXT NOT NULL,
    score       INT NOT NULL,
    disconnect  TEXT NOT NULL,
    types       TEXT[] NOT NULL DEFAULT '{}'
);
CREATE INDEX run_scenarios_profile_idx ON run_scenarios (profile);
```

Правила записи:

* `sent_at` **не доверять** как времени события; все агрегаты по времени
  строить по `received_at` (часы клиентов бывают сбиты).
* `install` — upsert в `clients` + запись в `installs` (только при
  изменении `version`/`os`/`arch`/`python` или счётчик; хранение всех
  повторов не обязательно).
* `run` — запись в `runs` + `run_scenarios` в одной транзакции.
* Хранить `max_score` и уровень: доля `high|full` = «заблокированные
  запуски», `medium` = «нестабильные».

### Роллапы для дашборда (обязательны для скорости)

Агрегаты за день по `received_at` (materialized view или таблица,
обновление раз в 5–15 минут):

* `daily_stats(date, installs, runs, clients, blocked_runs, unstable_runs, avg_score)`
* `daily_os(date, os, clients)`
* `daily_versions(date, version, clients)`
* `daily_levels(date, level, runs)`
* `daily_types(date, type, runs)`
* `daily_profiles(date, profile, runs, avg_score)`

---

## 8. Публичный API сайта (read-only)

Все ответы — JSON, UTF-8, `Access-Control-Allow-Origin: *`,
`Cache-Control: public, max-age=60`.

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/v1/health` | `{"status":"ok"}` |
| GET | `/api/v1/stats/summary` | ключевые числа (см. ниже) |
| GET | `/api/v1/stats/timeseries?metric=installs\|runs\|clients\|blocked&interval=day&from=…&to=…` | временные ряды |
| GET | `/api/v1/stats/distribution?dimension=os\|arch\|python\|version\|level\|type\|disconnect\|profile\|feature` | распределения |
| GET | `/api/v1/stats/geo` | страны (если включён GeoIP) |

`summary` (пример):

```json
{
  "generated_at": "2026-10-06T15:05:00Z",
  "totals": { "installs": 128, "clients": 96, "runs": 41230 },
  "active": { "day": 41, "week": 77, "month": 92 },
  "quality": { "blocked_share": 0.18, "unstable_share": 0.11, "avg_score": 21.4 },
  "versions": { "2.1.1": 61, "2.1.0": 24, "2.0.0": 11 },
  "generated_by": "TSPU Monitor telemetry"
}
```

Определения: `active.day` — клиенты с ≥1 `run` за 24 ч; `blocked_share` —
доля `run` с `max_level in (high, full)` за 30 дней; `clients` — всего
уникальных `client_id`.

---

## 9. Дашборд (обязательные блоки)

Стиль: тёмная тема в духе дашборда приложения (Tabler-подобная:
`#0b1220` фон, карточки, синие акценты), адаптив, русский язык.

1. **Шапка**: «TSPU Monitor — анонимная статистика», дата обновления,
   ссылка на репозиторий, кнопка «Как отключить».
2. **Большие числа**: установки, активные клиенты (24ч/7д/30д), всего
   запусков, средний уровень блокировок, доля заблокированных запусков.
3. **Графики времени** (90 дней, переключатель периода): новые установки,
   запуски, активные клиенты, доля `high|full`.
4. **Распределения**: уровни блокировок, топ-10 типов блокировок, обрывы,
   ОС, версии приложения, версии Python, сценарии, фичи (web/webhook/telegram).
5. **Таблица сценариев**: профиль, запусков, средний score, топ-тип.
6. **Карта стран** (опционально, только если GeoIP включён).
7. **Публичная страница «Методика и приватность»**: что собирается
   (перечислить поля), что не собирается, как отключить
   (`tspu-monitor telemetry disable`), сроки хранения, контакт.
8. **Свежесть**: данные на дашборде отстают максимум на 15 минут;
   индикатор «обновлено N минут назад».

---

## 10. Приватность, безопасность, эксплуатация

* Полезная нагрузка не содержит ПДн (§6). **IP-адрес не хранить в БД**
  и не логировать; допустимо эфемерно использовать для rate limit и
  GeoIP → сохранять только страну.
* Логи приложения: без тел запросов; при отладке — ручная выборка.
* Ретенция: сырые `runs` — 180 дней (настраиваемо), агрегаты — бессрочно.
* Бэкапы: `pg_dump` раз в сутки, хранение 14 дней, проверка восстановления.
* Мониторинг: `/api/v1/health`, uptime-чек, алерт при 5xx/таймаутах или
  отсутствии событий > 6 часов (если есть активные клиенты).
* Ingest защищён rate limit; дашборд/API — публичные агрегаты.
* Деплой: Docker Compose (app + postgres + reverse proxy Caddy/nginx),
  отдельный VPS/контейнер, domain `statistics.fairen8.ru`, TLS Let's
  Encrypt, HTTP/2, HSTS, firewall (только 80/443).

---

## 11. Тесты и критерии приёмки

Обязательные проверки перед продакшеном:

1. **Схемы**: валидные `install`/`run` (примеры §4–§5) принимаются → 202;
   неизвестное поле игнорируется; неверный `event` → 400; битый JSON → 400;
   тело > 16 КБ → 413.
2. **Идемпотентность**: 10 одинаковых `install` → 1 клиент,
   `installs_count=1`; 10 `run` → 10 записей.
3. **Часовые пояса**: `sent_at` со сбитым временем не ломает агрегаты
   (агрегаты по `received_at`).
4. **Нагрузка**: 1000 событий/мин без ошибок; p95 ingest < 200 мс.
5. **Дашборд**: все блоки §9 отображаются на тестовых данных (сид-скрипт),
   фильтры периода работают, страница приватности совпадает с §6/§10.
6. **Отказоустойчивость**: недоступность БД → ingest отвечает 503 за < 3 с;
   клиент не пострадает (молчаливый отказ).
7. **Публичный API**: ответы кэшируются, CORS, лимит запросов.
8. **Смоук клиента**: `tspu-monitor telemetry test` получает 2xx на
   staging; после включения телеметрии на staging-клиенте появляется
   `install` и `run`.

### Что считать готовым (Definition of Done)

* ingest работает по контракту §2, данные лежат в БД §7, роллапы строятся;
* дашборд §9 и публичный API §8 работают на реальных событиях;
* приватность §10 соблюдена (проверить отсутствие IP в БД и логах);
* тесты §11 зелёные; деплой в Docker с TLS и бэкапами;
* `curl -X POST https://statistics.fairen8.ru/api/v1/events -H 'Content-Type: application/json' -d '<пример install>' -i` → `202`.

---

## 12. Примеры для проверки (curl)

```bash
curl -i -X POST https://statistics.fairen8.ru/api/v1/events \
  -H 'Content-Type: application/json' \
  -H 'User-Agent: tspu-monitor/2.1.1' \
  -d '{"event":"install","client_id":"f9a24616123448b0a8d68cfd205aecce",
       "version":"2.1.1","sent_at":"2026-10-06T14:48:17Z",
       "os":"debian","arch":"x86_64","python":"3.11.17"}'
# ожидаем 202

curl -s https://statistics.fairen8.ru/api/v1/stats/summary | jq .
```

Совместимость: сервис должен принимать события клиентов **всех версий
≥ 2.1.1** и деградировать безопасно, если поля появятся позже: новые поля
игнорируются, отсутствие необязательных не ломает приём.
