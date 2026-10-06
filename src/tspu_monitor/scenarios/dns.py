"""Сценарий DNS: сравнение резолверов, DoH, контроль UDP/53."""

from __future__ import annotations

from typing import Any

from ..probes.base import BaseProbe
from ..probes.dns import DnsResolveProbe, DohProbe
from ..probes.udp import UdpProbe
from ..utils import build_dns_query
from .base import BaseScenario


class DnsScenario(BaseScenario):
    name = "dns"
    title = "DNS"
    description = "DNS-спуфинг, фильтрация DoH, доступность UDP/53"
    version = "2.0.0"

    def get_default_config(self) -> dict[str, Any]:
        return {"probe_doh": True}

    def target(self) -> str:
        control = list(self.config.get("control_hosts") or [])
        return control[0] if control else ""

    def get_probes(self) -> list[BaseProbe]:
        timeout = float(self.config.get("timeout_seconds", 10))
        control = list(self.config.get("control_hosts") or [])
        blocked = list(self.config.get("blocked_test_hosts") or [])
        resolvers = list(self.config.get("dns_resolvers") or ["1.1.1.1", "8.8.8.8"])

        probes: list[BaseProbe] = [
            DnsResolveProbe(
                {
                    "hosts": control + blocked,
                    "resolvers": resolvers,
                    "timeout_seconds": timeout,
                }
            )
        ]
        if self.config.get("probe_doh", True):
            probes.append(
                DohProbe(
                    {
                        "hosts": control[:1] or ["ya.ru"],
                        "timeout_seconds": timeout,
                    }
                )
            )
        probes.append(
            UdpProbe(
                {
                    "host": "1.1.1.1",
                    "port": 53,
                    "payload": build_dns_query("ya.ru"),
                    "control": True,
                    "probe_count": 2,
                    "timeout_seconds": timeout,
                }
            )
        )
        return probes
