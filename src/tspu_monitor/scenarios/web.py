"""Сценарий WEB: HTTPS-доступность, заглушки, SNI-фильтрация, RST, ICMP, TTL."""

from __future__ import annotations

from typing import Any

from ..probes.base import BaseProbe
from ..probes.http import HttpProbe
from ..probes.icmp import PingProbe, TraceProbe
from ..probes.raw import RawTtlProbe
from ..probes.tcp import TcpConnectProbe
from ..probes.tls import TlsHandshakeProbe
from .base import BaseScenario


class WebScenario(BaseScenario):
    name = "web"
    title = "WEB / HTTPS"
    description = (
        "HTTPS, страницы-заглушки, SNI-фильтрация, RST, ICMP, traceroute, "
        "TTL-аномалии (raw)"
    )
    version = "2.0.0"

    def get_default_config(self) -> dict[str, Any]:
        return {
            "schemes": ["https"],
            "samples": 2,
            "max_body": 65536,
            "probe_tcp": True,
            "probe_ping": True,
            "probe_trace": True,
            "probe_raw": True,
            "ping_count": 3,
            "trace_targets": 1,
            "trace_max_ttl": 15,
            "trace_timeout_seconds": 2,
        }

    def target(self) -> str:
        blocked = list(self.config.get("blocked_test_hosts") or [])
        control = list(self.config.get("control_hosts") or [])
        candidates = blocked or control
        return candidates[0] if candidates else ""

    def get_probes(self) -> list[BaseProbe]:
        timeout = float(self.config.get("timeout_seconds", 10))
        samples = int(self.config.get("samples", 2))
        control = list(self.config.get("control_hosts") or [])
        blocked = list(self.config.get("blocked_test_hosts") or [])
        hosts = control + blocked

        tls_targets: list[dict[str, Any]] = [
            {"host": host, "port": 443, "sni": host, "role": "real"} for host in blocked
        ]
        tls_targets += [
            {"host": host, "port": 443, "sni": host, "role": "cover"} for host in control
        ]

        probes: list[BaseProbe] = [
            HttpProbe(
                {
                    "hosts": hosts,
                    "schemes": list(self.config.get("schemes", ["https"])),
                    "samples": samples,
                    "max_body": int(self.config.get("max_body", 65536)),
                    "timeout_seconds": timeout,
                }
            ),
            TlsHandshakeProbe(
                {"targets": tls_targets, "timeout_seconds": timeout}
            ),
        ]

        if self.config.get("probe_tcp", True) and hosts:
            probes.append(
                TcpConnectProbe(
                    {
                        "hosts": hosts,
                        "ports": [443],
                        "samples": 2,
                        "timeout_seconds": timeout,
                    }
                )
            )
        if self.config.get("probe_ping", True) and hosts:
            probes.append(
                PingProbe(
                    {
                        "hosts": hosts,
                        "count": int(self.config.get("ping_count", 3)),
                        "timeout_seconds": timeout,
                    }
                )
            )
        if self.config.get("probe_trace", True) and hosts:
            trace_hosts = hosts[: max(1, int(self.config.get("trace_targets", 1)))]
            probes.append(
                TraceProbe(
                    {
                        "hosts": trace_hosts,
                        "max_ttl": int(self.config.get("trace_max_ttl", 15)),
                        "timeout_seconds": float(
                            self.config.get("trace_timeout_seconds", 2)
                        ),
                    }
                )
            )
        if self.config.get("probe_raw", True) and hosts:
            # Требует root/scapy — иначе проба аккуратно пропускается.
            probes.append(
                RawTtlProbe(
                    {
                        "hosts": hosts,
                        "ports": [443],
                        "timeout_seconds": timeout,
                    }
                )
            )
        return probes
