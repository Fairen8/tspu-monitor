"""Логирование TSPU Monitor.

Пишем три ротируемых журнала:

* ``main.log`` — общий журнал работы;
* ``probes.log`` — каждая проба и её результат;
* ``telegram.log`` — события Telegram-бота.

Каталог журналов берётся из аргумента, переменной окружения
``TSPU_LOG_DIR`` или общего конфига. Если каталог недоступен для записи,
используется ``~/.tspu-monitor/logs``.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LOG_DIR = "/var/log/tspu-monitor"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

_CONFIGURED = False
_CURRENT_LOG_DIR: Path | None = None


def _resolve_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    return getattr(logging, str(level).upper(), logging.INFO)


def _fallback_log_dir() -> Path:
    return Path.home() / ".tspu-monitor" / "logs"


def _writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.touch()
        probe.unlink()
        return True
    except (OSError, PermissionError):
        return False


def _rotating_handler(path: Path, level: int) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        str(path), maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    return handler


def setup_logging(
    level: str | int = "INFO",
    log_dir: str | Path | None = None,
    console: bool = False,
) -> Path:
    """Настроить журналирование. Возвращает фактический каталог журналов."""
    global _CONFIGURED, _CURRENT_LOG_DIR

    resolved_level = _resolve_level(level)
    requested = Path(
        log_dir or os.environ.get("TSPU_LOG_DIR") or DEFAULT_LOG_DIR
    ).expanduser()
    path = requested if _writable_dir(requested) else _fallback_log_dir()
    _writable_dir(path)
    _CURRENT_LOG_DIR = path

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(resolved_level)

    root.addHandler(_rotating_handler(path / "main.log", resolved_level))
    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(resolved_level)
        stream.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        root.addHandler(stream)

    for name, filename in (("tspu.probes", "probes.log"), ("tspu.telegram", "telegram.log")):
        child = logging.getLogger(name)
        for handler in list(child.handlers):
            child.removeHandler(handler)
        child.setLevel(resolved_level)
        child.addHandler(_rotating_handler(path / filename, resolved_level))
        child.propagate = True

    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    _CONFIGURED = True
    return path


def get_logger(name: str = "tspu") -> logging.Logger:
    """Вернуть логгер, при необходимости настроив журналирование."""
    if not _CONFIGURED:
        setup_logging()
    return logging.getLogger(name)


def get_log_dir() -> Path:
    """Каталог, в который сейчас пишутся журналы."""
    if _CURRENT_LOG_DIR is not None:
        return _CURRENT_LOG_DIR
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            return Path(handler.baseFilename).parent
    return _fallback_log_dir()


def read_last_lines(file_path: str | os.PathLike, n: int = 100) -> list[str]:
    """Прочитать последние ``n`` строк файла (эффективно, с конца)."""
    path = Path(file_path)
    if not path.exists():
        return []
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            block = 8192
            data = b""
            while size > 0 and data.count(b"\n") <= n:
                read_size = min(block, size)
                size -= read_size
                fh.seek(size)
                data = fh.read(read_size) + data
        return data.decode("utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []
