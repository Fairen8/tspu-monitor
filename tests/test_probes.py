"""Тесты проб: парсинг, билдеры пакетов, вспомогательные функции."""

from __future__ import annotations

import sys

import pytest

from tspu_monitor.probes import (
    BUILTIN_PROBES,
    PROBE_REGISTRY,
    PingProbe,
    classify_tls_error,
    detect_filter_header,
    detect_plug_page,
)
from tspu_monitor.probes.base import BaseProbe
from tspu_monitor.probes.icmp import PathMtuProbe
from tspu_monitor.probes.quic import build_version_negotiation_trigger, is_version_negotiation
from tspu_monitor.probes.udp import build_openvpn_reset, build_wireguard_handshake
from tspu_monitor.utils import build_dns_query, is_private_ip

# ---------------------------------------------------------------------------
# Реестр
# ---------------------------------------------------------------------------


def test_probe_registry_complete():
    names = {p.name for p in BUILTIN_PROBES}
    expected = {
        "icmp.ping",
        "icmp.trace",
        "icmp.mtu",
        "tcp.connect",
        "tcp.scan",
        "udp.probe",
        "wireguard.handshake",
        "openvpn.reset",
        "dns.resolve",
        "dns.doh",
        "tls.handshake",
        "http.get",
        "quic.initial",
        "raw.ttl",
    }
    assert expected.issubset(names)
    assert PROBE_REGISTRY["icmp.ping"] is PingProbe


def test_all_probes_instantiate():
    for probe_cls in BUILTIN_PROBES:
        probe = probe_cls({})
        assert isinstance(probe, BaseProbe)
        assert probe.timeout == 10


# ---------------------------------------------------------------------------
# TLS-классификатор ошибок
# ---------------------------------------------------------------------------


def test_classify_tls_error():
    assert classify_tls_error("Connection reset by peer") == "reset"
    assert classify_tls_error("tlsv1 alert handshake failure") == "alert"
    assert classify_tls_error("no peer certificate available") == "handshake"
    assert classify_tls_error("certificate verify failed") == "cert"
    assert classify_tls_error("", rc=-1) == "timeout"
    assert classify_tls_error("Cipher: TLS_AES_128_GCM_SHA256") is None


# ---------------------------------------------------------------------------
# HTTP-подписи
# ---------------------------------------------------------------------------


def test_detect_plug_page():
    signature = detect_plug_page("Доступ ограничен: ресурс заблокирован")
    assert signature is not None
    assert signature.startswith("body:")
    assert detect_plug_page("<html>Привет</html>") is None


def test_detect_filter_header():
    assert detect_filter_header({"Server": "blackhole"}) is not None
    assert detect_filter_header({"X-RKN": "1"}) is not None
    assert detect_filter_header({"Server": "nginx"}) is None


# ---------------------------------------------------------------------------
# Билдеры пакетов
# ---------------------------------------------------------------------------


def test_build_dns_query():
    query = build_dns_query("ya.ru")
    assert query[2:4] == b"\x01\x00"  # флаг recursion desired
    assert b"\x02ya\x02ru\x00" in query
    assert query.endswith(b"\x00\x01\x00\x01")


def test_build_wireguard_handshake():
    packet = build_wireguard_handshake()
    assert len(packet) == 148
    assert packet[0] == 1
    assert packet[1:4] == b"\x00\x00\x00"


def test_build_openvpn_reset():
    packet = build_openvpn_reset()
    assert packet[0] == 0x38
    assert len(packet) == 14


def test_quic_version_negotiation_trigger():
    packet = build_version_negotiation_trigger()
    assert len(packet) >= 1200
    assert packet[0] & 0x80  # long header
    assert packet[1:5] == b"\x00\x00\x00\x00"


def test_is_version_negotiation():
    vn = bytes([0x80]) + b"\x00\x00\x00\x00" + bytes([8]) + b"a" * 8 + bytes([8]) + b"b" * 8
    assert is_version_negotiation(vn)
    assert not is_version_negotiation(b"\x01\x02\x03")
    assert not is_version_negotiation(b"\x40" + b"\x00" * 30)


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------


def test_is_private_ip():
    assert is_private_ip("10.0.0.1")
    assert is_private_ip("192.168.1.1")
    assert is_private_ip("172.16.5.5")
    assert is_private_ip("127.0.0.1")
    assert not is_private_ip("8.8.8.8")
    assert not is_private_ip("203.0.113.1")
    assert not is_private_ip("not-an-ip")


# ---------------------------------------------------------------------------
# BaseProbe helpers
# ---------------------------------------------------------------------------


async def test_run_cmd_success():
    probe = PingProbe({})
    rc, stdout, stderr = await probe.run_cmd(
        [sys.executable, "-c", "print('hello')"], timeout=10
    )
    assert rc == 0
    assert "hello" in stdout


async def test_run_cmd_missing_binary():
    probe = PingProbe({})
    rc, stdout, stderr = await probe.run_cmd(
        ["definitely-not-a-real-binary-xyz"], timeout=5
    )
    assert rc == -2
    assert "binary not found" in stderr


async def test_resolve_ip_address():
    probe = PingProbe({})
    assert await probe.resolve("127.0.0.1") == "127.0.0.1"


# ---------------------------------------------------------------------------
# Парсинг вывода ping
# ---------------------------------------------------------------------------


async def test_ping_parses_output(monkeypatch):
    probe = PingProbe({"hosts": ["1.2.3.4"], "count": 2, "timeout_seconds": 2})
    output = (
        "PING 1.2.3.4 (1.2.3.4) 56(84) bytes of data.\n"
        "64 bytes from 1.2.3.4: icmp_seq=1 ttl=57 time=1.10 ms\n"
        "64 bytes from 1.2.3.4: icmp_seq=2 ttl=57 time=3.30 ms\n"
        "\n--- 1.2.3.4 ping statistics ---\n"
        "2 packets transmitted, 2 received, 0% packet loss, time 1001ms\n"
        "rtt min/avg/max/mdev = 1.100/2.200/3.300/0.500 ms\n"
    )

    async def fake_run_cmd(argv, timeout=None, stdin=None, env=None):
        return 0, output, ""

    monkeypatch.setattr(probe, "run_cmd", fake_run_cmd)
    result = await probe._ping_one("1.2.3.4")
    assert result.success is True
    assert result.data["packet_loss_percent"] == 0
    assert result.data["rtt_avg_ms"] == pytest.approx(2.2)
    assert result.data["rtt_min_ms"] == pytest.approx(1.1)


async def test_ping_handles_100_percent_loss(monkeypatch):
    probe = PingProbe({"hosts": ["1.2.3.4"], "count": 2, "timeout_seconds": 2})
    output = "2 packets transmitted, 0 received, 100% packet loss, time 1000ms\n"

    async def fake_run_cmd(argv, timeout=None, stdin=None, env=None):
        return 1, output, ""

    monkeypatch.setattr(probe, "run_cmd", fake_run_cmd)
    result = await probe._ping_one("1.2.3.4")
    assert result.success is False
    assert result.data["packet_loss_percent"] == 100


async def test_path_mtu_probe_requires_host():
    probe = PathMtuProbe({})
    results = await probe.run()
    assert results[0].skipped is True
