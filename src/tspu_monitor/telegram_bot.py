"""Минимальный Telegram-бот для управления монитором.

Реализован напрямую через Bot API (long polling, aiohttp) — без тяжёлых
зависимостей. Это даёт полный контроль над маршрутизацией запросов:

* ``telegram.proxy`` — HTTP(S) или SOCKS5-прокси
  (для SOCKS5 нужен пакет ``aiohttp-socks``, extra ``[socks]``);
* ``telegram.api_base`` — альтернативный адрес Bot API (зеркало).

ВАЖНО: прокси/zapret применяется только к Telegram API. Сетевые пробы
всегда идут напрямую (см. DOCS.md → «Telegram за блокировкой»).
"""

from __future__ import annotations

import asyncio
from typing import Any

from . import __version__
from .config import AppConfig, coerce_config_value
from .engine import Engine
from .logging_setup import get_logger, read_last_lines
from .reporter import Reporter
from .utils import truncate


def redact_secret(text: str, secret: str | None) -> str:
    """Убрать секрет (например, токен бота) из текста перед логированием."""
    if secret and secret in text:
        return text.replace(secret, "***")
    return text

HELP_TEXT = (
    "TSPU Monitor — управление\n\n"
    "/status — статус и последний запуск\n"
    "/check [сценарий] — запустить проверки\n"
    "/report — отчёт (TXT-файлом)\n"
    "/scenarios — список сценариев\n"
    "/logs [N] — последние строки журнала\n"
    "/config show | get K | set K V — конфигурация\n"
    "/version — версия\n"
)


