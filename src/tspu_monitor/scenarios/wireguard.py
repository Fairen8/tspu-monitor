"""Сценарий WIREGUARD: reachability, handshake, fallback-порты."""

from __future__ import annotations

from typing import Any

from ..probes.base import BaseProbe
from ..probes.icmp import PingProbe
from ..probes.tcp import TcpConnectProbe
from ..probes.udp import UdpProbe, WireGuardHandshakeProbe
from ..utils import build_dns_query
from .base import BaseScenario


class WireGuardScenario(BaseScenario):
    name = "wireguard"
    title = "WIREGUARD"
    description = "WireGuard handshake, доступность портов, UDP-контроль"
    version = "2.0.0"

    def get_default_config(self) -> dict[str, Any]:
        return {
            "port": 51820,
            "alt_ports": [443, 1194, 53],
            "probe_alt_ports": True,
        }

    def _server(self) -> str:
        targets = self.secrets.get("targets", {}) or {}
        return str(targets.get("wireguard_server") or self.config.get("server") or "")

    def _port(self) -> int:
        targets = self.secrets.get("targets", {}) or {}
        return int(targets.get("wireguard_port") or self.config.get("port", 51820))

    def target(self) -> str:
        server = self._server()
        return f"{server}:{self._port()}" if server else ""

    def get_probes(self) -> list[BaseProbe]:
        server = self._server()
        port = self._port()
        timeout = float(self.config.get("timeout_seconds", 10))
        ping_count = int(self.config.get("ping_count", 4))

        probes: list[BaseProbe] = [
            PingProbe({"hosts": [server], "count": ping_count, "timeout_seconds": timeout}),
            WireGuardHandshakeProbe(
                {
                    "host": server,
                    "port": port,
                    "probe_count": 2,
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
            TcpConnectProbe(
                {
                    "hosts": [server],
                    "ports": [22, 443],
                    "samples": 1,
                    "timeout_seconds": timeout,
                }
            ),
        ]
        if self.config.get("probe_alt_ports", True):
            for alt in self.config.get("alt_ports", []) or []:
                alt_port = int(alt)
                if alt_port == port:
                    continue
                probes.append(
                    WireGuardHandshakeProbe(
                        {
                            "host": server,
                            "port": alt_port,
                            "probe_count": 1,
                            "timeout_seconds": timeout,
                        }
                    )
                )
        return probes
