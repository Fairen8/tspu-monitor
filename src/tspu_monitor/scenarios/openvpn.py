"""Сценарий OPENVPN: reset-пакет, TCP-fallback, TLS-маскировка."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..classification import Finding
from ..models import BlockType, ProbeResult
from ..probes.base import BaseProbe
from ..probes.tcp import TcpConnectProbe
from ..probes.tls import TlsHandshakeProbe
from ..probes.udp import OpenVpnResetProbe, UdpProbe
from ..utils import build_dns_query
from .base import BaseScenario


class OpenVpnScenario(BaseScenario):
    name = "openvpn"
    title = "OPENVPN"
    description = "OpenVPN UDP-reset, TCP-порты, TLS-crypt cover"
    version = "2.0.0"

    def get_default_config(self) -> dict[str, Any]:
        return {
            "port": 1194,
            "tcp_ports": [443, 1194, 8443],
            "tls_cover_sni": "www.google.com",
        }

    def _server(self) -> str:
        targets = self.secrets.get("targets", {}) or {}
        return str(targets.get("openvpn_server") or self.config.get("server") or "")

    def _port(self) -> int:
        targets = self.secrets.get("targets", {}) or {}
        return int(targets.get("openvpn_port") or self.config.get("port", 1194))

    def target(self) -> str:
        server = self._server()
        return f"{server}:{self._port()}" if server else ""

    def get_probes(self) -> list[BaseProbe]:
        server = self._server()
        port = self._port()
        timeout = float(self.config.get("timeout_seconds", 10))
        return [
            OpenVpnResetProbe(
                {"host": server, "port": port, "timeout_seconds": timeout}
            ),
            TcpConnectProbe(
                {
                    "hosts": [server],
                    "ports": list(self.config.get("tcp_ports", [443, 1194, 8443])),
                    "samples": 1,
                    "timeout_seconds": timeout,
                }
            ),
            TlsHandshakeProbe(
                {
                    "targets": [
                        {
                            "host": server,
                            "port": 443,
                            "sni": self.config.get("tls_cover_sni", "www.google.com"),
                            "role": "cover",
                        }
                    ],
                    "timeout_seconds": timeout,
                }
            ),
            UdpProbe(
                {
                    "host": "1.1.1.1",
                    "port": 53,
                    "payload": build_dns_query("ya.ru"),
                    "control": True,
                    "probe_count": 2,
                    "timeout_seconds": timeout,
                }
            ),
        ]

    def extra_findings(self, results: Sequence[ProbeResult]) -> list[Finding]:
        tls_cover = next(
            (
                r
                for r in results
                if r.probe == "tls.handshake" and r.data.get("role") == "cover"
            ),
            None,
        )
        tcp_443_ok = any(
            r.probe == "tcp.connect" and r.success and r.data.get("port") == 443
            for r in results
        )
        if tls_cover is not None and not tls_cover.success and tcp_443_ok:
            return [
                Finding(
                    BlockType.MISCONFIG,
                    5,
                    "TCP/443 доступен, но TLS-handshake не завершается: "
                    "TLS-crypt/маскировка не настроены",
                    cause="Отсутствует TLS-маскировка OpenVPN (TLS-crypt-v2/stunnel)",
                    recommendation=(
                        "Настройте TLS-crypt-v2 (OpenVPN 2.5+) или stunnel, "
                        "чтобы трафик выглядел как обычный TLS"
                    ),
                )
            ]
        return []
