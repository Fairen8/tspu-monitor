"""QUIC-проба.

Полноценный QUIC Initial требует AEAD-защиты (HKDF + AES-GCM). Вместо
этого используется легальный приём: пакет с **неподдерживаемой версией**
размером ≥ 1200 байт. По RFC 9000 здоровый QUIC-сервер обязан ответить
Version Negotiation. Если сервер молчит, а TCP/443 работает — QUIC
фильтруется.
"""

from __future__ import annotations

import asyncio
import os
import random
import socket
import struct
from typing import Any

from ..models import ProbeResult, Severity
from .base import BaseProbe

VN_MIN_SIZE = 1200


def build_version_negotiation_trigger() -> bytes:
    """Собрать QUIC-пакет с версией 0 (триггер Version Negotiation)."""
    first_byte = 0xC0 | random.randint(0, 0x3F)
    version = struct.pack(">I", 0x00000000)
    dcid = os.urandom(8)
    scid = os.urandom(8)
    packet = bytes([first_byte]) + version + bytes([8]) + dcid + bytes([8]) + scid
    if len(packet) < VN_MIN_SIZE:
        packet += os.urandom(VN_MIN_SIZE - len(packet))
    return packet


def is_version_negotiation(data: bytes) -> bool:
    """Проверить, что ответ — QUIC Version Negotiation."""
    if len(data) < 1 + 4 + 1 + 8 + 1 + 8:
        return False
    if not data[0] & 0x80:  # long header
        return False
    return data[1:5] == b"\x00\x00\x00\x00"


class QuicProbe(BaseProbe):
    """Проверка доступности QUIC (HTTP/3) по UDP/443."""

    name = "quic.initial"
    title = "QUIC (HTTP/3)"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        hosts = self.config.get("hosts")
        self.hosts: list[str] = list(hosts) if hosts else (
            [self.config["host"]] if self.config.get("host") else []
        )
        self.port: int = int(self.config.get("port", 443))
        self.probe_count: int = max(1, int(self.config.get("probe_count", 3)))
        self.control: bool = bool(self.config.get("control", False))
        self.control_hosts = set(self.config.get("control_hosts", []) or [])

    async def run(self) -> list[ProbeResult]:
        return list(
            await asyncio.gather(*(self._probe(host) for host in self.hosts))
        )

    async def _probe(self, host: str) -> ProbeResult:
        start = self.time_ms()
        target = f"{host}:{self.port}"
        ip = await self.resolve(host)
        if ip is None:
            return self.make_result(
                success=False,
                target=target,
                data={"host": host, "port": self.port},
                error=f"не удалось разрешить имя {host}",
                severity=Severity.WARNING,
                duration_ms=self.time_ms() - start,
            )

        payload = build_version_negotiation_trigger()
        replies = 0
        vn_replies = 0
        rtts: list[float] = []
        last_error: str | None = None
        loop = asyncio.get_running_loop()

        for _ in range(self.probe_count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            try:
                await loop.sock_connect(sock, (ip, self.port))
                t0 = self.time_ms()
                await loop.sock_sendall(sock, payload)
                try:
                    data = await asyncio.wait_for(
                        loop.sock_recv(sock, 4096), timeout=self.timeout
                    )
                    replies += 1
                    rtts.append(self.time_ms() - t0)
                    if is_version_negotiation(data):
                        vn_replies += 1
                except TimeoutError:
                    continue
            except ConnectionRefusedError:
                last_error = "ICMP port unreachable"
            except OSError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            finally:
                try:
                    sock.close()
                except OSError:
                    pass

        is_control = self.control or host in self.control_hosts
        if replies > 0:
            severity = Severity.INFO
            error = None
        elif is_control:
            severity = Severity.WARNING
            error = f"контрольный QUIC-хост не ответил ({last_error or 'timeout'})"
        else:
            severity = Severity.WARNING
            error = f"QUIC-сервер не ответил ({last_error or 'timeout'})"

        return self.make_result(
            success=replies > 0,
            target=target,
            data={
                "host": host,
                "ip": ip,
                "port": self.port,
                "replies": replies,
                "version_negotiation": vn_replies,
                "payload_size": len(payload),
                "control": is_control,
                "rtt_samples_ms": rtts,
            },
            raw=f"probes={self.probe_count} replies={replies} vn={vn_replies}",
            error=error,
            severity=severity,
            duration_ms=self.time_ms() - start,
        )