class TelegramBot:
    """Long-polling бот с whitelist и поддержкой прокси."""

    def __init__(self, config: AppConfig, engine: Engine, reporter: Reporter) -> None:
        self.config = config
        self.engine = engine
        self.reporter = reporter
        tg = config.secrets.get("telegram", {}) or {}
        self.token: str = str(tg.get("bot_token", "") or "")
        self.enabled: bool = bool(tg.get("enabled", False)) and bool(self.token)
        self.allowed_users = {
            int(user)
            for user in (tg.get("allowed_users", []) or [])
            if str(user).lstrip("-").isdigit()
        }
        self.report_chat_id = tg.get("report_chat_id")
        self.proxy: str | None = tg.get("proxy")
        self.api_base: str = str(
            tg.get("api_base") or "https://api.telegram.org"
        ).rstrip("/")
        self.logger = get_logger("tspu.telegram")
        self._session: Any = None
        self._offset = 0

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.allowed_users)

    def default_chat_id(self) -> int | None:
        if self.report_chat_id:
            return int(self.report_chat_id)
        if self.allowed_users:
            return sorted(self.allowed_users)[0]
        return None

    # ------------------------------------------------------------------
    async def _new_session(self) -> Any:
        import aiohttp

        if self.proxy and self.proxy.startswith("socks"):
            try:
                from aiohttp_socks import ProxyConnector  # type: ignore

                connector = ProxyConnector.from_url(self.proxy)
                return aiohttp.ClientSession(connector=connector)
            except ImportError as exc:
                raise RuntimeError(
                    "Для SOCKS5-прокси установите пакет: pip install aiohttp-socks"
                ) from exc
        return aiohttp.ClientSession(proxy=self.proxy)

    async def _api(self, method: str, **params: Any) -> dict[str, Any]:
        import aiohttp

        url = f"{self.api_base}/bot{self.token}/{method}"
        timeout = aiohttp.ClientTimeout(total=40, connect=10)
        if self._session is None or self._session.closed:
            self._session = await self._new_session()
        try:
            async with self._session.post(url, json=params, timeout=timeout) as resp:
                return await resp.json()
        except Exception as exc:  # noqa: BLE001
            message = redact_secret(str(exc), self.token)
            self.logger.warning("Telegram API %s: %s", method, message)
            return {"ok": False, "description": message}

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    # -- отправка ---------------------------------------------------------
    async def send_message(self, chat_id: int, text: str) -> bool:
        result = await self._api("sendMessage", chat_id=chat_id, text=text)
        return bool(result.get("ok"))

    async def send_document(
        self, chat_id: int, content: str, filename: str, caption: str = ""
    ) -> bool:
        import aiohttp

        url = f"{self.api_base}/bot{self.token}/sendDocument"
        timeout = aiohttp.ClientTimeout(total=60, connect=10)
        if self._session is None or self._session.closed:
            self._session = await self._new_session()
        form = aiohttp.FormData()
        form.add_field("chat_id", str(chat_id))
        if caption:
            form.add_field("caption", caption)
        form.add_field(
            "document", content.encode("utf-8"), filename=filename,
            content_type="text/plain; charset=utf-8",
        )
        try:
            async with self._session.post(url, data=form, timeout=timeout) as resp:
                data = await resp.json()
                return bool(data.get("ok"))
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("sendDocument: %s", redact_secret(str(exc), self.token))
            return False

    # ------------------------------------------------------------------
    async def run(self, stop_event: asyncio.Event) -> None:
        if not self.active:
            self.logger.warning("Telegram-бот не запущен: отключён или нет allowed_users")
            return
        me = await self._api("getMe")
        if not me.get("ok"):
            self.logger.error(
                "Telegram getMe не удался (%s). Проверьте токен/прокси/zapret.",
                me.get("description"),
            )
            return
        self.logger.info(
            "Telegram-бот @%s запущен (прокси: %s)",
            (me.get("result") or {}).get("username"),
            "включён" if self.proxy else "не задан",
        )
        try:
            while not stop_event.is_set():
                updates = await self._api(
                    "getUpdates",
                    offset=self._offset,
                    timeout=25,
                    allowed_updates=["message"],
                )
                if not updates.get("ok"):
                    await asyncio.sleep(5)
                    continue
                for update in updates.get("result", []) or []:
                    self._offset = max(self._offset, int(update["update_id"]) + 1)
                    await self._handle_update(update)
        finally:
            await self.close()
            self.logger.info("Telegram-бот остановлен")

    # -- обработка --------------------------------------------------------
    async def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        text = str(message.get("text") or "").strip()
        chat = message.get("chat") or {}
        user = message.get("from") or {}
        chat_id = chat.get("id")
        user_id = user.get("id")
        if not text or chat_id is None or user_id is None:
            return
        if int(user_id) not in self.allowed_users:
            self.logger.warning(
                "Неавторизованный доступ: user_id=%s username=%s text=%s",
                user_id,
                user.get("username"),
                truncate(text, 40),
            )
            await self.send_message(chat_id, "Доступ запрещён")
            return
        try:
            await self._dispatch(chat_id, text)
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("Ошибка обработки команды: %s", exc)
            await self.send_message(chat_id, f"Ошибка: {exc}")

    async def _dispatch(self, chat_id: int, text: str) -> None:
        parts = text.split()
        command = parts[0].lower().split("@")[0]
        args = parts[1:]

        if command in ("/start", "/help"):
            await self.send_message(
                chat_id, HELP_TEXT + f"\nВерсия: {__version__}"
            )
        elif command == "/version":
            await self.send_message(chat_id, f"TSPU Monitor {__version__}")
        elif command == "/status":
            snapshot = self.engine.status_snapshot()
            last = snapshot.get("last_run") or {}
            lines = [
                f"TSPU Monitor {__version__}",
                f"Активных сценариев: {len(snapshot['enabled'])}",
                "  " + ", ".join(snapshot["enabled"]),
            ]
            if last:
                level = str(last.get("max_level", "none"))
                lines.append(
                    f"Последний запуск: {last.get('run_id')} "
                    f"({last.get('finished')}) — {level} "
                    f"({last.get('max_score', 0)}/100)"
                )
            else:
                lines.append("Запусков ещё не было")
            await self.send_message(chat_id, "\n".join(lines))
        elif command == "/check":
            profiles = args or None
            record = await self.engine.run(profiles=profiles)
            await self.send_message(chat_id, self._format_record(record))
        elif command == "/report":
            records = self.engine.list_runs(limit=200)
            content, _ = self.reporter.generate(records, self.engine.last_record)
            path = self.reporter.save(content)
            await self.send_document(
                chat_id, content, path.name, caption="Отчёт TSPU Monitor"
            )
        elif command == "/scenarios":
            lines = []
            for item in self.engine.manager.list_all():
                mark = "[x]" if item["enabled"] else "[ ]"
                lines.append(f"{mark} {item['name']} — {item['title']}")
            await self.send_message(chat_id, "\n".join(lines) or "Нет сценариев")
        elif command == "/logs":
            lines = int(args[0]) if args and args[0].isdigit() else 50
            lines = max(1, min(lines, 2000))
            content = read_last_lines(self.config.log_dir / "main.log", n=lines)
            await self.send_message(
                chat_id, truncate("\n".join(content), 3900) or "Журнал пуст"
            )
        elif command == "/config":
            await self._dispatch_config(chat_id, args)
        else:
            await self.send_message(chat_id, HELP_TEXT)

    async def _dispatch_config(self, chat_id: int, args: list[str]) -> None:
        import yaml

        if not args or args[0] == "show":
            text = yaml.safe_dump(
                self.config.settings, allow_unicode=True, sort_keys=False
            )
            await self.send_message(chat_id, truncate(text, 3900))
            return
        if args[0] == "get" and len(args) >= 2:
            from .config import get_dotted

            value = get_dotted(self.config.settings, args[1], "<нет>")
            await self.send_message(chat_id, f"{args[1]} = {value}")
            return
        if args[0] == "set" and len(args) >= 3:
            key, raw = args[1], args[2]
            try:
                value = coerce_config_value(key, raw)
            except KeyError:
                await self.send_message(
                    chat_id, f"Ключ '{key}' нельзя менять через бота"
                )
                return
            self.config.set(key, value)
            self.config.save_settings()
            await self.send_message(chat_id, f"OK: {key} = {value}")
            return
        await self.send_message(chat_id, "Использование: /config show|get K|set K V")

    @staticmethod
    def _format_record(record: Any) -> str:
        if not record.analyses:
            return "Нет активных сценариев."
        lines = [
            f"{record.max_level.icon} Уровень: {record.max_level.title} "
            f"({record.max_score}/100)"
        ]
        for analysis in record.analyses:
            lines.append(
                f"{analysis.level.icon} {analysis.title}: {analysis.level.title} "
                f"({analysis.score}/100)"
            )
            for cause in analysis.causes[:3]:
                lines.append(f"   • {cause}")
        return "\n".join(lines)
