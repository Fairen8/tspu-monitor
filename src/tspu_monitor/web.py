"""Веб-сервер: дашборд и REST API.

Запускается командой ``tspu-monitor web`` или вместе с демоном
(``daemon --web`` / ``web.enabled: true``). По умолчанию слушает
``127.0.0.1``; доступ по токену ``secrets.web.token`` (если задан).
"""

from __future__ import annotations

import hmac
import re
import threading
import webbrowser
from pathlib import Path

from aiohttp import web

from . import __version__
from .config import AppConfig
from .engine import Engine
from .logging_setup import get_logger
from .models import Severity, utc_now_iso
from .reporter import Reporter
from .utils import parse_iso

logger = get_logger("tspu.web")

TEMPLATES = Path(__file__).resolve().parent / "templates"
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


def is_loopback(host: str) -> bool:
    return host in LOOPBACK


def _extract_token(request: web.Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return str(request.query.get("token", ""))


def _authorized(request: web.Request, token: object) -> bool:
    if not token:
        return True
    supplied = _extract_token(request)
    return bool(supplied) and hmac.compare_digest(supplied, str(token))


def _unauthorized() -> web.Response:
    return web.json_response({"error": "unauthorized"}, status=401)


@web.middleware
async def _error_middleware(
    request: web.Request, handler
) -> web.StreamResponse:
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка обработки %s %s", request.method, request.path)
        return web.json_response({"error": str(exc)}, status=500)


def _stats(runs) -> dict[str, int]:
    checks = critical = warning = 0
    for run in runs:
        for analysis in run.analyses:
            for result in analysis.results:
                checks += 1
                if result.severity == Severity.CRITICAL:
                    critical += 1
                elif result.severity == Severity.WARNING:
                    warning += 1
    return {
        "runs": len(runs),
        "checks": checks,
        "critical": critical,
        "warning": warning,
    }


def build_app(
    config: AppConfig,
    engine: Engine | None = None,
    reporter: Reporter | None = None,
) -> web.Application:
    """Собрать aiohttp-приложение дашборда."""
    engine = engine or Engine(config)
    reporter = reporter or Reporter(config)
    token = config.secret("web.token")

    app = web.Application(middlewares=[_error_middleware])

    async def index(_: web.Request) -> web.StreamResponse:
        return web.FileResponse(TEMPLATES / "index.html")

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "version": __version__})

    async def summary(request: web.Request) -> web.Response:
        if not _authorized(request, token):
            return _unauthorized()
        limit = max(1, min(int(request.query.get("limit", 30)), 500))
        runs = engine.list_runs(limit=limit)
        last = engine.last_record or (runs[0] if runs else None)
        history = [
            {
                "run_id": run.run_id,
                "finished": run.finished,
                "max_score": run.max_score,
                "max_level": run.max_level.name.lower(),
            }
            for run in reversed(runs)
        ]
        return web.json_response(
            {
                "version": __version__,
                "generated_at": utc_now_iso(),
                "enabled": engine.manager.get_enabled_names(),
                "web": {
                    "host": config.get("web.host", "127.0.0.1"),
                    "port": int(config.get("web.port", 8787)),
                    "refresh_seconds": int(config.get("web.refresh_seconds", 30)),
                    "auth_required": bool(token),
                },
                "stats": _stats(runs),
                "history": history,
                "last_run": last.to_dict() if last else None,
            }
        )

    async def runs_list(request: web.Request) -> web.Response:
        if not _authorized(request, token):
            return _unauthorized()
        limit = max(1, min(int(request.query.get("limit", 50)), 500))
        return web.json_response(
            [run.to_dict() for run in engine.list_runs(limit=limit)]
        )

    async def run_detail(request: web.Request) -> web.Response:
        if not _authorized(request, token):
            return _unauthorized()
        run_id = request.match_info["run_id"]
        if not re.fullmatch(r"[0-9a-fA-F]{6,64}", run_id):
            return web.json_response({"error": "invalid run id"}, status=400)
        record = engine.load_run(run_id)
        if record is None:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(record.to_dict())

    async def check(request: web.Request) -> web.Response:
        if not _authorized(request, token):
            return _unauthorized()
        payload: dict = {}
        if request.can_read_body:
            try:
                payload = await request.json()
            except Exception:  # noqa: BLE001
                payload = {}
        profiles = payload.get("profiles")
        if profiles is not None and not isinstance(profiles, list):
            return web.json_response({"error": "profiles must be a list"}, status=400)
        samples = payload.get("samples")
        try:
            record = await engine.run(
                profiles=profiles,
                samples=int(samples) if samples is not None else None,
            )
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(record.to_dict())

    async def metrics(request: web.Request) -> web.Response:
        if not _authorized(request, token):
            return _unauthorized()
        runs = engine.list_runs(limit=200)
        last = engine.last_record or (runs[0] if runs else None)
        stats = _stats(runs)
        lines = [
            "# HELP tspu_monitor_score Уровень блокировок сценария (0-100)",
            "# TYPE tspu_monitor_score gauge",
        ]
        if last is not None:
            for analysis in last.analyses:
                labels = (
                    f'profile="{analysis.profile}",target="{analysis.target}",'
                    f'level="{analysis.level.name.lower()}"'
                )
                lines.append(f"tspu_monitor_score{{{labels}}} {analysis.score}")
            lines.append(
                "tspu_monitor_last_run_timestamp_seconds "
                f"{parse_iso(last.finished).timestamp():.0f}"
            )
        lines.append("# TYPE tspu_monitor_runs_total gauge")
        lines.append(f"tspu_monitor_runs_total {stats['runs']}")
        lines.append("# TYPE tspu_monitor_checks_total counter")
        for severity, value in (
            ("critical", stats["critical"]),
            ("warning", stats["warning"]),
        ):
            lines.append(
                f'tspu_monitor_checks_total{{severity="{severity}"}} {value}'
            )
        return web.Response(
            text="\n".join(lines) + "\n",
            content_type="text/plain; version=0.0.4",
        )

    app.router.add_get("/", index)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/summary", summary)
    app.router.add_get("/api/runs", runs_list)
    app.router.add_get("/api/runs/{run_id}", run_detail)
    app.router.add_post("/api/check", check)
    app.router.add_get("/metrics", metrics)
    return app


async def start_web(
    config: AppConfig,
    engine: Engine,
    reporter: Reporter,
    host: str,
    port: int,
) -> web.AppRunner:
    """Запустить веб-сервер в текущем event loop (для демона)."""
    app = build_app(config, engine, reporter)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Веб-дашборд: http://%s:%d/", host, port)
    return runner


def run_web(config: AppConfig, host: str, port: int, open_browser: bool = False) -> int:
    """Блокирующий запуск веб-сервера (для CLI)."""
    app = build_app(config)
    token = config.secret("web.token")
    url = f"http://{host}:{port}/"
    print(f"Веб-дашборд: {url}")
    print(
        "Доступ: "
        + ("по токену (secrets.web.token)" if token else "без авторизации (loopback)")
    )
    if open_browser:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    web.run_app(app, host=host, port=port, print=None)
    return 0
