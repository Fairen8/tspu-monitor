"""Тесты добровольной анонимной статистики."""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tests.conftest import make_config
from tspu_monitor.engine import Engine
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
from tspu_monitor.telemetry import Telemetry, percentile

FORBIDDEN_KEYS = {
    "target",
    "host",
    "ip",
    "evidence",
    "causes",
    "recommendations",
    "results",
    "raw",
    "error",
    "error_message",
}


@pytest.fixture(autouse=True)
def fake_env_checks(monkeypatch):
    """Тесты не должны ходить в сеть: подменяем проверки IPv6/Telegram."""

    async def fake_ipv6(self) -> bool:
        return True

    async def fake_telegram(self) -> bool:
        return True

    monkeypatch.setattr(Telemetry, "_check_ipv6", fake_ipv6)
    monkeypatch.setattr(Telemetry, "_check_telegram_api", fake_telegram)


def make_record(
    level: BlockLevel = BlockLevel.HIGH, score: int = 72
) -> RunRecord:
    severity = Severity.CRITICAL if level != BlockLevel.NONE else Severity.INFO
    return RunRecord(
        run_id="abc123def456",
        started=utc_now_iso(),
        finished=utc_now_iso(),
        duration_seconds=1.23,
        analyses=[
            Analysis(
                profile="web",
                title="WEB / HTTPS",
                target="twitter.com",
                score=score,
                level=level,
                types=[BlockType.RST_INJECTION],
                disconnect=DisconnectLevel.PERIODIC,
                causes=["Инъекция RST"],
                evidence=["TLS twitter.com:443: handshake прерван"],
                recommendations=["Смените SNI"],
                results=[
                    ProbeResult(
                        probe="tcp.connect",
                        target="twitter.com:443",
                        success=False,
                        severity=severity,
                        duration_ms=12.0,
                        data={
                            "fast_rst_count": 2,
                            "timeout_count": 1,
                            "refused_count": 2,
                            "rtt_samples_ms": [10.0, 20.0, 30.0],
                        },
                    ),
                    ProbeResult(
                        probe="tls.handshake",
                        target="twitter.com:443[twitter.com]",
                        success=False,
                        severity=severity,
                        duration_ms=55.0,
                        data={"error_class": "reset", "handshake_ms": 50.0},
                        error="соединение сброшено (RST)",
                    ),
                    ProbeResult(
                        probe="http.check",
                        target="twitter.com",
                        success=False,
                        severity=Severity.WARNING if level != BlockLevel.NONE else Severity.INFO,
                        duration_ms=100.0,
                        data={},
                        error="ConnectionRefusedError: тест",
                    ),
                ],
            )
        ],
    )


