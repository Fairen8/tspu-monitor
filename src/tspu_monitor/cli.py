"""Консольный интерфейс ``tspu-monitor``.

Основной способ управления монитором. Команды:

* ``check`` — запустить проверки и показать диагноз;
* ``report`` — собрать TXT/JSON-отчёт за период;
* ``status`` — состояние и расписание;
* ``scenarios`` — список/включение/выключение сценариев;
* ``config`` — просмотр, изменение и проверка конфигурации;
* ``logs`` — последние строки журналов;
* ``self-test`` — проверка окружения;
* ``daemon`` — периодические проверки, отчёты, webhook и Telegram;
* ``web`` — веб-дашборд и REST API;
* ``version``.

Коды возврата: 0 — норма, 1 — ошибка, 2 — деградация (средний уровень),
3 — блокировка (высокий/полный уровень).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import shutil
import signal
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import yaml

from . import __version__
from .config import (
    CONFIG_ALLOWED_KEYS,
    AppConfig,
    coerce_config_value,
    get_dotted,
    load_config,
    validate_config,
)
from .engine import Engine
from .logging_setup import get_logger, read_last_lines, setup_logging
from .models import (
    EXIT_ERROR,
    EXIT_OK,
    BlockLevel,
    RunRecord,
    exit_code_for_level,
    utc_now,
)
from .notifier import WebhookNotifier
from .reporter import Reporter
from .scheduler import Scheduler, next_interval_time, next_weekly_time
from .utils import fmt_dt, truncate

logger = get_logger("tspu.cli")

LOG_FILES = ("main.log", "probes.log", "telegram.log")


# ---------------------------------------------------------------------------
# Оформление
# ---------------------------------------------------------------------------


class Style:
    """ANSI-оформление вывода (можно отключить)."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def cyan(self, text: str) -> str:
        return self._wrap("36", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def level(self, level: BlockLevel, text: str) -> str:
        if level >= BlockLevel.HIGH:
            return self.red(text)
        if level == BlockLevel.MEDIUM:
            return self.yellow(text)
        return self.green(text)


def _color_enabled(args: argparse.Namespace) -> bool:
    if getattr(args, "no_color", False) or os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tspu-monitor",
        description="TSPU Monitor 2 — консольная диагностика блокировок DPI/TSPU.",
    )
    parser.add_argument("--config-dir", default=None, help="Каталог с конфигурацией")
    parser.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING/ERROR")
    parser.add_argument("--no-color", action="store_true", help="Отключить цвета")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="command")

    p_check = sub.add_parser("check", help="Запустить проверки")
    p_check.add_argument("profiles", nargs="*", help="Имена сценариев (по умолчанию все)")
    p_check.add_argument("--json", action="store_true", help="Вывод в JSON")
    p_check.add_argument("--samples", type=int, default=None, help="Повторов на пробу")
    p_check.add_argument("--webhook", action="store_true", help="Отправить webhook")
    p_check.add_argument("--quiet", action="store_true", help="Без вывода (только код)")

    p_report = sub.add_parser("report", help="Сформировать отчёт")
    p_report.add_argument("--hours", type=int, default=None, help="Окно отчёта, часов")
    p_report.add_argument("--json", action="store_true", help="JSON-отчёт в stdout")
    p_report.add_argument("--output", default=None, help="Имя файла отчёта")
    p_report.add_argument("--no-save", action="store_true", help="Не сохранять файл")
    p_report.add_argument("--send", action="store_true", help="Отправить в Telegram")

    p_status = sub.add_parser("status", help="Показать состояние")
    p_status.add_argument("--json", action="store_true")

    p_scen = sub.add_parser("scenarios", help="Управление сценариями")
    scen_sub = p_scen.add_subparsers(dest="scen_action", required=True)
    p_scen_list = scen_sub.add_parser("list", help="Список сценариев")
    p_scen_list.add_argument("--json", action="store_true")
    p_scen_enable = scen_sub.add_parser("enable", help="Включить сценарий")
    p_scen_enable.add_argument("name")
    p_scen_disable = scen_sub.add_parser("disable", help="Выключить сценарий")
    p_scen_disable.add_argument("name")

    p_config = sub.add_parser("config", help="Конфигурация")
    cfg_sub = p_config.add_subparsers(dest="cfg_action", required=True)
    p_cfg_show = cfg_sub.add_parser("show", help="Показать settings.yaml")
    p_cfg_show.add_argument("--json", action="store_true")
    p_cfg_get = cfg_sub.add_parser("get", help="Получить значение")
    p_cfg_get.add_argument("key")
    p_cfg_set = cfg_sub.add_parser("set", help="Установить значение")
    p_cfg_set.add_argument("key")
    p_cfg_set.add_argument("value")
    cfg_sub.add_parser("validate", help="Проверить конфигурацию")

    p_logs = sub.add_parser("logs", help="Показать журнал")
    p_logs.add_argument("--lines", "-n", type=int, default=100)
    p_logs.add_argument("--file", choices=LOG_FILES, default="main.log")

    p_self = sub.add_parser("self-test", help="Проверить окружение")
    p_self.add_argument("--json", action="store_true")

    p_daemon = sub.add_parser("daemon", help="Демон: расписание, отчёты, уведомления")
    p_daemon.add_argument("--interval", type=int, default=None, help="Интервал, минут")
    p_daemon.add_argument("--no-telegram", action="store_true")
    p_daemon.add_argument("--no-webhook", action="store_true")
    p_daemon.add_argument("--web", action="store_true", help="Запустить веб-дашборд")

    p_web = sub.add_parser("web", help="Веб-дашборд с REST API")
    p_web.add_argument("--host", default=None, help="Адрес (по умолчанию web.host)")
    p_web.add_argument("--port", type=int, default=None, help="Порт (по умолчанию web.port)")
    p_web.add_argument("--open", action="store_true", help="Открыть браузер")
    p_web.add_argument(
        "--allow-remote-no-auth",
        action="store_true",
        help="Разрешить не-loopback адрес без токена (не рекомендуется)",
    )

    sub.add_parser("version", help="Версия")
    return parser


