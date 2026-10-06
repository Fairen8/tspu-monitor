"""Реестр проб."""

from __future__ import annotations

from typing import Any

from .base import BaseProbe
from .dns import DnsResolveProbe, DohProbe
from .http import HttpProbe, detect_filter_header, detect_plug_page
from .icmp import PathMtuProbe, PingProbe, TraceProbe
from .quic import QuicProbe
from .raw import RawTtlProbe
from .tcp import PortScanProbe, TcpConnectProbe
from .tls import TlsHandshakeProbe, classify_tls_error
from .udp import OpenVpnResetProbe, UdpProbe, WireGuardHandshakeProbe

BUILTIN_PROBES: list[type[BaseProbe]] = [
    PingProbe,
    TraceProbe,
    PathMtuProbe,
    TcpConnectProbe,
    PortScanProbe,
    UdpProbe,
    WireGuardHandshakeProbe,
    OpenVpnResetProbe,
    DnsResolveProbe,
    DohProbe,
    TlsHandshakeProbe,
    HttpProbe,
    QuicProbe,
    RawTtlProbe,
]

PROBE_REGISTRY: dict[str, type[BaseProbe]] = {p.name: p for p in BUILTIN_PROBES}


def get_probe(name: str) -> type[BaseProbe] | None:
    return PROBE_REGISTRY.get(name)


def list_probes() -> list[dict[str, Any]]:
    return [
        {"name": p.name, "title": p.title, "requires_root": p.requires_root}
        for p in BUILTIN_PROBES
    ]


__all__ = [
    "BaseProbe",
    "PingProbe",
    "TraceProbe",
    "PathMtuProbe",
    "TcpConnectProbe",
    "PortScanProbe",
    "UdpProbe",
    "WireGuardHandshakeProbe",
    "OpenVpnResetProbe",
    "DnsResolveProbe",
    "DohProbe",
    "TlsHandshakeProbe",
    "HttpProbe",
    "QuicProbe",
    "RawTtlProbe",
    "PROBE_REGISTRY",
    "BUILTIN_PROBES",
    "get_probe",
    "list_probes",
    "detect_plug_page",
    "detect_filter_header",
    "classify_tls_error",
]
