"""Анонимная статистика (включена по умолчанию, легко отключается).

Отправляется только при ``telemetry.enabled: true`` (по умолчанию включена;
отключается командой ``tspu-monitor telemetry disable``).
Полезная нагрузка не содержит адресов, имён хостов, секретов и иных
персональных данных — только обезличенные технические метрики.
Спецификация приёмника: ``docs/statistics-service-spec.md``.

Сбои отправки (сайт недоступен, нет сети, таймаут) полностью игнорируются:
пользователю ничего не выводится, в журнал попадает только DEBUG-строка.
"""

from __future__ import annotations

import platform
import uuid
from pathlib import Path
from typing import Any

from . import __version__
from .config import AppConfig
from .logging_setup import get_logger
from .models import RunRecord, Severity, utc_now_iso
from .utils import ensure_writable_dir, read_json, write_json_atomic

DEFAULT_URL = "https://statistics.fairen8.ru/api/v1/events"
STATE_FILE = "telemetry.json"


def _os_id() -> str:
    """Идентификатор дистрибутива (без версии ядра и имени хоста)."""
    try:
        release = platform.freedesktop_os_release()  # Python 3.10+
    except (AttributeError, OSError):
        release = {}
    if release.get("ID"):
        return str(release["ID"])
    return platform.system().lower() or "unknown"


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
            self._state: dict[str, Any] = state
            self._save()
        else:
            self._state = state

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
    def install_payload(self) -> dict[str, Any]:
        return {
            "event": "install",
            "client_id": self.client_id,
            "version": __version__,
            "sent_at": utc_now_iso(),
            "os": _os_id(),
            "arch": platform.machine() or "unknown",
            "python": platform.python_version(),
        }

    def run_payload(self, record: RunRecord) -> dict[str, Any]:
        counts = {"scenarios": 0, "checks": 0, "critical": 0, "warning": 0}
        scenarios: list[dict[str, Any]] = []
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
            scenarios.append(
                {
                    "profile": analysis.profile,
                    "level": analysis.level.name.lower(),
                    "score": analysis.score,
                    "types": [t.value for t in analysis.types],
                    "disconnect": analysis.disconnect.name.lower(),
                }
            )

        telegram = self.config.secrets.get("telegram", {}) or {}
        features = {
            "web": bool(self.config.get("web.enabled")),
            "webhook": bool(
                self.config.get("webhook.enabled") and self.config.get("webhook.url")
            ),
            "telegram": bool(telegram.get("enabled") and telegram.get("bot_token")),
        }

        return {
            "event": "run",
            "client_id": self.client_id,
            "version": __version__,
            "sent_at": utc_now_iso(),
            "duration_seconds": round(record.duration_seconds, 2),
            "max_level": record.max_level.name.lower(),
            "max_score": record.max_score,
            "scenarios": scenarios,
            "counts": counts,
            "features": features,
        }

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
        if not self.install_sent and await self._post(self.install_payload()):
            self._state["install_sent"] = True
            self._save()
        if record.analyses:
            await self._post(self.run_payload(record))

    async def send_test(self) -> bool:
        """Ручная проверка (команда ``telemetry test``); отчёт — вызывающему."""
        return await self._post(self.install_payload())