# ---------------------------------------------------------------------------
# Общие помощники
# ---------------------------------------------------------------------------


def _load(args: argparse.Namespace, console: bool = False) -> AppConfig:
    config = load_config(args.config_dir)
    level = args.log_level or config.get("general.log_level", "INFO")
    setup_logging(level=level, log_dir=config.log_dir, console=console)
    return config


def _print_record(record: RunRecord, style: Style) -> None:
    if not record.analyses:
        print("Нет активных сценариев. Включите: tspu-monitor scenarios enable <имя>")
        return
    header = (
        f"Уровень блокировок: {record.max_level.title} "
        f"({record.max_score}/100), запуск {record.run_id}, "
        f"{record.duration_seconds:.1f} с"
    )
    print(style.level(record.max_level, style.bold(header)))
    print()
    for analysis in sorted(record.analyses, key=lambda a: -a.score):
        line = (
            f"{analysis.level.name:<6} {analysis.title} — {analysis.level.title} "
            f"({analysis.score}/100)"
        )
        print(style.level(analysis.level, line))
        if analysis.types:
            print("   Типы: " + "; ".join(analysis.type_titles))
        print(f"   Цель: {analysis.target}")
        if analysis.disconnect.name != "NONE":
            print("   Обрывы: " + analysis.disconnect.title)
        for cause in analysis.causes[:4]:
            print("   Причина: " + truncate(cause, 90))
        for evidence in analysis.evidence[:3]:
            print("   Факт: " + truncate(evidence, 90))
        for recommendation in analysis.recommendations[:4]:
            print("   Рекомендация: " + truncate(recommendation, 90))
        print()


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------


def cmd_check(args: argparse.Namespace, config: AppConfig) -> int:
    engine = Engine(config)
    style = Style(_color_enabled(args))

    def on_progress(analysis: Any) -> None:
        if args.quiet or args.json or not sys.stdout.isatty():
            return
        line = (
            f"  {analysis.level.name:<6} {analysis.title} — {analysis.level.title} "
            f"({analysis.score}/100)"
        )
        print(style.level(analysis.level, line))

    try:
        record = asyncio.run(
            engine.run(
                profiles=args.profiles or None,
                samples=args.samples,
                progress=on_progress,
            )
        )
    except ValueError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))
    elif not args.quiet:
        _print_record(record, style)

    if args.webhook:
        sent = asyncio.run(WebhookNotifier(config).notify(record))
        if not args.quiet:
            message = (
                "Webhook отправлен."
                if sent
                else "Webhook не отправлен (выключен/не настроен/уровень ниже порога)."
            )
            print(message)

    return exit_code_for_level(record.max_level)


