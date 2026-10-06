"""Шаблон пользовательского сценария.

Скопируйте файл под другим именем (без ведущего подчёркивания), например::

    cp _example.py my_service.py

и адаптируйте под свою задачу. Затем включите сценарий:

    tspu-monitor scenarios enable my_service
"""

from __future__ import annotations

from typing import Any

from ...probes.base import BaseProbe
from ...probes.http import HttpProbe
from ...probes.tcp import TcpConnectProbe
from ..base import BaseScenario


class MyServiceScenario(BaseScenario):
    """Пример: доступность HTTP-сервиса и его TCP-порта."""

    name = "my_service"
    title = "MY SERVICE"
    description = "Шаблон: TCP-порт + HTTP-код ответа"
    version = "2.0.0"

    def get_default_config(self) -> dict[str, Any]:
        return {
            "host": "example.com",
            "port": 443,
            "scheme": "https",
            "path": "/",
        }

    def target(self) -> str:
        return str(self.config.get("host", ""))

    def get_probes(self) -> list[BaseProbe]:
        host = str(self.config.get("host", ""))
        port = int(self.config.get("port", 443))
        timeout = float(self.config.get("timeout_seconds", 10))
        return [
            TcpConnectProbe(
                {
                    "hosts": [host],
                    "ports": [port],
                    "samples": int(self.config.get("samples", 2)),
                    "timeout_seconds": timeout,
                }
            ),
            HttpProbe(
                {
                    "hosts": [host],
                    "schemes": [str(self.config.get("scheme", "https"))],
                    "path": str(self.config.get("path", "/")),
                    "samples": 1,
                    "timeout_seconds": timeout,
                }
            ),
        ]
