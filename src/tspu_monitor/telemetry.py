"""Анонимная статистика (включена по умолчанию, легко отключается).

Отправляется только при ``telemetry.enabled: true`` (по умолчанию включена;
отключается командой ``tspu-monitor telemetry disable``).
Полезная нагрузка не содержит адресов, имён хостов, секретов и иных
персональных данных — только обезличенные технические метрики
(включая страну и класс провайдера, которые определяет приёмник по IP
запроса, не сохраняя сам IP). Спецификация приёмника:
``docs/statistics-service-spec.md``.

Сбои отправки (сайт недоступен, нет сети, таймаут) полностью игнорируются:
пользователю ничего не выводится, в журнал попадает только DEBUG-строка.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import platform
import re
import socket
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import __version__
from .config import AppConfig
from .logging_setup import get_logger
from .models import ProbeResult, RunRecord, Severity, utc_now, utc_now_iso
from .utils import ensure_writable_dir, is_private_ip, read_json, write_json_atomic

DEFAULT_URL = "https://statistics.fairen8.ru/api/v1/events"
STATE_FILE = "telemetry.json"

#: Уровни, которые считаются «заблокировано» для переходов block_started/ended.
BLOCKED_LEVELS = {"high", "full"}

#: Стадии отказа пробы (в порядке приоритета при равенстве счётчиков).
FAIL_STAGES = ("connect", "tls", "handshake", "reset", "no_data")

#: IPv6-цели для проверки доступности (Cloudflare DNS, Google DNS).
_IPV6_PROBES = (("2606:4700:4700::1111", 443), ("2001:4860:4860::8888", 53))

_IPV6_TTL = timedelta(hours=24)
_TELEGRAM_TTL = timedelta(hours=1)
_ENV_TIMEOUT = 2.0

_ERROR_CLASS_RE = re.compile(
    r"^([A-Za-z_][\w.]*(?:Error|Exception|Timeout|gaierror))(?::|\s|$)"
)


def _os_id() -> str:
    """Идентификатор дистрибутива (без версии ядра и имени хоста)."""
    try:
        release = platform.freedesktop_os_release()  # Python 3.10+
    except (AttributeError, OSError):
        release = {}
    if release.get("ID"):
        return str(release["ID"])
    return platform.system().lower() or "unknown"


def _iso_age(value: str | None) -> timedelta | None:
    """Возраст ISO-метки времени; ``None``, если метки нет/не читается."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    try:
        return utc_now() - parsed
    except TypeError:
        return None


