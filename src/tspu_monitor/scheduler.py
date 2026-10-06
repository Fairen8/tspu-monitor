"""Планировщик периодических проверок и отчётов.

Без внешних зависимостей: asyncio-цикл, который спит до ближайшего
события и вызывает переданные корутины.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from .logging_setup import get_logger

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def parse_hhmm(value: str) -> tuple[int, int]:
    """``"09:30"`` → ``(9, 30)``."""
    try:
        hour_s, minute_s = str(value).split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
    except (ValueError, AttributeError):
        return 9, 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return 9, 0
    return hour, minute


def next_interval_time(now: datetime, minutes: int) -> datetime:
    return now + timedelta(minutes=max(1, int(minutes)))


def next_weekly_time(now: datetime, weekday: str, hhmm: str) -> datetime:
    """Ближайшее наступление дня недели и времени (UTC)."""
    hour, minute = parse_hhmm(hhmm)
    target = WEEKDAYS.get(str(weekday).lower(), 0)
    days_ahead = (target - now.weekday()) % 7
    candidate = (now + timedelta(days=days_ahead)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


class Scheduler:
    """Периодический запуск проверок и еженедельного отчёта."""

    def __init__(
        self,
        settings: dict[str, Any],
        on_check: Callable[[], Awaitable[Any]],
        on_report: Callable[[], Awaitable[Any]],
    ) -> None:
        self.settings = settings
        self.on_check = on_check
        self.on_report = on_report
        self.logger = get_logger("tspu.scheduler")
        self._next_check: datetime | None = None
        self._next_report: datetime | None = None

    # -- расчёт времени ---------------------------------------------------
    @property
    def check_interval_minutes(self) -> int:
        return int(self.settings.get("scheduler", {}).get("check_interval_minutes", 60))

    def _initial_check_time(self, now: datetime) -> datetime:
        delay = int(self.settings.get("scheduler", {}).get("startup_delay_seconds", 15))
        return now + timedelta(seconds=max(0, delay))

    def next_report_time(self, now: datetime) -> datetime:
        sched = self.settings.get("scheduler", {})
        return next_weekly_time(
            now,
            str(sched.get("report_day", "monday")),
            str(sched.get("report_time", "09:00")),
        )

    def describe(self) -> dict[str, str | None]:
        """Ближайшие запуски (для ``status``)."""
        return {
            "next_check": self._next_check.isoformat() if self._next_check else None,
            "next_report": self._next_report.isoformat() if self._next_report else None,
            "check_interval_minutes": self.check_interval_minutes,
        }

    # -- цикл -------------------------------------------------------------
    async def run(self, stop_event: asyncio.Event) -> None:
        now = datetime.now(UTC)
        self._next_check = self._initial_check_time(now)
        self._next_report = self.next_report_time(now)
        self.logger.info(
            "Планировщик запущен: проверки каждые %d мин (первая в %s), "
            "отчёт в %s",
            self.check_interval_minutes,
            self._next_check.strftime("%H:%M:%S"),
            self._next_report.strftime("%Y-%m-%d %H:%M UTC"),
        )

        while not stop_event.is_set():
            next_event = min(self._next_check, self._next_report)
            delay = max(0.0, (next_event - datetime.now(UTC)).total_seconds())
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                break  # получен сигнал остановки
            except TimeoutError:
                pass

            now = datetime.now(UTC)
            if now >= self._next_check:
                await self._safe(self.on_check, "периодическая проверка")
                self._next_check = next_interval_time(now, self.check_interval_minutes)
            if now >= self._next_report:
                await self._safe(self.on_report, "еженедельный отчёт")
                self._next_report = self.next_report_time(now)

        self.logger.info("Планировщик остановлен")

    async def _safe(self, callback: Callable[[], Awaitable[Any]], label: str) -> None:
        try:
            await callback()
        except Exception:  # noqa: BLE001
            self.logger.exception("Ошибка при выполнении задачи '%s'", label)
