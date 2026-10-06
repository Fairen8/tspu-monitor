"""TCP-пробы: connect() с замерами и поиск инъекций RST."""

from __future__ import annotations

import asyncio
import re
import socket
from typing import Any

from ..models import ProbeResult, Severity
from .base import BaseProbe

_NMAP_PORT_RE = re.compile(r"^(\d+)/(tcp|udp)\s+(\S+)\s+(\S+)", re.MULTILINE)


class TcpConnectProbe(BaseProbe):
    """TCP ``connect()`` с несколькими попытками.

    Для каждой пары (host, port) выполняется ``samples`` попыток. Это
    позволяет измерить не только доступность, но и **стабильность**
    (уровень обрывов). Мгновенный RST (< ``fast_rst_threshold_ms``)
    считается признаком инъекции со стороны DPI.
    """

    name = "tcp.connect"
    title = "TCP connect"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        hosts = self.config.get("hosts")
        self.hosts: list[str] = list(hosts) if hosts else (
            [self.config["host"]] if self.config.get("host") else []
        )
        ports = self.config.get("ports")
        if ports:
            self.ports: list[int] = [int(p) for p in ports]
        elif self.config.get("port"):
            self.ports = [int(self.config["port"])]
        else:
            self.ports = [80, 443]
        self.samples: int = max(1, int(self.config.get("samples", 2)))
        self.fast_rst_threshold_ms: float = float(
            self.config.get("fast_rst_threshold_ms", 5.0)
        )

    async def run(self) -> list[ProbeResult]:
        tasks = [
            self._connect_one(host, port) for host in self.hosts for port in self.ports
        ]
        return list(await asyncio.gather(*tasks))

    async def _connect_one(self, host: str, port: int) -> ProbeResult:
        start = self.time_ms()
        target = f"{host}:{port}"
        ip = await self.resolve(host)
        if ip is None:
            return self.make_result(
                success=False,
                target=target,
                data={"host": host, "port": port, "attempts": 0, "successes": 0},
                error=f"не удалось разрешить имя {host}",
                severity=Severity.WARNING,
                duration_ms=self.time_ms() - start,
            )

        success_count = 0
        fast_rst_count = 0
        refused_count = 0
        timeout_count = 0
        other_errors: list[str] = []
        rtt_samples: list[float] = []
        ttl: int | None = None

        for _ in range(self.samples):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)
            try:
                # Позволяет потом прочитать TTL полученного пакета (Linux).
                try:
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_RECVTTL, 1)
                except (AttributeError, OSError):
                    pass
                t0 = self.time_ms()
                loop = asyncio.get_running_loop()
                try:
                    await asyncio.wait_for(
                        loop.sock_connect(sock, (ip, port)), timeout=self.timeout
                    )
                    duration = self.time_ms() - t0
                    success_count += 1
                    rtt_samples.append(duration)
                    try:
                        ttl = sock.getsockopt(socket.IPPROTO_IP, socket.IP_TTL)
                    except OSError:
                        ttl = None
                except TimeoutError:
                    timeout_count += 1
                except ConnectionRefusedError:
                    duration = self.time_ms() - t0
                    refused_count += 1
                    if duration < self.fast_rst_threshold_ms:
                        fast_rst_count += 1
                except OSError as exc:
                    other_errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                try:
                    sock.close()
                except OSError:
                    pass

        attempts = self.samples
        fails = attempts - success_count
        fail_ratio = fails / attempts if attempts else 0.0
        success = success_count > 0

        if fast_rst_count > 0:
            severity = Severity.CRITICAL
        elif not success or fails > 0:
            severity = Severity.WARNING
        else:
            severity = Severity.INFO

        error: str | None = None
        if not success:
            if fast_rst_count:
                error = "мгновенный RST — подозрение на инъекцию"
            elif refused_count:
                error = "соединение отклонено"
            elif timeout_count:
                error = "таймаут подключения"
            elif other_errors:
                error = other_errors[-1]
        elif fails > 0:
            error = f"{fails} из {attempts} попыток неудачны"

        data: dict[str, Any] = {
            "host": host,
            "ip": ip,
            "port": port,
            "attempts": attempts,
            "successes": success_count,
            "fail_ratio": fail_ratio,
            "fast_rst_count": fast_rst_count,
            "refused_count": refused_count,
            "timeout_count": timeout_count,
            "rtt_samples_ms": rtt_samples,
            "rtt_avg_ms": (sum(rtt_samples) / len(rtt_samples)) if rtt_samples else None,
            "ttl": ttl,
        }
        return self.make_result(
            success=success,
            target=target,
            data=data,
            raw=f"attempts={attempts} ok={success_count} rst={fast_rst_count} "
            f"refused={refused_count} timeout={timeout_count}",
            error=error,
            severity=severity,
            duration_ms=self.time_ms() - start,
        )


class PortScanProbe(BaseProbe):
    """TCP-скан через nmap (fallback — connect-пробы)."""

    name = "tcp.scan"
    title = "TCP-скан портов"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.hosts: list[str] = list(self.config.get("hosts", []))
        self.ports: list[int] = [int(p) for p in self.config.get("ports", [80, 443])]

    async def run(self) -> list[ProbeResult]:
        return list(
            await asyncio.gather(*(self._scan_one(host) for host in self.hosts))
        )

    async def _scan_one(self, host: str) -> ProbeResult:
        start = self.time_ms()
        ports_arg = ",".join(str(p) for p in self.ports)
        argv = [
            "nmap",
            "-Pn",
            "-sT",
            "-p",
            ports_arg,
            "--host-timeout",
            f"{int(self.timeout * len(self.ports))}s",
            host,
        ]
        rc, stdout, stderr = await self.run_cmd(
            argv, timeout=self.timeout * (len(self.ports) + 5)
        )

        ports: list[dict[str, Any]] = []
        if rc == -2:
            fallback = TcpConnectProbe(
                {
                    "hosts": [host],
                    "ports": self.ports,
                    "samples": 1,
                    "timeout_seconds": self.timeout,
                }
            )
            for result in await fallback.run():
                ports.append(
                    {
                        "port": result.data.get("port"),
                        "state": "open" if result.success else "closed/filtered",
                        "service": "?",
                    }
                )
            raw = "nmap не установлен — использованы connect()-пробы"
        else:
            for match in _NMAP_PORT_RE.finditer(stdout):
                ports.append(
                    {
                        "port": int(match.group(1)),
                        "state": match.group(3),
                        "service": match.group(4),
                    }
                )
            raw = stdout + ("\n" + stderr if stderr else "")

        open_ports = [p["port"] for p in ports if p.get("state") == "open"]
        filtered = [p["port"] for p in ports if "filter" in str(p.get("state"))]
        error = None
        severity = Severity.INFO
        if rc < 0 and rc != -2:
            severity = Severity.WARNING
            error = stderr or "nmap завершился с ошибкой"
        elif filtered:
            severity = Severity.WARNING
            error = f"порты в состоянии filtered: {filtered}"

        return self.make_result(
            success=bool(ports),
            target=host,
            data={
                "host": host,
                "ports": ports,
                "open_ports": open_ports,
                "filtered_ports": filtered,
            },
            raw=raw,
            error=error,
            severity=severity,
            duration_ms=self.time_ms() - start,
        )
