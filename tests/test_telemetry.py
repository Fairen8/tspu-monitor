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
from tspu_monitor.telemetry import Telemetry

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


def make_record() -> RunRecord:
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
                score=72,
                level=BlockLevel.HIGH,
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
                        severity=Severity.CRITICAL,
                    )
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


async def test_enabled_sends_install_and_run(tmp_path, ingest):
    client, received = ingest
    telemetry = make_telemetry(tmp_path, url=str(client.make_url("/api/v1/events")))

    await telemetry.send_run(make_record())

    events = [event["event"] for event in received]
    assert "install" in events
    assert "run" in events

    run = next(event for event in received if event["event"] == "run")
    assert run["max_score"] == 72
    assert run["max_level"] == "high"
    assert run["scenarios"][0]["profile"] == "web"
    assert run["scenarios"][0]["types"] == ["rst_injection"]
    assert run["counts"] == {"scenarios": 1, "checks": 1, "critical": 1, "warning": 0}


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
