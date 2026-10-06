"""Тесты планировщика."""

from __future__ import annotations

from datetime import UTC, datetime

from tspu_monitor.scheduler import (
    Scheduler,
    next_interval_time,
    next_weekly_time,
    parse_hhmm,
)

UTC = UTC


def test_parse_hhmm():
    assert parse_hhmm("09:30") == (9, 30)
    assert parse_hhmm("00:00") == (0, 0)
    assert parse_hhmm("23:59") == (23, 59)
    assert parse_hhmm("25:00") == (9, 0)
    assert parse_hhmm("bad") == (9, 0)


def test_next_interval_time():
    now = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
    assert next_interval_time(now, 30) == datetime(2026, 10, 5, 10, 30, tzinfo=UTC)
    assert next_interval_time(now, 0) == datetime(2026, 10, 5, 10, 1, tzinfo=UTC)


def test_next_weekly_time_same_week():
    now = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)  # понедельник
    assert next_weekly_time(now, "monday", "11:00") == datetime(
        2026, 10, 5, 11, 0, tzinfo=UTC
    )


def test_next_weekly_time_next_week():
    now = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)  # понедельник
    assert next_weekly_time(now, "monday", "09:00") == datetime(
        2026, 10, 12, 9, 0, tzinfo=UTC
    )


def test_next_weekly_time_later_day():
    now = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)  # понедельник
    assert next_weekly_time(now, "friday", "09:00") == datetime(
        2026, 10, 9, 9, 0, tzinfo=UTC
    )


async def noop() -> None:
    return None


def test_scheduler_describe_initial():
    scheduler = Scheduler({}, on_check=noop, on_report=noop)
    description = scheduler.describe()
    assert description["next_check"] is None
    assert description["next_report"] is None
    assert description["check_interval_minutes"] == 60


def test_scheduler_interval_override():
    scheduler = Scheduler(
        {"scheduler": {"check_interval_minutes": 15}}, on_check=noop, on_report=noop
    )
    assert scheduler.check_interval_minutes == 15
