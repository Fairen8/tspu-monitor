"""Сценарий XRAY / VLESS Reality: SNI real/bogus/cover и сверка сертификатов."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..classification import Finding
from ..models import BlockType, ProbeResult
from ..probes.base import BaseProbe
from ..probes.tcp import TcpConnectProbe
from ..probes.tls import TlsHandshakeProbe
from .base import BaseScenario


class XrayScenario(BaseScenario):
    name = "xray"
    title = "XRAY / VLESS REALITY"
    description = "Reality: SNI-дифференциал, cover-ресурс, сертификаты"
    version = "2.0.0"

    def get_default_config(self) -> dict[str, Any]:
        return {
            "port": 443,
            "reality_sni": "www.microsoft.com",
            "bogus_sni": "bogus.invalid.example",
        }

    def _server(self) -> str:
        targets = self.secrets.get("targets", {}) or {}
        return str(targets.get("xray_server") or self.config.get("server") or "")

    def _port(self) -> int:
        targets = self.secrets.get("targets", {}) or {}
        return int(targets.get("xray_port") or self.config.get("port", 443))

    def _reality_sni(self) -> str:
        targets = self.secrets.get("targets", {}) or {}
        return str(targets.get("xray_reality_sni") or self.config.get("reality_sni"))

    def target(self) -> str:
        server = self._server()
        return f"{server}:{self._port()}" if server else ""

    def get_probes(self) -> list[BaseProbe]:
        server = self._server()
        port = self._port()
        timeout = float(self.config.get("timeout_seconds", 10))
        sni_real = self._reality_sni()
        sni_bogus = str(self.config.get("bogus_sni", "bogus.invalid.example"))
        return [
            TcpConnectProbe(
                {
                    "hosts": [server],
                    "ports": [port],
                    "samples": 1,
                    "timeout_seconds": timeout,
                }
            ),
            TlsHandshakeProbe(
                {
                    "targets": [
                        {"host": server, "port": port, "sni": sni_real, "role": "real"},
                        {"host": server, "port": port, "sni": sni_bogus, "role": "bogus"},
                        {"host": sni_real, "port": 443, "sni": sni_real, "role": "cover"},
                    ],
                    "timeout_seconds": timeout,
                }
            ),
        ]

    def extra_findings(self, results: Sequence[ProbeResult]) -> list[Finding]:
        real = next(
            (
                r
                for r in results
                if r.probe == "tls.handshake" and r.data.get("role") == "real"
            ),
            None,
        )
        cover = next(
            (
                r
                for r in results
                if r.probe == "tls.handshake" and r.data.get("role") == "cover"
            ),
            None,
        )
        if real and cover and real.success and cover.success:
            subject_real = real.data.get("subject")
            subject_cover = cover.data.get("subject")
            if subject_real and subject_cover and subject_real != subject_cover:
                return [
                    Finding(
                        BlockType.MISCONFIG,
                        8,
                        "Сертификат XRay-сервера отличается от cover-сайта",
                        cause="Reality настроен некорректно: сертификат cover не клонируется",
                        recommendation=(
                            "Проверьте privateKey/dest/shortIds в конфигурации Reality"
                        ),
                    )
                ]
        return []
