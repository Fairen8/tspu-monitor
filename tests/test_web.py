"""Тесты веб-дашборда и REST API."""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tests.conftest import make_config
from tspu_monitor.cli import _check_web_access
from tspu_monitor.web import build_app

TOKEN = "secret-token"


def web_config(tmp_path: Path, token: str | None = None):
    secrets = {"web": {"token": token}} if token else {}
    return make_config(
        tmp_path,
        settings={"scenarios": {"enabled": []}},
        secrets=secrets,
    )


async def _client(config) -> TestClient:
    client = TestClient(TestServer(build_app(config)))
    await client.start_server()
    return client


@pytest.fixture
async def client(tmp_path):
    instance = await _client(web_config(tmp_path, token=TOKEN))
    yield instance
    await instance.close()


@pytest.fixture
async def anon_client(tmp_path):
    instance = await _client(web_config(tmp_path))
    yield instance
    await instance.close()


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


async def test_health_open(client):
    resp = await client.get("/api/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"
    assert data["version"]


async def test_index_serves_dashboard(client):
    resp = await client.get("/")
    assert resp.status == 200
    text = await resp.text()
    assert "TSPU Monitor" in text
    assert "Проверить сейчас" in text
    assert "/api/summary" in text


async def test_summary_requires_token(client):
    assert (await client.get("/api/summary")).status == 401
    assert (await client.get("/api/summary", headers=auth())).status == 200


async def test_summary_with_query_token(client):
    resp = await client.get(f"/api/summary?token={TOKEN}")
    assert resp.status == 200


async def test_summary_shape(client):
    resp = await client.get("/api/summary?limit=5", headers=auth())
    data = await resp.json()
    assert set(
        ["version", "generated_at", "enabled", "web", "stats", "history", "last_run"]
    ) <= set(data)
    assert data["web"]["auth_required"] is True
    assert isinstance(data["history"], list)


async def test_check_endpoint_runs_scenarios(client):
    resp = await client.post("/api/check", json={}, headers=auth())
    assert resp.status == 200
    record = await resp.json()
    assert record["max_level"] == "none"
    assert record["run_id"]


async def test_runs_and_detail(client):
    created = await (await client.post("/api/check", json={}, headers=auth())).json()
    run_id = created["run_id"]

    listing = await client.get("/api/runs?limit=5", headers=auth())
    assert listing.status == 200
    items = await listing.json()
    assert any(item["run_id"] == run_id for item in items)

    detail = await client.get(f"/api/runs/{run_id}", headers=auth())
    assert detail.status == 200
    assert (await detail.json())["run_id"] == run_id

    missing = await client.get("/api/runs/deadbeef", headers=auth())
    assert missing.status == 404

    invalid = await client.get("/api/runs/not-hex-id", headers=auth())
    assert invalid.status == 400


async def test_check_rejects_bad_profiles(client):
    resp = await client.post(
        "/api/check", json={"profiles": "web"}, headers=auth()
    )
    assert resp.status == 400


async def test_metrics_endpoint(client):
    resp = await client.get("/metrics", headers=auth())
    assert resp.status == 200
    text = await resp.text()
    assert "tspu_monitor_runs_total" in text
    assert "tspu_monitor_checks_total" in text


async def test_anonymous_access_without_token(anon_client):
    resp = await anon_client.get("/api/summary")
    assert resp.status == 200
    data = await resp.json()
    assert data["web"]["auth_required"] is False


def test_web_access_guard():
    assert _check_web_access("127.0.0.1", None, False) is None
    assert _check_web_access("localhost", None, False) is None
    assert _check_web_access("0.0.0.0", None, False) is not None
    assert _check_web_access("0.0.0.0", TOKEN, False) is None
    assert _check_web_access("0.0.0.0", None, True) is None
