"""Сценарий AMNEZIA WG: junk-пакеты, размеры S1/S2, Path MTU."""

from __future__ import annotations

from typing import Any

from ..probes.base import BaseProbe
from ..probes.icmp import PathMtuProbe, PingProbe
from ..probes.tcp import TcpConnectProbe
from ..probes.udp import UdpProbe, WireGuardHandshakeProbe
from ..utils import build_dns_query
from .base import BaseScenario


class AmneziaScenario(BaseScenario):
    name = "amnezia"
    title = "AMNEZIA WG"
    description = "AmneziaWG: junk-пакеты, MTU, поведение UDP"
    version = "2.0.0"

    def get_default_config(self) -> dict[str, Any]:
        return {
            "port": 51820,
            "junk_packets": 5,
            "junk_min_size": 50,
            "junk_max_size": 150,
            "check_mtu": True,
        }

    def _server(self) -> str:
        targets = self.secrets.get("targets", {}) or {}
        return str(targets.get("amnezia_server") or self.config.get("server") or "")

    def _port(self) -> int:
        targets = self.secrets.get("targets", {}) or {}
        return int(targets.get("amnezia_port") or self.config.get("port", 51820))

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
                    "junk_packets": 0,
                    "probe_count": 2,
                    "timeout_seconds": timeout,
                }
            ),
            WireGuardHandshakeProbe(
                {
                    "host": server,
                    "port": port,
                    "junk_packets": int(self.config.get("junk_packets", 5)),
                    "junk_min_size": int(self.config.get("junk_min_size", 50)),
                    "junk_max_size": int(self.config.get("junk_max_size", 150)),
                    "probe_count": 2,
                    "timeout_seconds": timeout,
                }
            ),
            UdpProbe(
                {
                    "host": server,
                    "port": port,
                    "payload_size": 1200,
                    "expected_silent": True,
                    "probe_count": 2,
                    "timeout_seconds": timeout,
                }
            ),
            UdpProbe(
                {
                    "host": server,
                    "port": port,
                    "payload_size": 64,
                    "expected_silent": True,
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
        if self.config.get("check_mtu", True):
            probes.append(
                PathMtuProbe({"host": server, "timeout_seconds": timeout})
            )
        return probes