def cmd_report(args: argparse.Namespace, config: AppConfig) -> int:
    engine = Engine(config)
    reporter = Reporter(config)
    records = engine.list_runs(limit=500)
    hours = args.hours or int(config.get("scheduler.report_interval_hours", 168))
    window_start = utc_now() - timedelta(hours=max(1, hours))
    text, payload = reporter.generate(
        records, engine.last_record, window_start=window_start
    )

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not args.no_save:
            path = reporter.save(text, name=args.output)
            print(f"Отчёт сохранён: {path}")
        print(text)

    if args.send:
        from .telegram_bot import TelegramBot

        bot = TelegramBot(config, engine, reporter)
        if not bot.active:
            print(
                "Telegram не настроен (secrets.telegram.enabled/bot_token/allowed_users).",
                file=sys.stderr,
            )
            return EXIT_ERROR
        chat_id = bot.default_chat_id()
        filename = args.output or f"report-{datetime.now():%Y%m%d-%H%M%S}.txt"
        ok = asyncio.run(
            bot.send_document(chat_id, text, filename, caption="Отчёт TSPU Monitor")
        )
        print("Отчёт отправлен в Telegram." if ok else "Не удалось отправить отчёт.")
    return EXIT_OK


def cmd_status(args: argparse.Namespace, config: AppConfig) -> int:
    engine = Engine(config)
    snapshot = engine.status_snapshot()
    now = datetime.now(UTC)
    interval = int(config.get("scheduler.check_interval_minutes", 60))
    next_check = next_interval_time(now, interval)
    next_report = next_weekly_time(
        now,
        str(config.get("scheduler.report_day", "monday")),
        str(config.get("scheduler.report_time", "09:00")),
    )

    if args.json:
        payload = {
            "version": __version__,
            "config_dir": snapshot["config_dir"],
            "data_dir": snapshot["data_dir"],
            "log_dir": str(config.log_dir),
            "enabled_scenarios": snapshot["enabled"],
            "known_scenarios": snapshot["known"],
            "next_check": next_check.isoformat(),
            "next_report": next_report.isoformat(),
            "last_run": snapshot["last_run"],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK

    style = Style(_color_enabled(args))
    print(style.bold(f"TSPU Monitor {__version__}"))
    print(f"Конфигурация: {snapshot['config_dir']}")
    print(f"Данные:       {snapshot['data_dir']}")
    print(f"Журналы:      {config.log_dir}")
    print(f"Сценарии ({len(snapshot['enabled'])}): " + ", ".join(snapshot["enabled"]))
    print(f"Ближайшая проверка: {fmt_dt(next_check)}")
    print(
        f"Ближайший отчёт:    {fmt_dt(next_report)} "
        f"(интервал проверок: {interval} мин)"
    )
    last = snapshot.get("last_run")
    if last:
        level = BlockLevel[str(last.get("max_level", "none")).upper()]
        print(
            style.level(
                level,
                f"Последний запуск: {last.get('run_id')} — {level.title} "
                f"({last.get('max_score', 0)}/100), {last.get('finished')}",
            )
        )
    else:
        print("Последний запуск: данных нет")
    return EXIT_OK


def cmd_scenarios(args: argparse.Namespace, config: AppConfig) -> int:
    engine = Engine(config)
    if args.scen_action == "list":
        items = engine.manager.list_all()
        if args.json:
            print(json.dumps(items, ensure_ascii=False, indent=2))
            return EXIT_OK
        for item in items:
            mark = "x" if item["enabled"] else " "
            custom = " (custom)" if item["custom"] else ""
            print(
                f"[{mark}] {item['name']:<14} v{item['version']:<6} {item['title']}{custom}"
            )
        return EXIT_OK
    if args.scen_action == "enable":
        if not engine.manager.enable(args.name):
            print(f"Сценарий '{args.name}' не найден.", file=sys.stderr)
            return EXIT_ERROR
        print(f"Включён: {args.name}")
        return EXIT_OK
    if args.scen_action == "disable":
        if not engine.manager.disable(args.name):
            print(f"Сценарий '{args.name}' не активен.", file=sys.stderr)
            return EXIT_ERROR
        print(f"Выключен: {args.name}")
        return EXIT_OK
    return EXIT_ERROR


def cmd_config(args: argparse.Namespace, config: AppConfig) -> int:
    if args.cfg_action == "show":
        if args.json:
            print(json.dumps(config.settings, ensure_ascii=False, indent=2))
        else:
            print(
                yaml.safe_dump(config.settings, allow_unicode=True, sort_keys=False)
            )
        return EXIT_OK
    if args.cfg_action == "get":
        value = get_dotted(config.settings, args.key, "<нет>")
        print(value)
        return EXIT_OK
    if args.cfg_action == "set":
        try:
            value = coerce_config_value(args.key, args.value)
        except KeyError:
            allowed = ", ".join(sorted(CONFIG_ALLOWED_KEYS))
            print(f"Ключ '{args.key}' менять нельзя. Доступные: {allowed}", file=sys.stderr)
            return EXIT_ERROR
        except (ValueError, TypeError):
            print(f"Не удалось преобразовать значение '{args.value}'", file=sys.stderr)
            return EXIT_ERROR
        config.set(args.key, value)
        config.save_settings()
        print(f"{args.key} = {value}")
        return EXIT_OK
    if args.cfg_action == "validate":
        problems = validate_config(config)
        if not problems:
            print("Конфигурация корректна.")
            return EXIT_OK
        print(f"Найдено замечаний: {len(problems)}")
        for problem in problems:
            print(f"  - {problem}")
        return EXIT_OK
    return EXIT_ERROR


def cmd_logs(args: argparse.Namespace, config: AppConfig) -> int:
    path = config.log_dir / args.file
    lines = read_last_lines(path, n=max(1, args.lines))
    if not lines:
        print(f"Журнал {path} пуст или не найден.", file=sys.stderr)
        return EXIT_ERROR
    for line in lines:
        print(line)
    return EXIT_OK


def cmd_self_test(args: argparse.Namespace, config: AppConfig) -> int:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "", critical: bool = False) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail, "critical": critical})

    py_ok = sys.version_info >= (3, 11)
    add("python>=3.11", py_ok, sys.version.split()[0], critical=True)

    binaries = ["ping", "traceroute", "nmap", "dig", "openssl"]
    for binary in binaries:
        path = shutil.which(binary)
        add(f"binary:{binary}", bool(path), path or "не найден")

    scapy_available = importlib.util.find_spec("scapy") is not None
    add(
        "scapy (raw)",
        scapy_available,
        "установлен" if scapy_available else "нет (raw-пробы пропускаются)",
    )
    is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    add("root/CAP_NET_RAW", is_root, "есть" if is_root else "нет (raw-пробы пропускаются)")

    settings_ok = config.settings_path.exists()
    add("settings.yaml", settings_ok, str(config.settings_path), critical=True)
    secrets_ok = config.secrets_path.exists()
    add("secrets.yaml", secrets_ok, "найден" if secrets_ok else "не найден")

    for label, directory in (
        ("data_dir", config.data_dir),
        ("reports_dir", config.reports_dir),
        ("log_dir", config.log_dir),
    ):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            writable = os.access(directory, os.W_OK)
        except OSError:
            writable = False
        add(f"writable:{label}", writable, str(directory), critical=label == "data_dir")

    enabled = config.get("scenarios.enabled", []) or []
    add("scenarios.enabled", bool(enabled), f"{len(enabled)} шт.")

    webhook = config.settings.get("webhook", {}) or {}
    add(
        "webhook",
        bool(webhook.get("enabled") and webhook.get("url")),
        str(webhook.get("url") or "не настроен"),
    )
    telegram = config.secrets.get("telegram", {}) or {}
    telegram_configured = bool(telegram.get("enabled")) and bool(
        str(telegram.get("bot_token") or "").strip()
    )
    add(
        "telegram",
        telegram_configured,
        "настроен" if telegram_configured else "не настроен",
    )

    critical_failed = [c for c in checks if c["critical"] and not c["ok"]]
    if args.json:
        print(
            json.dumps(  # codeql[py/clear-text-logging-sensitive-data] — без секретов
                {"ok": not critical_failed, "checks": checks},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        style = Style(_color_enabled(args))
        print(style.bold(f"TSPU Monitor {__version__} — self-test"))
        for item in checks:
            marker = "[OK]" if item["ok"] else ("[!!]" if item["critical"] else "[ ]")
            colored = (
                style.green(marker)
                if item["ok"]
                else (style.red(marker) if item["critical"] else style.yellow(marker))
            )
            print(f"{colored} {item['check']:<22} {item['detail']}")
        print()
        if critical_failed:
            names = ", ".join(c["check"] for c in critical_failed)
            print(style.red("Критические проблемы: " + names))
        else:
            print(style.green("Критических проблем нет."))
    return EXIT_ERROR if critical_failed else EXIT_OK


def _check_web_access(host: str, token: Any, allow_insecure: bool) -> str | None:
    """Проверить безопасность привязки веб-сервера. ``None`` — всё хорошо."""
    if host in ("127.0.0.1", "localhost", "::1", "[::1]"):
        return None
    if token:
        return None
    if allow_insecure:
        return None
    return (
        f"адрес {host} не loopback, а secrets.web.token не задан — "
        "доступ без авторизации запрещён (обойти: --allow-remote-no-auth)"
    )


def cmd_web(args: argparse.Namespace, config: AppConfig) -> int:
    from .web import run_web

    host = str(args.host or config.get("web.host", "127.0.0.1"))
    port = int(args.port or config.get("web.port", 8787))
    token = config.secret("web.token")
    problem = _check_web_access(host, token, args.allow_remote_no_auth)
    if problem:
        print(f"Ошибка: {problem}", file=sys.stderr)
        return EXIT_ERROR
    return run_web(config, host=host, port=port, open_browser=args.open)


def cmd_daemon(args: argparse.Namespace, config: AppConfig) -> int:
    if args.interval:
        config.set("scheduler.check_interval_minutes", int(args.interval))
        config.save_settings()

    engine = Engine(config)
    reporter = Reporter(config)
    notifier = WebhookNotifier(config)

    web_host = str(config.get("web.host", "127.0.0.1"))
    web_port = int(config.get("web.port", 8787))
    web_enabled = bool(args.web or config.get("web.enabled"))
    if web_enabled:
        problem = _check_web_access(web_host, config.secret("web.token"), False)
        if problem:
            logger.warning("Веб-дашборд выключен: %s", problem)
            web_enabled = False

    bot = None
    if not args.no_telegram:
        from .telegram_bot import TelegramBot

        bot = TelegramBot(config, engine, reporter)
        if not bot.active:
            logger.warning(
                "Telegram-бот выключен (secrets.telegram: enabled/token/allowed_users)"
            )

    async def on_check() -> None:
        record = await engine.run()
        if not args.no_webhook and notifier.active:
            await notifier.notify(record)

    async def on_report() -> None:
        records = engine.list_runs(limit=500)
        text, _ = reporter.generate(records, engine.last_record)
        path = reporter.save(text)
        if bot is not None and bot.active:
            chat_id = bot.default_chat_id()
            if chat_id is not None:
                await bot.send_document(
                    chat_id, text, path.name, caption="Отчёт TSPU Monitor"
                )

    async def runner(stop_event: asyncio.Event) -> None:
        scheduler = Scheduler(config.settings, on_check=on_check, on_report=on_report)
        tasks = [asyncio.create_task(scheduler.run(stop_event))]
        if bot is not None and bot.active:
            tasks.append(asyncio.create_task(bot.run(stop_event)))
        web_runner = None
        if web_enabled:
            from .web import start_web

            web_runner = await start_web(config, engine, reporter, web_host, web_port)
        logger.info(
            "Демон запущен (webhook: %s, telegram: %s, web: %s)",
            "вкл" if notifier.active and not args.no_webhook else "выкл",
            "вкл" if bot is not None and bot.active else "выкл",
            "вкл" if web_enabled else "выкл",
        )
        try:
            await stop_event.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if web_runner is not None:
                await web_runner.cleanup()
            if bot is not None:
                await bot.close()
        logger.info("Демон остановлен")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop_event = asyncio.Event()

    def _stop(*_: Any) -> None:
        loop.call_soon_threadsafe(stop_event.set)

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):
                pass

    try:
        loop.run_until_complete(runner(stop_event))
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
    return EXIT_OK


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_OK
    if args.command == "version":
        print(f"TSPU Monitor {__version__}")
        return EXIT_OK

    try:
        config = _load(args, console=args.command in ("daemon", "web"))
    except Exception as exc:  # noqa: BLE001
        print(f"Ошибка загрузки конфигурации: {exc}", file=sys.stderr)
        return EXIT_ERROR

    handlers = {
        "check": cmd_check,
        "report": cmd_report,
        "status": cmd_status,
        "scenarios": cmd_scenarios,
        "config": cmd_config,
        "logs": cmd_logs,
        "self-test": cmd_self_test,
        "daemon": cmd_daemon,
        "web": cmd_web,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return EXIT_ERROR
    try:
        return handler(args, config)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        logger.exception("Команда '%s' завершилась ошибкой", args.command)
        print(f"Ошибка: {exc}", file=sys.stderr)
        return EXIT_ERROR
