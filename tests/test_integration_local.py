"""Локальные интеграционные тесты проб на мок-серверах.

Проверяют реальные сетевые пути проб (TCP/HTTP/UDP/QUIC/WG/TLS/Shadowsocks)
без выхода в интернет: используются loopback-серверы asyncio.
Маркер: ``integration``.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import ssl
import struct
import subprocess

import pytest

from tspu_monitor.classification import DiagnosisEngine
from tspu_monitor.models import BlockType, Severity
from tspu_monitor.probes.http import HttpProbe
from tspu_monitor.probes.quic import QuicProbe
from tspu_monitor.probes.tcp import TcpConnectProbe
from tspu_monitor.probes.tls import TlsHandshakeProbe
from tspu_monitor.probes.udp import (
    OpenVpnResetProbe,
    UdpProbe,
    WireGuardHandshakeProbe,
)
from tspu_monitor.scenarios.shadowsocks import ShadowsocksEntropyProbe

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Мок-серверы
# ---------------------------------------------------------------------------


async def start_tcp_server(handler):
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


async def start_http_server(
    body: bytes, status: str = "200 OK", extra_headers: bytes = b""
):
    async def handler(reader, writer):
        with contextlib.suppress(Exception):
            await reader.readuntil(b"\r\n\r\n")
        response = (
            f"HTTP/1.1 {status}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
        ).encode() + extra_headers + b"\r\n" + body
        writer.write(response)
        with contextlib.suppress(Exception):
            await writer.drain()
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

    return await start_tcp_server(handler)


class _UdpEcho(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.transport.sendto(data, addr)


async def start_udp_echo():
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        _UdpEcho, local_addr=("127.0.0.1", 0)
    )
    port = transport.get_extra_info("sockname")[1]
    return transport, port


def free_tcp_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


# ---------------------------------------------------------------------------
# TCP
# ---------------------------------------------------------------------------


async def test_tcp_connect_success_local():
    async def handler(reader, writer):
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

    server, port = await start_tcp_server(handler)
    try:
        probe = TcpConnectProbe(
            {
                "hosts": ["127.0.0.1"],
                "ports": [port],
                "samples": 2,
                "timeout_seconds": 2,
            }
        )
        results = await probe.run()
    finally:
        server.close()
        await server.wait_closed()

    result = results[0]
    assert result.success is True
    assert result.data["successes"] == 2
    assert result.data["fail_ratio"] == 0
    assert result.severity == Severity.INFO

    diagnosis = DiagnosisEngine().diagnose("t", "T", "127.0.0.1", results)
    assert diagnosis.level.name == "NONE"


async def test_tcp_refused_classified_as_rst_injection():
    port = free_tcp_port()
    probe = TcpConnectProbe(
        {
            "hosts": ["127.0.0.1"],
            "ports": [port],
            "samples": 2,
            "timeout_seconds": 2,
            "fast_rst_threshold_ms": 1000,  # loopback-refused всегда быстрый
        }
    )
    results = await probe.run()
    result = results[0]
    assert result.success is False
    assert result.data["refused_count"] == 2
    assert result.data["fast_rst_count"] == 2

    diagnosis = DiagnosisEngine().diagnose("t", "T", "127.0.0.1", results)
    assert BlockType.RST_INJECTION in diagnosis.types
    assert diagnosis.score >= 45


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


async def test_http_probe_success_local():
    server, port = await start_http_server(b"<html>ok</html>")
    try:
        probe = HttpProbe(
            {
                "hosts": [f"127.0.0.1:{port}"],
                "schemes": ["http"],
                "samples": 1,
                "timeout_seconds": 3,
            }
        )
        results = await probe.run()
    finally:
        server.close()
        await server.wait_closed()

    result = results[0]
    assert result.success is True
    assert result.data["status"] == 200
    assert result.data["download_bytes"] > 0
    assert result.data["plug_page_detected"] is False


async def test_http_probe_detects_plug_page():
    body = "Доступ ограничен: ресурс заблокирован по решению Роскомнадзора".encode()
    server, port = await start_http_server(body)
    try:
        probe = HttpProbe(
            {
                "hosts": [f"127.0.0.1:{port}"],
                "schemes": ["http"],
                "samples": 1,
                "timeout_seconds": 3,
            }
        )
        results = await probe.run()
    finally:
        server.close()
        await server.wait_closed()

    result = results[0]
    assert result.data["plug_page_detected"] is True
    assert result.severity == Severity.CRITICAL

    diagnosis = DiagnosisEngine().diagnose("t", "T", "127.0.0.1", results)
    assert BlockType.HTTP_PLUG in diagnosis.types


async def test_http_probe_detects_filter_header():
    server, port = await start_http_server(
        b"blocked", extra_headers=b"Server: blackhole\r\n"
    )
    try:
        probe = HttpProbe(
            {
                "hosts": [f"127.0.0.1:{port}"],
                "schemes": ["http"],
                "samples": 1,
                "timeout_seconds": 3,
            }
        )
        results = await probe.run()
    finally:
        server.close()
        await server.wait_closed()

    assert results[0].data["plug_page_detected"] is True


# ---------------------------------------------------------------------------
# UDP / QUIC / WireGuard
# ---------------------------------------------------------------------------


async def test_udp_probe_gets_replies():
    transport, port = await start_udp_echo()
    try:
        probe = UdpProbe(
            {
                "host": "127.0.0.1",
                "port": port,
                "probe_count": 2,
                "payload_size": 32,
                "timeout_seconds": 2,
            }
        )
        results = await probe.run()
    finally:
        transport.close()

    result = results[0]
    assert result.success is True
    assert result.data["replies_received"] == 2


async def test_wireguard_probe_receives_unexpected_reply():
    transport, port = await start_udp_echo()
    try:
        probe = WireGuardHandshakeProbe(
            {
                "host": "127.0.0.1",
                "port": port,
                "probe_count": 2,
                "junk_packets": 2,
                "timeout_seconds": 2,
            }
        )
        results = await probe.run()
    finally:
        transport.close()

    result = results[0]
    assert result.data["replies"] == 2
    assert result.data["refused"] is False
    assert result.severity == Severity.WARNING


async def test_quic_probe_receives_reply():
    transport, port = await start_udp_echo()
    try:
        probe = QuicProbe(
            {
                "host": "127.0.0.1",
                "port": port,
                "probe_count": 2,
                "timeout_seconds": 2,
            }
        )
        results = await probe.run()
    finally:
        transport.close()

    result = results[0]
    assert result.success is True
    assert result.data["replies"] == 2
    # echo возвращает наш же пакет с версией 0 — это не настоящий VN-ответ,
    # но проверяем, что детектор не падает и возвращает признак.
    assert result.data["version_negotiation"] in (0, 1, 2)


async def test_openvpn_reset_probe_receives_reply():
    transport, port = await start_udp_echo()
    try:
        probe = OpenVpnResetProbe(
            {
                "host": "127.0.0.1",
                "port": port,
                "probe_count": 2,
                "timeout_seconds": 2,
            }
        )
        results = await probe.run()
    finally:
        transport.close()

    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0].data["replies"] == 2


# ---------------------------------------------------------------------------
# Shadowsocks
# ---------------------------------------------------------------------------


async def test_shadowsocks_entropy_connected_without_reset():
    async def handler(reader, writer):
        with contextlib.suppress(Exception):
            await reader.read(65536)
        await asyncio.sleep(0.5)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

    server, port = await start_tcp_server(handler)
    try:
        probe = ShadowsocksEntropyProbe(
            {
                "host": "127.0.0.1",
                "port": port,
                "bytes": 128,
                "timeout_seconds": 1,
            }
        )
        results = await probe.run()
    finally:
        server.close()
        await server.wait_closed()

    result = results[0]
    assert result.data["connected"] is True
    assert result.data["reset"] is False
    assert result.success is True


async def test_shadowsocks_entropy_detects_rst():
    async def handler(reader, writer):
        with contextlib.suppress(Exception):
            await reader.read(65536)
        sock = writer.get_extra_info("socket")
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                )
        writer.close()

    server, port = await start_tcp_server(handler)
    try:
        probe = ShadowsocksEntropyProbe(
            {
                "host": "127.0.0.1",
                "port": port,
                "bytes": 128,
                "timeout_seconds": 2,
            }
        )
        results = await probe.run()
    finally:
        server.close()
        await server.wait_closed()

    result = results[0]
    assert result.data["connected"] is True
    assert result.data["reset"] is True
    assert result.severity == Severity.CRITICAL

    diagnosis = DiagnosisEngine().diagnose("t", "T", "127.0.0.1", results)
    assert BlockType.PROTOCOL_DETECT in diagnosis.types


# ---------------------------------------------------------------------------
# TLS
# ---------------------------------------------------------------------------


async def test_tls_handshake_local(tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
        ],
        check=True,
        capture_output=True,
    )

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))

    async def handler(reader, writer):
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0, ssl=context)
    port = server.sockets[0].getsockname()[1]
    try:
        probe = TlsHandshakeProbe(
            {
                "targets": [
                    {
                        "host": "127.0.0.1",
                        "port": port,
                        "sni": "localhost",
                        "role": "real",
                    }
                ],
                "timeout_seconds": 5,
            }
        )
        results = await probe.run()
    finally:
        server.close()
        await server.wait_closed()

    result = results[0]
    assert result.success is True, result.error
    assert result.data["role"] == "real"
    assert result.data["protocol"] or result.data["cipher"]
