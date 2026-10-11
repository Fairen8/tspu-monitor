"""Базовый класс проб.

Проба — минимальная единица измерения (ICMP ping, TCP connect, TLS
handshake и т.д.). Проба получает словарь конфигурации и возвращает
список :class:`~tspu_monitor.models.ProbeResult`.
"""

from __future__ import annotations

import abc
import asyncio
import os
import socket
import time
from collections.abc import Sequence
from typing import Any

from ..logging_setup import get_logger
from ..models import ProbeResult, Severity


def _decode_output(data: bytes) -> str:
    """Декодировать вывод команды: UTF-8 → cp866/cp1251 (русская Windows)."""
    if not data:
        return ""
    for encoding in ("utf-8", "cp866", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


class BaseProbe(abc.ABC):
    """Абстрактная сетевая проба."""

    #: Идентификатор пробы в формате ``<категория>.<имя>``.
    name: str = "base"
    #: Человекочитаемое название.
    title: str = "Проба"
    #: Требуются ли raw-сокеты (root/CAP_NET_RAW).
    requires_root: bool = False

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self.timeout: float = float(self.config.get("timeout_seconds", 10))
        self.logger = get_logger("tspu.probes")

    @abc.abstractmethod
    async def run(self) -> list[ProbeResult]:
        """Выполнить пробу и вернуть результаты (обычно по одному на цель)."""

    # ------------------------------------------------------------------
    # Фабрика результатов
    # ------------------------------------------------------------------
    def make_result(
        self,
        success: bool,
        target: str,
        data: dict[str, Any] | None = None,
        raw: str = "",
        error: str | None = None,
        severity: Severity = Severity.INFO,
        duration_ms: float = 0.0,
        name: str | None = None,
    ) -> ProbeResult:
        return ProbeResult(
            probe=name or self.name,
            target=target,
            success=success,
            severity=severity,
            duration_ms=duration_ms,
            data=data or {},
            raw=raw,
            error=error,
        )

    def skipped_result(self, reason: str) -> ProbeResult:
        """Результат-заглушка: проба не выполнялась (нет прав/бинарника)."""
        return self.make_result(
            success=False,
            target="n/a",
            data={"skipped": True, "skip_reason": reason},
            error=reason,
            severity=Severity.INFO,
        )

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------
    async def run_cmd(
        self,
        argv: Sequence[str],
        timeout: float | None = None,
        stdin: bytes | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        """Запустить внешнюю команду.

        Возвращает ``(rc, stdout, stderr)``. Коды: ``-1`` — таймаут,
        ``-2`` — бинарник не найден.
        """
        timeout = timeout if timeout is not None else self.timeout
        full_env = dict(os.environ)
        full_env.setdefault("LC_ALL", "C")
        if env:
            full_env.update(env)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env,
            )
        except FileNotFoundError:
            return -2, "", f"binary not found: {argv[0]}"
        except OSError as exc:
            return -2, "", f"{type(exc).__name__}: {exc}"

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=stdin), timeout=timeout
            )
        except TimeoutError:
            with_suppress = getattr(proc, "kill", None)
            if with_suppress:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            try:
                await proc.communicate()
            except Exception:  # noqa: BLE001
                pass
            return -1, "", f"timeout after {timeout}s"

        return (
            proc.returncode if proc.returncode is not None else -1,
            _decode_output(stdout_b),
            _decode_output(stderr_b),
        )

    async def resolve(self, host: str) -> str | None:
        """Разрешить имя в IPv4 (асинхронно). ``None`` при ошибке."""
        loop = asyncio.get_running_loop()
        try:
            infos = await asyncio.wait_for(
                loop.getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_STREAM),
                timeout=min(self.timeout, 10),
            )
        except (TimeoutError, socket.gaierror, OSError):
            return None
        return infos[0][4][0] if infos else None

    @staticmethod
    def time_ms() -> float:
        return time.perf_counter() * 1000.0