def collect_keys(obj, out: set[str]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            out.add(str(key))
            collect_keys(value, out)
    elif isinstance(obj, list):
        for item in obj:
            collect_keys(item, out)


@pytest.fixture
async def ingest():
    received: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        received.append(await request.json())
        return web.Response(status=202)

    app = web.Application()
    app.router.add_post("/api/v1/events", handler)
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client, received
    await client.close()


def make_telemetry(tmp_path, url: str | None = None, enabled: bool = True) -> Telemetry:
    settings = {
        "scenarios": {"enabled": []},
        "telemetry": {"enabled": enabled, "timeout_seconds": 2},
    }
    if url:
        settings["telemetry"]["url"] = url
    return Telemetry(make_config(tmp_path, settings=settings))


async def test_disabled_does_not_send(tmp_path, ingest):
    client, received = ingest
    telemetry = make_telemetry(
        tmp_path, url=str(client.make_url("/api/v1/events")), enabled=False
    )
    await telemetry.send_run(make_record())
    assert received == []


def test_telemetry_enabled_by_default():
    from tspu_monitor.config import DEFAULT_SETTINGS

    assert DEFAULT_SETTINGS["telemetry"]["enabled"] is True


def test_percentile():
    assert percentile([], 0.5) is None
    assert percentile([5.0], 0.5) == 5.0
    assert percentile([10.0, 20.0, 30.0, 50.0], 0.5) == 25.0
    assert percentile([10.0, 20.0, 30.0, 50.0], 0.95) == 47.0


async def test_enabled_sends_install_and_run(tmp_path, ingest):
    client, received = ingest
    telemetry = make_telemetry(tmp_path, url=str(client.make_url("/api/v1/events")))

    await telemetry.send_run(make_record())

    events = [event["event"] for event in received]
    assert "install" in events
    assert "run" in events

    install = next(event for event in received if event["event"] == "install")
    assert install["ipv6"] is True
    assert install["dns_mode"] in ("system", "isp")

    run = next(event for event in received if event["event"] == "run")
    assert run["max_score"] == 72
    assert run["max_level"] == "high"
    assert run["delta_score"] is None
    assert run["first_critical_at"] is not None
    assert run["errors"] == ["ConnectionRefusedError"]
    assert run["machine"]["cores"] >= 1
    assert run["profiles_on"] == []
    assert run["schedule_min"] == 60
    assert run["tg_api_ok"] is True
    assert run["ipv6"] is True

    scenario = run["scenarios"][0]
    assert scenario["profile"] == "web"
    assert scenario["types"] == ["rst_injection"]
    assert scenario["latency_p50"] == 30.0
    assert scenario["latency_p95"] == 90.0
    assert scenario["timeouts"] == 1
    assert scenario["fail_stage"] == "reset"
    assert scenario["anomalies"] == {"rst": 3, "ttl": 0, "ipid": 0}
    assert scenario["block_started"] is True
    assert scenario["block_ended"] is False
    assert run["counts"] == {"scenarios": 1, "checks": 3, "critical": 2, "warning": 1}


async def test_block_transitions_and_delta(tmp_path, ingest):
    client, received = ingest
    telemetry = make_telemetry(tmp_path, url=str(client.make_url("/api/v1/events")))

    await telemetry.send_run(make_record(level=BlockLevel.NONE, score=0))
    await telemetry.send_run(make_record(level=BlockLevel.HIGH, score=60))
    started = [e for e in received if e["event"] == "run"][-1]
    assert started["delta_score"] == 60
    assert started["scenarios"][0]["block_started"] is True
    assert started["scenarios"][0]["block_ended"] is False

    await telemetry.send_run(make_record(level=BlockLevel.MEDIUM, score=30))
    ended = [e for e in received if e["event"] == "run"][-1]
    assert ended["delta_score"] == -30
    assert ended["scenarios"][0]["block_started"] is False
    assert ended["scenarios"][0]["block_ended"] is True


async def test_env_cache_avoids_rechecks(tmp_path, ingest, monkeypatch):
    calls = {"ipv6": 0, "tg": 0}

    async def counting_ipv6(self) -> bool:
        calls["ipv6"] += 1
        return True

    async def counting_telegram(self) -> bool:
        calls["tg"] += 1
        return False

    monkeypatch.setattr(Telemetry, "_check_ipv6", counting_ipv6)
    monkeypatch.setattr(Telemetry, "_check_telegram_api", counting_telegram)

    client, _ = ingest
    telemetry = make_telemetry(tmp_path, url=str(client.make_url("/api/v1/events")))
    first = await telemetry.collect_env()
    second = await telemetry.collect_env()
    assert calls == {"ipv6": 1, "tg": 1}
    assert first["ipv6"] is True and first["tg_api_ok"] is False
    assert second["ipv6"] is True and second["tg_api_ok"] is False


async def test_payload_has_no_identifying_data(tmp_path, ingest):
    client, received = ingest
    telemetry = make_telemetry(tmp_path, url=str(client.make_url("/api/v1/events")))

    await telemetry.send_run(make_record())

    for payload in received:
        keys: set[str] = set()
        collect_keys(payload, keys)
        assert FORBIDDEN_KEYS.isdisjoint(keys), keys & FORBIDDEN_KEYS
        assert "twitter.com" not in json.dumps(payload, ensure_ascii=False)
        assert "client_id" in keys
        assert all(" " not in item for item in payload.get("errors", []))


async def test_client_id_is_stable(tmp_path):
    first = make_telemetry(tmp_path, enabled=False).client_id
    second = make_telemetry(tmp_path, enabled=False).client_id
    assert first
    assert first == second


async def test_engine_sends_install_event(tmp_path, ingest):
    client, received = ingest
    config = make_config(
        tmp_path,
        settings={
            "scenarios": {"enabled": []},
            "telemetry": {
                "enabled": True,
                "url": str(client.make_url("/api/v1/events")),
                "timeout_seconds": 2,
            },
        },
    )
    engine = Engine(config)
    await engine.run()

    assert any(event["event"] == "install" for event in received)


async def test_failures_are_silent(tmp_path):
    config = make_config(
        tmp_path,
        settings={
            "scenarios": {"enabled": []},
            "telemetry": {
                "enabled": True,
                "url": "http://127.0.0.1:9/api/v1/events",
                "timeout_seconds": 0.5,
            },
        },
    )
    telemetry = Telemetry(config)
    await telemetry.send_run(make_record())  # не должно бросать исключение
    assert await telemetry.send_test() is False


async def test_send_test_reports_result(tmp_path, ingest):
    client, received = ingest
    telemetry = make_telemetry(tmp_path, url=str(client.make_url("/api/v1/events")))
    assert await telemetry.send_test() is True
    assert received and received[0]["event"] == "install"
