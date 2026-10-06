"""Тесты моделей данных."""

from __future__ import annotations

from tspu_monitor.models import (
    Analysis,
    BlockLevel,
    BlockType,
    DisconnectLevel,
    ProbeResult,
    RunRecord,
    Severity,
    disconnect_from_ratio,
    exit_code_for_level,
    level_from_score,
    utc_now_iso,
)


def test_utc_now_iso_format():
    ts = utc_now_iso()
    assert ts.endswith("Z") and "T" in ts


def test_severity_rank_and_title():
    assert Severity.INFO.rank < Severity.WARNING.rank < Severity.CRITICAL.rank
    assert Severity.CRITICAL.title


def test_level_from_score_thresholds():
    assert level_from_score(0) == BlockLevel.NONE
    assert level_from_score(1) == BlockLevel.LOW
    assert level_from_score(24) == BlockLevel.LOW
    assert level_from_score(25) == BlockLevel.MEDIUM
    assert level_from_score(49) == BlockLevel.MEDIUM
    assert level_from_score(50) == BlockLevel.HIGH
    assert level_from_score(69) == BlockLevel.HIGH
    assert level_from_score(70) == BlockLevel.FULL
    assert level_from_score(100) == BlockLevel.FULL


def test_level_from_score_custom_thresholds():
    assert level_from_score(10, {"medium": 10}) == BlockLevel.MEDIUM
    assert level_from_score(5, {"medium": "bad"}) == BlockLevel.LOW


def test_disconnect_from_ratio():
    assert disconnect_from_ratio(0.0, attempts=3) == DisconnectLevel.NONE
    assert disconnect_from_ratio(1.0, attempts=1) == DisconnectLevel.NONE
    assert disconnect_from_ratio(0.1, attempts=3) == DisconnectLevel.RARE
    assert disconnect_from_ratio(0.3, attempts=3) == DisconnectLevel.PERIODIC
    assert disconnect_from_ratio(0.6, attempts=3) == DisconnectLevel.FREQUENT
    assert disconnect_from_ratio(0.9, attempts=3) == DisconnectLevel.CONSTANT


def test_probe_result_roundtrip():
    original = ProbeResult(
        probe="tcp.connect",
        target="1.1.1.1:443",
        success=False,
        severity=Severity.CRITICAL,
        duration_ms=12.5,
        data={"port": 443},
        error="мгновенный RST",
    )
    restored = ProbeResult.from_dict(original.to_dict())
    assert restored.probe == original.probe
    assert restored.severity == Severity.CRITICAL
    assert restored.data == {"port": 443}
    assert restored.error == original.error


def test_probe_result_skipped():
    result = ProbeResult(
        probe="raw.ttl", target="n/a", success=False, data={"skipped": True}
    )
    assert result.skipped is True


def test_analysis_roundtrip():
    analysis = Analysis(
        profile="web",
        title="WEB",
        target="twitter.com",
        score=45,
        level=BlockLevel.MEDIUM,
        types=[BlockType.RST_INJECTION, BlockType.HTTP_PLUG],
        disconnect=DisconnectLevel.PERIODIC,
        causes=["Инъекция RST"],
        evidence=["tcp.connect: мгновенный RST"],
        recommendations=["Маскируйте трафик"],
        results=[
            ProbeResult(probe="tcp.connect", target="twitter.com:443", success=False)
        ],
    )
    restored = Analysis.from_dict(analysis.to_dict())
    assert restored.level == BlockLevel.MEDIUM
    assert restored.types == [BlockType.RST_INJECTION, BlockType.HTTP_PLUG]
    assert restored.disconnect == DisconnectLevel.PERIODIC
    assert restored.results[0].probe == "tcp.connect"


def test_run_record_max_level():
    record = RunRecord(
        run_id="abc",
        started=utc_now_iso(),
        finished=utc_now_iso(),
        duration_seconds=1.0,
        analyses=[
            Analysis(profile="a", title="A", target="x", score=0),
            Analysis(profile="b", title="B", target="y", score=80, level=BlockLevel.FULL),
        ],
    )
    assert record.max_level == BlockLevel.FULL
    assert record.max_score == 80
    restored = RunRecord.from_dict(record.to_dict())
    assert restored.max_level == BlockLevel.FULL
    assert len(restored.analyses) == 2


def test_exit_code_for_level():
    assert exit_code_for_level(BlockLevel.NONE) == 0
    assert exit_code_for_level(BlockLevel.MEDIUM) == 2
    assert exit_code_for_level(BlockLevel.HIGH) == 3
    assert exit_code_for_level(BlockLevel.FULL) == 3
