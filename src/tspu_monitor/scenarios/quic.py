"""Сценарий QUIC/HTTP3: проверка UDP/443 и сравнение с TCP/443."""

from __future__ import annotations

from typing import Any

from ..probes.base import BaseProbe
from ..probes.quic import QuicProbe
from ..probes.tcp import TcpConnectProbe
from .base import BaseScenario


class QuicScenario(BaseScenario):
    name = "quic"
    title = "QUIC / HTTP3"
    description = "Доступность QUIC (UDP/443) на фоне работающего TCP/443"
    version = "2.0.0"

    def get_default_config(self) -> dict[str, Any]:
        return {"probe_count": 3, "port": 443}

    def target(self) -> str:
        blocked = list(self.config.get("blocked_test_hosts") or [])
        control = list(self.config.get("control_hosts") or [])
        candidates = blocked or control
        return candidates[0] if candidates else ""

    def get_probes(self) -> list[BaseProbe]:
        timeout = float(self.config.get("timeout_seconds", 10))
        control = list(self.config.get("control_hosts") or [])
        blocked = list(self.config.get("blocked_test_hosts") or [])
        port = int(self.config.get("port", 443))
        return [
            QuicProbe(
                {
                    "hosts": control + blocked,
                    "control_hosts": control,
                    "port": port,
                    "probe_count": int(self.config.get("probe_count", 3)),
                    "timeout_seconds": timeout,
                }
            ),
            TcpConnectProbe(
                {
                    "hosts": control + blocked,
                    "ports": [port],
                    "samples": 1,
                    "timeout_seconds": timeout,
                }
            ),
        ]
