"""Тесты генератора отчётов."""

from __future__ import annotations

from tests.conftest import make_config
from tspu_monitor.config import AppConfig
from tspu_monitor.models import (
    Analysis,
    BlockLevel,
    BlockType,
    DisconnectLevel,
    ProbeResult,
    RunRecord,
    Severity,
    utc_now_iso,
)
from tspu_monitor.reporter import Reporter, compute_forecast


def make_record(score: int, level: BlockLevel, run_id: str = "r1") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        started=utc_now_iso(),
        finished=utc_now_iso(),
        duration_seconds=1.5,
        analyses=[
            Analysis(
                profile="web",
                title="WEB / HTTPS",
                target="twitter.com",
                score=score,
                level=level,
                types=[BlockType.RST_INJECTION],
                disconnect=DisconnectLevel.PERIODIC,
                causes=["Инъекция RST со стороны DPI/TSPU"],
                evidence=["TCP twitter.com:443: мгновенный RST"],
                recommendations=["Маскируйте трафик"],
                results=[
                    ProbeResult(
                        probe="tcp.connect",
                        target="twitter.com:443",
                        success=False,
                        severity=Severity.CRITICAL,
                        error="мгновенный RST",
                        duration_ms=3.0,
                    )
                ],
            )
        ],
    )


def test_generate_text_contains_sections(config: AppConfig):
    reporter = Reporter(config)
    record = make_record(55, BlockLevel.HIGH)
    text, payload = reporter.generate([record], record)
    assert "ИТОГОВАЯ ОЦЕНКА" in text
    assert "ДЕТАЛИ ПО СЦЕНАРИЯМ" in text
    assert "ПРОГНОЗ" in text
    assert "WEB / HTTPS" in text
    assert "Инъекция RST" in text


def test_generate_json_structure(config: AppConfig):
    reporter = Reporter(config)
    record = make_record(55, BlockLevel.HIGH)
    _, payload = reporter.generate([record], record)
    assert payload["summary"]["level"] == "high"
    assert payload["summary"]["score"] == 55
    assert "rst_injection" in payload["summary"]["types"]
    assert payload["profiles"][0]["profile"] == "web"
    assert payload["runs"] == 1


def test_save_writes_file(config: AppConfig):
    reporter = Reporter(config)
    path = reporter.save("test-content", name="custom.txt")
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "test-content"


def test_forecast_insufficient_data():
    forecast = compute_forecast([make_record(10, BlockLevel.LOW)])
    assert forecast["trend"] == "insufficient"


def test_forecast_worsening():
    records = [
        make_record(5, BlockLevel.LOW, "a"),
        make_record(10, BlockLevel.LOW, "b"),
        make_record(60, BlockLevel.HIGH, "c"),
        make_record(80, BlockLevel.FULL, "d"),
    ]
    forecast = compute_forecast(records)
    assert forecast["trend"] == "worsening"


def test_forecast_improving():
    records = [
        make_record(80, BlockLevel.FULL, "a"),
        make_record(70, BlockLevel.FULL, "b"),
        make_record(10, BlockLevel.LOW, "c"),
        make_record(5, BlockLevel.LOW, "d"),
    ]
    forecast = compute_forecast(records)
    assert forecast["trend"] == "improving"


def test_forecast_stable():
    records = [
        make_record(30, BlockLevel.MEDIUM, "a"),
        make_record(31, BlockLevel.MEDIUM, "b"),
    ]
    forecast = compute_forecast(records)
    assert forecast["trend"] == "stable"


def test_logs_section_can_be_disabled(tmp_path):
    config = make_config(tmp_path, settings={"reports": {"include_logs": False}})
    reporter = Reporter(config)
    record = make_record(10, BlockLevel.LOW)
    text, _ = reporter.generate([record], record)
    assert "отключено" in text


def test_empty_records_report(config: AppConfig):
    reporter = Reporter(config)
    text, payload = reporter.generate([], None)
    assert "нет запусков" in text.lower() or "За период нет запусков" in text
    assert payload["runs"] == 0
    assert payload["summary"]["level"] == "none"
