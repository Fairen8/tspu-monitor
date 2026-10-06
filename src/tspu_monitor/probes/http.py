"""HTTP/HTTPS-проба: заглушки, RST, скорость загрузки."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ..models import ProbeResult, Severity
from ..utils import truncate
from .base import BaseProbe

BODY_SIGNATURES = [
    re.compile(r"доступ\s+ограничен", re.IGNORECASE),
    re.compile(r"доступ\s+к\s+информационному\s+ресурсу", re.IGNORECASE),
    re.compile(r"ресурс\s+заблокирован", re.IGNORECASE),
    re.compile(r"единый\s+реестр", re.IGNORECASE),
    re.compile(r"роскомнадзор", re.IGNORECASE),
    re.compile(r"this\s+resource\s+is\s+blocked", re.IGNORECASE),
    re.compile(r"access\s+to\s+this\s+resource\s+is\s+restricted", re.IGNORECASE),
]

HEADER_SIGNATURES = [
    re.compile(r"blackhole", re.IGNORECASE),
    re.compile(r"censor", re.IGNORECASE),
    re.compile(r"\brkn\b", re.IGNORECASE),
    re.compile(r"tspu", re.IGNORECASE),
]

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0 "
    "TSPU-Monitor/2.0"
)


def detect_plug_page(body: str) -> str | None:
    """Вернуть сигнатуру страницы-заглушки или ``None``."""
    for pattern in BODY_SIGNATURES:
        if pattern.search(body):
            return f"body:{pattern.pattern}"
    return None


def detect_filter_header(headers: dict[str, str]) -> str | None:
    for name, value in headers.items():
        for pattern in HEADER_SIGNATURES:
            if pattern.search(name) or pattern.search(value):
                return f"{name}: {truncate(value, 80)}"
    return None


class HttpProbe(BaseProbe):
    """GET-запросы с несколькими попытками.

    Собирает:

    * статус/редиректы и заголовки;
    * сигнатуры страниц-заглушек ТСПУ/оператора;
    * факты сброса соединения (RST);
    * объём и скорость загрузки (для детекта шейпинга);
    * статистику успешных/неудачных попыток (уровень обрывов).
    """

    name = "http.get"
    title = "HTTP GET"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.hosts: list[str] = list(self.config.get("hosts", []))
        self.schemes: list[str] = list(self.config.get("schemes", ["https"]))
        self.path: str = str(self.config.get("path", "/"))
        self.samples: int = max(1, int(self.config.get("samples", 2)))
        self.max_body: int = int(self.config.get("max_body", 65536))
        self.user_agent: str = str(self.config.get("user_agent", DEFAULT_UA))

    async def run(self) -> list[ProbeResult]:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        connector = aiohttp.TCPConnector(ssl=False, limit=8)
        headers = {"User-Agent": self.user_agent, "Accept": "*/*"}
        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector, headers=headers
        ) as session:
            tasks = [
                asyncio.create_task(self._probe_url(session, scheme, host))
                for host in self.hosts
                for scheme in self.schemes
            ]
            return list(await asyncio.gather(*tasks))

    async def _probe_url(self, session: Any, scheme: str, host: str) -> ProbeResult:
        url = f"{scheme}://{host}{self.path}"
        start = self.time_ms()
        attempts = 0
        successes = 0
        reset = False
        truncated_body = False
        status: int | None = None
        final_url: str | None = None
        redirects: list[str] = []
        headers: dict[str, str] = {}
        body_snippet = ""
        download_bytes = 0
        speed_kbps: float | None = None
        last_error: str | None = None

        for _ in range(self.samples):
            attempts += 1
            requested = self.time_ms()
            try:
                async with session.get(url, allow_redirects=True, ssl=False) as resp:
                    status = resp.status
                    final_url = str(resp.url)
                    redirects = [str(r.url) for r in resp.history]
                    headers = {k.lower(): v for k, v in resp.headers.items()}
                    body = bytearray()
                    async for chunk in resp.content.iter_chunked(16384):
                        body.extend(chunk)
                        if len(body) >= self.max_body:
                            truncated_body = True
                            break
                    elapsed_ms = max(1.0, self.time_ms() - requested)
                    download_bytes = max(download_bytes, len(body))
                    if len(body):
                        speed_kbps = (len(body) / 1024.0) / (elapsed_ms / 1000.0)
                    body_snippet = bytes(body[:4096]).decode(
                        resp.charset or "utf-8", errors="replace"
                    )
                    successes += 1
            except TimeoutError:
                last_error = "timeout"
            except ConnectionResetError as exc:
                reset = True
                last_error = f"connection reset: {exc}"
            except Exception as exc:  # noqa: BLE001 - aiohttp богат на исключения
                text = f"{type(exc).__name__}: {exc}"
                if "reset" in text.lower():
                    reset = True
                last_error = text

        duration = self.time_ms() - start
        body_signature = detect_plug_page(body_snippet)
        header_signature = detect_filter_header(headers)
        plug_detected = bool(body_signature or header_signature)

        if plug_detected:
            severity = Severity.CRITICAL
            error: str | None = "обнаружена страница-заглушка"
        elif reset:
            severity = Severity.CRITICAL
            error = last_error or "соединение сброшено"
        elif successes == 0:
            severity = Severity.WARNING
            error = last_error or f"HTTP {status}"
        elif successes < attempts:
            severity = Severity.WARNING
            error = f"{attempts - successes} из {attempts} запросов неудачны"
        else:
            severity = Severity.INFO
            error = None

        data: dict[str, Any] = {
            "url": url,
            "host": host,
            "scheme": scheme,
            "attempts": attempts,
            "successes": successes,
            "fail_ratio": (attempts - successes) / attempts if attempts else 0.0,
            "status": status,
            "final_url": final_url,
            "redirects": redirects,
            "server_header": headers.get("server"),
            "plug_page_detected": plug_detected,
            "body_signature": body_signature,
            "header_signature": header_signature,
            "download_bytes": download_bytes,
            "speed_kbps": speed_kbps,
            "truncated_body": truncated_body,
            "reset": reset,
        }
        raw = (
            f"GET {url} -> {status} (попыток: {attempts}, успешных: {successes})\n"
            f"body[:512]={truncate(body_snippet, 512)}"
        )
        return self.make_result(
            success=successes > 0 and not plug_detected,
            target=url,
            data=data,
            raw=raw,
            error=error,
            severity=severity,
            duration_ms=duration,
        )



