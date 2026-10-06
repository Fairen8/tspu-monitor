"""Сценарий SHADOWSOCKS: reachability, энтропия, replay-cache."""

from __future__ import annotations

import asyncio
import os
import socket
from typing import Any

from ..models import ProbeResult, Severity
from ..probes.base import BaseProbe
from ..probes.tcp import TcpConnectProbe
from .base import BaseScenario


class ShadowsocksEntropyProbe(BaseProbe):
    """TCP-зонд: отправить случайный блок и проследить за реакцией.

    Реальный Shadowsocks-сервер без ключа ответ не даёт — молчание
    ожидаемо. Признак блокировки — RST/разрыв после отправки шума.
    """

    name = "shadowsocks.entropy"
    title = "Shadowsocks entropy"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.host: str = str(self.config.get("host", ""))
        self.port: int = int(self.config.get("port", 8388))
        payload = self.config.get("payload")
        if isinstance(payload, bytes):
            self.payload: bytes = payload
        else:
            self.payload = os.urandom(int(self.config.get("bytes", 256)))

    async def run(self) -> list[ProbeResult]:
        if not self.host:
            return [self.skipped_result("shadowsocks.entropy: не задан host")]
        start = self.time_ms()
        target = f"{self.host}:{self.port}"
        ip = await self.resolve(self.host)
        if ip is None:
            return self.make_result(
                success=False,
                target=target,
                data={"host": self.host, "port": self.port},
                error=f"не удалось разрешить имя {self.host}",
                severity=Severity.WARNING,
                duration_ms=self.time_ms() - start,
            )

        loop = asyncio.get_running_loop()
        connected = False
        reset = False
        reply_bytes = 0
        error: str | None = None
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            await asyncio.wait_for(
                loop.sock_connect(sock, (ip, self.port)), timeout=self.timeout
            )
            connected = True
            try:
                await loop.sock_sendall(sock, self.payload)
            except (ConnectionResetError, BrokenPipeError) as exc:
                reset = True
                error = f"сброс при отправке: {exc}"
            if not reset:
                try:
                    data = await asyncio.wait_for(
                        loop.sock_recv(sock, 4096), timeout=self.timeout
                    )
                    reply_bytes = len(data)
                except TimeoutError:
                    pass
                except (ConnectionResetError, BrokenPipeError) as exc:
                    reset = True
                    error = f"сброс при чтении: {exc}"
        except TimeoutError as exc:
            error = f"таймаут подключения: {exc}"
        except (ConnectionRefusedError, OSError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                sock.close()
            except OSError:
                pass

        severity = (
            Severity.CRITICAL if not connected or reset else Severity.INFO
        )

        return [
            self.make_result(
                success=connected and not reset,
                target=target,
                data={
                    "host": self.host,
                    "ip": ip,
                    "port": self.port,
                    "connected": connected,
                    "reply_bytes": reply_bytes,
                    "reset": reset,
                    "bytes_sent": len(self.payload),
                },
                raw=f"payload={len(self.payload)}B connected={connected} "
                f"reply={reply_bytes} reset={reset}",
                error=error,
                severity=severity,
                duration_ms=self.time_ms() - start,
            )
        ]


class ShadowsocksScenario(BaseScenario):
    name = "shadowsocks"
    title = "SHADOWSOCKS / OUTLINE"
    description = "Shadowsocks: случайный поток, replay-cache, RST"
    version = "2.0.0"

    def get_default_config(self) -> dict[str, Any]:
        return {
            "port": 8388,
            "extra_ports": [443, 80, 8443],
            "entropy_bytes": 256,
        }

    def _server(self) -> str:
        targets = self.secrets.get("targets", {}) or {}
        return str(
            targets.get("shadowsocks_server") or self.config.get("server") or ""
        )

    def _port(self) -> int:
        targets = self.secrets.get("targets", {}) or {}
        return int(targets.get("shadowsocks_port") or self.config.get("port", 8388))

    def target(self) -> str:
        server = self._server()
        return f"{server}:{self._port()}" if server else ""

    def get_probes(self) -> list[BaseProbe]:
        server = self._server()
        port = self._port()
        timeout = float(self.config.get("timeout_seconds", 10))
        payload = os.urandom(int(self.config.get("entropy_bytes", 256)))
        ports = [port] + [
            int(p) for p in self.config.get("extra_ports", []) if int(p) != port
        ]
        return [
            TcpConnectProbe(
                {
                    "hosts": [server],
                    "ports": ports,
                    "samples": 1,
                    "timeout_seconds": timeout,
                }
            ),
            ShadowsocksEntropyProbe(
                {
                    "host": server,
                    "port": port,
                    "payload": payload,
                    "timeout_seconds": timeout,
                }
            ),
            # Повторная отправка ТЕХ ЖЕ байтов — проверка replay-cache.
            ShadowsocksEntropyProbe(
                {
                    "host": server,
                    "port": port,
                    "payload": payload,
                    "timeout_seconds": timeout,
                }
            ),
        ]