def percentile(values: list[float], fraction: float) -> float | None:
    """Перцентиль с линейной интерполяцией (``None`` для пустого списка)."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    pos = (len(ordered) - 1) * fraction
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    value = ordered[low] + (ordered[high] - ordered[low]) * (pos - low)
    return round(value, 1)


def _latency_samples(results: list[ProbeResult]) -> list[float]:
    """Собрать все числовые задержки проб сценария (мс)."""
    samples: list[float] = []
    for result in results:
        data = result.data or {}
        if data.get("skipped"):
            continue
        raw = data.get("rtt_samples_ms")
        if isinstance(raw, list) and raw:
            samples.extend(float(x) for x in raw if isinstance(x, (int, float)))
            continue
        for key in ("handshake_ms", "rtt_avg_ms"):
            value = data.get(key)
            if isinstance(value, (int, float)):
                samples.append(float(value))
                break
        else:
            if result.duration_ms:
                samples.append(float(result.duration_ms))
    return samples


def _timeout_count(results: list[ProbeResult]) -> int:
    """Число таймаутов по всем пробам сценария."""
    total = 0
    for result in results:
        data = result.data or {}
        if data.get("skipped"):
            continue
        if isinstance(data.get("timeout_count"), int):
            total += int(data["timeout_count"])
            continue
        error = (result.error or "").lower()
        if data.get("error_class") == "timeout" or "timeout" in error or "таймаут" in error:
            total += 1
    return total


def _fail_stage_of(result: ProbeResult) -> str | None:
    """Стадия отказа пробы: connect/tls/handshake/reset/no_data."""
    data = result.data or {}
    if result.success or data.get("skipped"):
        return None
    probe = result.probe or ""
    error = (result.error or "").lower()
    error_class = str(data.get("error_class") or "")
    if error_class:
        if error_class == "reset":
            return "reset"
        if error_class in ("alert", "cert"):
            return "tls"
        if error_class == "handshake":
            return "handshake"
        if error_class == "timeout":
            return "handshake" if probe.startswith("tls") else "connect"
        return "no_data"
    if (
        "reset" in error
        or "rst" in error
        or "отклон" in error
        or data.get("reset")
        or data.get("fast_rst_count")
    ):
        return "reset"
    if probe.startswith("tls"):
        return "handshake" if "handshake" in error else "tls"
    if probe.startswith("tcp.connect"):
        return "connect"
    if "timeout" in error or "таймаут" in error:
        return "connect" if probe.startswith("tcp") else "no_data"
    return "no_data"


def _dominant_fail_stage(results: list[ProbeResult]) -> str | None:
    """Самая частая стадия отказа среди неуспешных проб."""
    counter: Counter[str] = Counter()
    for result in results:
        stage = _fail_stage_of(result)
        if stage:
            counter[stage] += 1
    if not counter:
        return None
    ranked = sorted(
        counter.items(),
        key=lambda item: (-item[1], FAIL_STAGES.index(item[0]) if item[0] in FAIL_STAGES else 99),
    )
    return ranked[0][0]


def _anomaly_counts(results: list[ProbeResult]) -> dict[str, int]:
    """Счётчики наблюдаемых аномалий: RST, подозрительный TTL, IP-ID."""
    rst = 0
    ttl = 0
    ipid = 0
    seen_ipids: dict[str, list[int]] = {}
    for result in results:
        data = result.data or {}
        if data.get("skipped"):
            continue
        probe = result.probe or ""
        if probe.startswith("tcp.connect"):
            rst += int(data.get("fast_rst_count") or 0)
        if data.get("error_class") == "reset" or data.get("reset") is True:
            rst += 1
        kind = data.get("reply_kind")
        if kind == "rst":
            rst += 1
        if kind in ("rst", "other", "icmp") and data.get("reply_ttl") is not None:
            ttl += 1
        if kind is not None:
            value = data.get("reply_ipid")
            if isinstance(value, int):
                if value == 0:
                    ipid += 1
                else:
                    source = str(data.get("reply_src") or "")
                    history = seen_ipids.setdefault(source, [])
                    if value in history:
                        ipid += 1
                    history.append(value)
    return {"rst": rst, "ttl": ttl, "ipid": ipid}


def _first_critical_seconds(record: RunRecord) -> float | None:
    """Секунды от старта запуска до первой critical-пробы (``None`` — не было)."""
    try:
        started = datetime.fromisoformat(record.started.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    earliest: datetime | None = None
    for analysis in record.analyses:
        for result in analysis.results:
            if result.severity != Severity.CRITICAL:
                continue
            try:
                stamp = datetime.fromisoformat(result.timestamp.replace("Z", "+00:00"))
            except (AttributeError, ValueError):
                continue
            if earliest is None or stamp < earliest:
                earliest = stamp
    if earliest is None:
        return None
    return round(max((earliest - started).total_seconds(), 0.0), 1)


def _top_error_classes(record: RunRecord, limit: int = 3) -> list[str]:
    """Топ классов исключений проб — только имена, без текстов."""
    counter: Counter[str] = Counter()
    for analysis in record.analyses:
        for result in analysis.results:
            match = _ERROR_CLASS_RE.match(result.error or "")
            if match:
                counter[match.group(1)] += 1
    return [name for name, _ in counter.most_common(limit)]


def _ram_bytes() -> int | None:
    """Объём физической памяти в байтах (``None``, если определить не удалось)."""
    system = platform.system()
    try:
        if system == "Linux":
            for line in Path("/proc/meminfo").read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
        elif system == "Darwin":
            return int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
        elif system == "Windows":
            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys)
    except (OSError, ValueError, AttributeError):
        return None
    return None


def machine_buckets() -> dict[str, Any]:
    """Обезличенные характеристики машины: ядра и круглый объём RAM."""
    total = _ram_bytes()
    ram_gb = max(1, int(round(total / (1024**3)))) if total else None
    return {"cores": min(os.cpu_count() or 0, 256), "ram_gb": ram_gb}


def dns_mode() -> str:
    """Какие DNS-серверы используются: ``isp`` (приватные) или ``system``."""
    try:
        lines = Path("/etc/resolv.conf").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return "system"
    servers = [
        parts[1]
        for parts in (line.split() for line in lines)
        if len(parts) >= 2 and parts[0] == "nameserver"
    ]
    if servers and all(is_private_ip(server) for server in servers):
        return "isp"
    return "system"


def _custom_scenarios_count() -> int:
    """Число пользовательских сценариев (файлы в ``scenarios/custom``)."""
    try:
        import tspu_monitor.scenarios as scenarios_pkg
    except Exception:  # noqa: BLE001
        return 0
    custom_dir = Path(scenarios_pkg.__file__).parent / "custom"
    if not custom_dir.is_dir():
        return 0
    return sum(1 for path in custom_dir.glob("*.py") if not path.name.startswith("_"))


class Telemetry:
    """Клиент анонимной статистики (no-op, когда выключен)."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        cfg = config.settings.get("telemetry", {}) or {}
        self.enabled: bool = bool(cfg.get("enabled", False))
        self.url: str = str(cfg.get("url") or DEFAULT_URL)
        self.timeout: float = float(cfg.get("timeout_seconds", 3))
        self.logger = get_logger("tspu.telemetry")

        data_dir = ensure_writable_dir(
            config.data_dir, Path.home() / ".tspu-monitor" / "data"
        )
        self.state_path = data_dir / STATE_FILE
        state = read_json(self.state_path) or {}
        if not state.get("client_id"):
            state["client_id"] = uuid.uuid4().hex
        state.setdefault("last", {})
        state.setdefault("cache", {})
        self._state: dict[str, Any] = state
        self._save()

    # ------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.enabled and bool(self.url)

    @property
    def client_id(self) -> str:
        return str(self._state.get("client_id", ""))

    @property
    def install_sent(self) -> bool:
        return bool(self._state.get("install_sent"))

    def _save(self) -> None:
        try:
            write_json_atomic(self.state_path, self._state)
        except OSError:
            pass

    # ------------------------------------------------------------------
    async def _check_ipv6(self) -> bool:
        """Есть ли реальный выход в IPv6 (TCP-коннект к публичным v6-узлам)."""
        loop = asyncio.get_running_loop()
        for host, port in _IPV6_PROBES:
            sock: socket.socket | None = None
            try:
                sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                sock.setblocking(False)
                await asyncio.wait_for(
                    loop.sock_connect(sock, (host, port)), timeout=_ENV_TIMEOUT
                )
                return True
            except (TimeoutError, OSError, ValueError):
                continue
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
        return False

    async def _check_telegram_api(self) -> bool:
        """Доступен ли Telegram API напрямую, без прокси (bool)."""
        import aiohttp

        telegram = self.config.secrets.get("telegram", {}) or {}
        base = str(telegram.get("api_base") or "https://api.telegram.org")
        url = base.rstrip("/") + "/"
        try:
            timeout = aiohttp.ClientTimeout(total=_ENV_TIMEOUT)
            async with aiohttp.ClientSession(
                timeout=timeout, trust_env=False
            ) as session, session.get(url, allow_redirects=False) as response:
                return response.status < 600
        except Exception as exc:  # noqa: BLE001 — недоступность не мешает работе
            self.logger.debug("Telegram API недоступен: %s", exc)
            return False

    async def collect_env(self) -> dict[str, Any]:
        """Сетевые метрики окружения с локальным кэшем (IPv6 24ч, Telegram 1ч)."""
        cache = self._state.setdefault("cache", {})
        need_ipv6 = not isinstance(cache.get("ipv6"), bool) or (
            (age := _iso_age(cache.get("ipv6_at"))) is None or age > _IPV6_TTL
        )
        need_tg = not isinstance(cache.get("tg_api_ok"), bool) or (
            (age := _iso_age(cache.get("tg_at"))) is None or age > _TELEGRAM_TTL
        )
        if need_ipv6 and need_tg:
            ipv6, tg_api_ok = await asyncio.gather(
                self._check_ipv6(), self._check_telegram_api()
            )
        elif need_ipv6:
            ipv6 = await self._check_ipv6()
        elif need_tg:
            tg_api_ok = await self._check_telegram_api()
        if need_ipv6:
            cache["ipv6"] = ipv6
            cache["ipv6_at"] = utc_now_iso()
        if need_tg:
            cache["tg_api_ok"] = tg_api_ok
            cache["tg_at"] = utc_now_iso()
        if need_ipv6 or need_tg:
            self._save()
        return {
            "ipv6": cache.get("ipv6"),
            "dns_mode": dns_mode(),
            "tg_api_ok": cache.get("tg_api_ok"),
        }

    # ------------------------------------------------------------------
    def install_payload(self, env: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event": "install",
            "client_id": self.client_id,
            "version": __version__,
            "sent_at": utc_now_iso(),
            "os": _os_id(),
            "arch": platform.machine() or "unknown",
            "python": platform.python_version(),
        }
        if env:
            payload["ipv6"] = env.get("ipv6")
            payload["dns_mode"] = env.get("dns_mode")
        return payload

    def run_payload(
        self, record: RunRecord, env: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        env = env or {}
        last = self._state.get("last") or {}
        previous_score = last.get("max_score")
        previous_levels = last.get("levels") or {}

        counts = {"scenarios": 0, "checks": 0, "critical": 0, "warning": 0}
        scenarios: list[dict[str, Any]] = []
        levels: dict[str, str] = {}
        for analysis in record.analyses:
            critical = sum(
                1 for r in analysis.results if r.severity == Severity.CRITICAL
            )
            warning = sum(
                1 for r in analysis.results if r.severity == Severity.WARNING
            )
            counts["scenarios"] += 1
            counts["checks"] += len(analysis.results)
            counts["critical"] += critical
            counts["warning"] += warning

            level = analysis.level.name.lower()
            levels[analysis.profile] = level
            blocked = level in BLOCKED_LEVELS
            was_blocked = (
                str(previous_levels.get(analysis.profile, "none")).lower()
                in BLOCKED_LEVELS
            )
            samples = _latency_samples(analysis.results)
            scenarios.append(
                {
                    "profile": analysis.profile,
                    "level": level,
                    "score": analysis.score,
                    "types": [t.value for t in analysis.types],
                    "disconnect": analysis.disconnect.name.lower(),
                    "latency_p50": percentile(samples, 0.5),
                    "latency_p95": percentile(samples, 0.95),
                    "timeouts": _timeout_count(analysis.results),
                    "fail_stage": _dominant_fail_stage(analysis.results),
                    "anomalies": _anomaly_counts(analysis.results),
                    "block_started": blocked and not was_blocked,
                    "block_ended": was_blocked and not blocked,
                }
            )

        delta_score = (
            record.max_score - int(previous_score)
            if isinstance(previous_score, int)
            else None
        )

        telegram = self.config.secrets.get("telegram", {}) or {}
        features = {
            "web": bool(self.config.get("web.enabled")),
            "webhook": bool(
                self.config.get("webhook.enabled") and self.config.get("webhook.url")
            ),
            "telegram": bool(telegram.get("enabled") and telegram.get("bot_token")),
        }

        payload = {
            "event": "run",
            "client_id": self.client_id,
            "version": __version__,
            "sent_at": utc_now_iso(),
            "duration_seconds": round(record.duration_seconds, 2),
            "max_level": record.max_level.name.lower(),
            "max_score": record.max_score,
            "delta_score": delta_score,
            "first_critical_at": _first_critical_seconds(record),
            "profiles_on": list(self.config.get("scenarios.enabled", []) or []),
            "schedule_min": int(
                self.config.get("scheduler.check_interval_minutes", 60) or 60
            ),
            "custom_scenarios": _custom_scenarios_count(),
            "machine": machine_buckets(),
            "errors": _top_error_classes(record),
            "ipv6": env.get("ipv6"),
            "dns_mode": env.get("dns_mode"),
            "tg_api_ok": env.get("tg_api_ok"),
            "scenarios": scenarios,
            "counts": counts,
            "features": features,
        }

        self._state["last"] = {"max_score": record.max_score, "levels": levels}
        self._save()
        return payload

    # ------------------------------------------------------------------
    async def _post(self, payload: dict[str, Any]) -> bool:
        """Отправить событие. Любая ошибка — только DEBUG в журнал."""
        if not self.url:
            return False
        import aiohttp

        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(
                timeout=timeout
            ) as session, session.post(
                self.url,
                json=payload,
                headers={"User-Agent": f"tspu-monitor/{__version__}"},
            ) as response:
                return 200 <= response.status < 300
        except Exception as exc:  # noqa: BLE001 — сбой не должен мешать работе
            self.logger.debug("Телеметрия не отправлена: %s", exc)
            return False

    async def send_run(self, record: RunRecord) -> None:
        """Отправить install (однократно) и run. Никогда не выбрасывает."""
        if not self.active:
            return
        env = await self.collect_env()
        if not self.install_sent and await self._post(self.install_payload(env)):
            self._state["install_sent"] = True
            self._save()
        if record.analyses:
            await self._post(self.run_payload(record, env))

    async def send_test(self) -> bool:
        """Ручная проверка (команда ``telemetry test``); отчёт — вызывающему."""
        return await self._post(self.install_payload(await self.collect_env()))
