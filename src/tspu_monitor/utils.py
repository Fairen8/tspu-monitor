"""Небольшие утилиты, общие для проб, движка и отчётов."""

from __future__ import annotations

import json
import os
import random
import socket
import struct
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def ensure_dir(path: os.PathLike | str) -> Path:
    """Создать каталог (рекурсивно). При ошибке вернуть ``Path`` как есть."""
    p = Path(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def parse_iso(value: str) -> datetime:
    """Разобрать ISO-8601 (с ``Z`` или без). При ошибке — текущее UTC."""
    if not value:
        return datetime.now(UTC)
    text = value.strip().rstrip("Z")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def fmt_dt(dt: datetime, tz_local: bool = True) -> str:
    """Форматировать дату в ``ДД.ММ.ГГГГ ЧЧ:ММ``."""
    if tz_local:
        dt = dt.astimezone()
    return dt.strftime("%d.%m.%Y %H:%M")


def fmt_ms(ms: float | None) -> str:
    if ms is None:
        return "-"
    if ms < 1000:
        return f"{ms:.1f} мс"
    return f"{ms / 1000:.2f} с"


def truncate(text: str | None, limit: int = 300) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def is_private_ip(ip: str) -> bool:
    """RFC1918 / loopback / link-local."""
    if ip in ("0.0.0.0", "127.0.0.1"):
        return True
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    if a == 10 or a == 127:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 192 and b == 168:
        return True
    return a == 169 and b == 254


def build_dns_query(domain: str, qtype: int = 1) -> bytes:
    """Собрать минимальный DNS-запрос (для UDP-зондов)."""
    transaction_id = random.getrandbits(16)
    flags = 0x0100  # стандартный запрос, recursion desired
    header = struct.pack(">HHHHHH", transaction_id, flags, 1, 0, 0, 0)
    question = b""
    for label in domain.rstrip(".").split("."):
        encoded = label.encode("idna") if label else b""
        question += bytes([len(encoded)]) + encoded
    question += b"\x00" + struct.pack(">HH", qtype, 1)
    return header + question


def write_json_atomic(path: os.PathLike | str, data: dict[str, Any]) -> None:
    """Атомарно записать JSON (через временный файл)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_json(path: os.PathLike | str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def resolve_ipv4(host: str) -> str | None:
    """Разрешить имя в IPv4 без блокировки event loop (для sync-кода)."""
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        return None
    if not infos:
        return None
    return infos[0][4][0]
