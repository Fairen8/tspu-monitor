"""UDP-пробы, включая реальные пакеты WireGuard и OpenVPN."""

from __future__ import annotations

import asyncio
import os
import random
import socket
import struct
from typing import Any

from ..models import ProbeResult, Severity
from .base import BaseProbe

# ---------------------------------------------------------------------------
# Общий обмен по UDP
# ---------------------------------------------------------------------------


async def udp_exchange(
    ip: str,
    port: int,
    payload: bytes,
    timeout: float,
    probe_count: int,
) -> dict[str, Any]:
    """Отправить ``payload`` ``probe_count`` раз и собрать статистику."""
    replies = 0
    refused = False
    rtts: list[float] = []
    last_error: str | None = None
    loop = asyncio.get_running_loop()

    for _ in range(max(1, probe_count)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        try:
            await loop.sock_connect(sock, (ip, port))
            t0 = loop.time()
            await loop.sock_sendall(sock, payload)
            try:
                data = await asyncio.wait_for(loop.sock_recv(sock, 4096), timeout=timeout)
                replies += 1
                rtts.append((loop.time() - t0) * 1000.0)
                if not data:
                    continue
            except TimeoutError:
                continue
        except ConnectionRefusedError:
            refused = True
            last_error = "ICMP port unreachable"
        except OSError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                sock.close()
            except OSError:
                pass

    return {
        "replies": replies,
        "refused": refused,
        "rtt_samples_ms": rtts,
        "rtt_avg_ms": (sum(rtts) / len(rtts)) if rtts else None,
        "last_error": last_error,
    }


# ---------------------------------------------------------------------------
# Generic UDP probe
# ---------------------------------------------------------------------------


class UdpProbe(BaseProbe):
    """Произвольный UDP-зонд.

    ``control=true`` помечает заведомо рабочий зонд (например,
    DNS к 1.1.1.1). Отсутствие ответа на контрольный зонд означает
    блокировку UDP в целом.
    """

    name = "udp.probe"
    title = "UDP-зонд"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        hosts = self.config.get("hosts")
        self.hosts: list[str] = list(hosts) if hosts else (
            [self.config["host"]] if self.config.get("host") else []
        )
        ports = self.config.get("ports")
        if ports:
            self.ports: list[int] = [int(p) for p in ports]
        elif self.config.get("port"):
            self.ports = [int(self.config["port"])]
        else:
            self.ports = [53]
        payload = self.config.get("payload")
        if isinstance(payload, bytes):
            self.payload: bytes = payload
        else:
            size = int(self.config.get("payload_size", 16))
            self.payload = b"\x00" * size
        self.payload_size: int = len(self.payload)
        self.probe_count: int = max(1, int(self.config.get("probe_count", 3)))
        self.control: bool = bool(self.config.get("control", False))
        self.expected_silent: bool = bool(self.config.get("expected_silent", False))

    async def run(self) -> list[ProbeResult]:
        tasks = [
            self._probe(host, port) for host in self.hosts for port in self.ports
        ]
        return list(await asyncio.gather(*tasks))

    async def _probe(self, host: str, port: int) -> ProbeResult:
        start = self.time_ms()
        target = f"{host}:{port}"
        ip = await self.resolve(host)
        if ip is None:
            return self.make_result(
                success=False,
                target=target,
                data={"host": host, "port": port, "payload_size": self.payload_size},
                error=f"не удалось разрешить имя {host}",
                severity=Severity.WARNING,
                duration_ms=self.time_ms() - start,
            )

        stats = await udp_exchange(ip, port, self.payload, self.timeout, self.probe_count)
        replies = int(stats["replies"])

        if self.control and replies == 0:
            severity = Severity.CRITICAL
            error = "контрольный UDP-зонд не получил ответа"
        elif self.expected_silent:
            severity = Severity.INFO
            error = None
        elif replies == 0:
            severity = Severity.WARNING
            error = f"нет ответа ({stats['last_error'] or 'все попытки в таймаут'})"
        else:
            severity = Severity.INFO
            error = None

        data: dict[str, Any] = {
            "host": host,
            "ip": ip,
            "port": port,
            "payload_size": self.payload_size,
            "control": self.control,
            "expected_silent": self.expected_silent,
            "replies_received": replies,
            "probe_count": self.probe_count,
            "refused": stats["refused"],
            "rtt_avg_ms": stats["rtt_avg_ms"],
        }
        return self.make_result(
            success=replies > 0 or self.expected_silent,
            target=target,
            data=data,
            raw=f"payload={self.payload_size}B probes={self.probe_count} "
            f"replies={replies} refused={stats['refused']}",
            error=error,
            severity=severity,
            duration_ms=self.time_ms() - start,
        )


# ---------------------------------------------------------------------------
# WireGuard / AmneziaWG
# ---------------------------------------------------------------------------


def build_wireguard_handshake() -> bytes:
    """WireGuard Type-1 handshake initiation (148 байт) со случайным телом.

    MAC1 фиктивный, поэтому валидный WG-сервер молча отбросит пакет —
    это нормальное поведение, а не блокировка.
    """
    header = struct.pack("<B3sI", 1, b"\x00\x00\x00", random.getrandbits(32))
    payload = os.urandom(148 - 8)
    return header + payload


class WireGuardHandshakeProbe(BaseProbe):
    """Отправка WireGuard handshake initiation (опционально с junk-пакетами).

    ``junk_packets > 0`` имитирует AmneziaWG (S1..S4). Реальный сервер
    в норме молчит; ответ или ICMP-unreachable — сигнал аномалии.
    """

    name = "wireguard.handshake"
    title = "WireGuard handshake"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.host: str = str(self.config.get("host", ""))
        self.port: int = int(self.config.get("port", 51820))
        self.junk_packets: int = int(self.config.get("junk_packets", 0))
        self.junk_min: int = int(self.config.get("junk_min_size", 50))
        self.junk_max: int = int(self.config.get("junk_max_size", 150))
        self.probe_count: int = max(1, int(self.config.get("probe_count", 2)))

    async def run(self) -> list[ProbeResult]:
        if not self.host:
            return [self.skipped_result("wireguard.handshake: не задан host")]
        start = self.time_ms()
        target = f"{self.host}:{self.port}"
        ip = await self.resolve(self.host)
        if ip is None:
            return self.make_result(
                success=False,
                target=target,
                data={"host": self.host, "port": self.port},
                error=f"не удалось разрешить имя {self.host}",
                severity=Severity.CRITICAL,
                duration_ms=self.time_ms() - start,
            )

        replies = 0
        refused = False
        last_error: str | None = None
        loop = asyncio.get_running_loop()

        for _ in range(self.probe_count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            try:
                await loop.sock_connect(sock, (ip, self.port))
                for _junk in range(self.junk_packets):
                    size = random.randint(self.junk_min, self.junk_max)
                    await loop.sock_sendall(sock, os.urandom(size))
                await loop.sock_sendall(sock, build_wireguard_handshake())
                try:
                    data = await asyncio.wait_for(
                        loop.sock_recv(sock, 4096), timeout=self.timeout
                    )
                    if data:
                        replies += 1
                except TimeoutError:
                    continue
            except ConnectionRefusedError:
                refused = True
                last_error = "ICMP port unreachable"
            except OSError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            finally:
                try:
                    sock.close()
                except OSError:
                    pass

        if refused:
            severity = Severity.CRITICAL
            success = False
            error = "порт отвергает пакеты (ICMP unreachable)"
        elif replies > 0:
            severity = Severity.WARNING
            success = True
            error = None
        else:
            severity = Severity.INFO
            success = True
            error = last_error

        return [
            self.make_result(
                success=success,
                target=target,
                data={
                    "host": self.host,
                    "ip": ip,
                    "port": self.port,
                    "replies": replies,
                    "refused": refused,
                    "junk_packets": self.junk_packets,
                    "payload_size": 148,
                    "expected_silent": True,
                },
                raw=f"junk={self.junk_packets} handshake=148B probes={self.probe_count} "
                f"replies={replies} refused={refused}",
                error=error,
                severity=severity,
                duration_ms=self.time_ms() - start,
            )
        ]


# ---------------------------------------------------------------------------
# OpenVPN
# ---------------------------------------------------------------------------


def build_openvpn_reset() -> bytes:
    """P_CONTROL_HARD_RESET_CLIENT_V2 (opcode 0x38).

    Валидный пакет, на который обычный OpenVPN-сервер может ответить;
    TLS-crypt сервер молча отбросит его.
    """
    session_id = os.urandom(8)
    return bytes([0x38]) + session_id + b"\x00" + struct.pack("!I", 0)


class OpenVpnResetProbe(BaseProbe):
    """UDP-зонд валидным OpenVPN reset-пакетом."""

    name = "openvpn.reset"
    title = "OpenVPN reset"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.host: str = str(self.config.get("host", ""))
        self.port: int = int(self.config.get("port", 1194))
        self.probe_count: int = max(1, int(self.config.get("probe_count", 3)))

    async def run(self) -> list[ProbeResult]:
        if not self.host:
            return [self.skipped_result("openvpn.reset: не задан host")]
        start = self.time_ms()
        target = f"{self.host}:{self.port}"
        ip = await self.resolve(self.host)
        if ip is None:
            return self.make_result(
                success=False,
                target=target,
                data={"host": self.host, "port": self.port},
                error=f"не удалось разрешить имя {self.host}",
                severity=Severity.CRITICAL,
                duration_ms=self.time_ms() - start,
            )

        stats = await udp_exchange(
            ip, self.port, build_openvpn_reset(), self.timeout, self.probe_count
        )
        replies = int(stats["replies"])
        refused = bool(stats["refused"])

        if refused:
            severity = Severity.WARNING
            error = "порт отвергает пакеты (ICMP unreachable)"
        elif replies > 0:
            severity = Severity.INFO
            error = None
        else:
            severity = Severity.INFO
            error = stats["last_error"]

        return [
            self.make_result(
                success=replies > 0,
                target=target,
                data={
                    "host": self.host,
                    "ip": ip,
                    "port": self.port,
                    "replies": replies,
                    "refused": refused,
                    "payload_size": 14,
                    "expected_silent": True,
                },
                raw=f"probes={self.probe_count} replies={replies} refused={refused}",
                error=error,
                severity=severity,
                duration_ms=self.time_ms() - start,
            )
        ]
