"""Сценарий WEB: HTTPS-доступность, заглушки, SNI-фильтрация, RST."""

from __future__ import annotations

from typing import Any

from ..probes.base import BaseProbe
from ..probes.http import HttpProbe
from ..probes.tls import TlsHandshakeProbe
from .base import BaseScenario


class WebScenario(BaseScenario):
    name = "web"
    title = "WEB / HTTPS"
    description = "HTTPS-запросы, страницы-заглушки, SNI-фильтрация, RST"
    version = "2.0.0"

    def get_default_config(self) -> dict[str, Any]:
        return {
            "schemes": ["https"],
            "samples": 2,
            "max_body": 65536,
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

        return [
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
