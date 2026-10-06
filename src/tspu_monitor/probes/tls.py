"""TLS-проба: handshake с произвольным SNI и классификация ошибок.

Основной путь — ``openssl s_client`` (даёт подробности сертификата).
Если openssl недоступен, используется Python ``ssl``.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import socket
import ssl
from typing import Any

from ..models import ProbeResult, Severity
from ..utils import truncate
from .base import BaseProbe

_CIPHER_RE = re.compile(r"Cipher\s*[:\s]\s*(\S+)")
_PROTOCOL_RE = re.compile(r"(?:Protocol version|Protocol)\s*[:\s]\s*(\S+)")
_SUBJECT_RE = re.compile(r"subject=([^\n]+)")
_ISSUER_RE = re.compile(r"issuer=([^\n]+)")
_VERIFY_RE = re.compile(r"Verification\s*:\s*(\w+)")

_OPENSSL_PATH: str | None = None


def _openssl_binary() -> str | None:
    global _OPENSSL_PATH
    if _OPENSSL_PATH is None:
        _OPENSSL_PATH = shutil.which("openssl") or ""
    return _OPENSSL_PATH or None


def classify_tls_error(text: str, rc: int = 0) -> str | None:
    """Классифицировать ошибку TLS по выводу openssl/исключению.

    Возвращает ``reset`` / ``alert`` / ``handshake`` / ``cert`` / ``timeout``
    или ``None``, если ошибки не видно.
    """
    lower = (text or "").lower()
    if rc == -1 or "timeout" in lower:
        return "timeout"
    if "connection reset" in lower or "reset by peer" in lower or "broken pipe" in lower:
        return "reset"
    if "no peer certificate" in lower:
        return "handshake"
    if "certificate verify failed" in lower or "unable to verify" in lower:
        return "cert"
    if "alert" in lower:
        return "alert"
    return None


_ERROR_RU = {
    "reset": "соединение сброшено (RST)",
    "alert": "TLS alert во время handshake",
    "handshake": "handshake прерван",
    "cert": "ошибка проверки сертификата",
    "timeout": "таймаут handshake",
}


class TlsHandshakeProbe(BaseProbe):
    """TLS handshake к списку целей.

    Каждая цель: ``{"host": ..., "port": 443, "sni": ..., "role": "real"}``.
    Роль используется правилами классификации:

    * ``real`` — целевой ресурс с настоящим SNI;
    * ``bogus`` — тот же ресурс с заведомо ложным SNI;
    * ``cover`` — контрольный ресурс.
    """

    name = "tls.handshake"
    title = "TLS handshake"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.targets: list[dict[str, Any]] = list(self.config.get("targets", []))

    async def run(self) -> list[ProbeResult]:
        if not self.targets:
            return [self.skipped_result("tls.handshake: не заданы targets")]
        return list(
            await asyncio.gather(*(self._probe_one(t) for t in self.targets))
        )

    async def _probe_one(self, target: dict[str, Any]) -> ProbeResult:
        host = str(target.get("host", ""))
        port = int(target.get("port", 443))
        sni = str(target.get("sni") or host)
        role = str(target.get("role", "real"))
        label = f"{host}:{port}[{sni}]"

        if _openssl_binary():
            return await self._openssl_probe(host, port, sni, role, label)
        return await self._python_probe(host, port, sni, role, label)

    # -- openssl ----------------------------------------------------------
    async def _openssl_probe(
        self, host: str, port: int, sni: str, role: str, label: str
    ) -> ProbeResult:
        start = self.time_ms()
        rc, stdout, stderr = await self.run_cmd(
            [
                "openssl",
                "s_client",
                "-connect",
                f"{host}:{port}",
                "-servername",
                sni,
                "-brief",
                "-no_ign_eof",
            ],
            timeout=self.timeout,
            stdin=b"",
        )
        combined = (stdout + "\n" + stderr).strip()
        duration = self.time_ms() - start

        cipher = _first(_CIPHER_RE, combined)
        protocol = _first(_PROTOCOL_RE, combined)
        subject = _first(_SUBJECT_RE, combined)
        issuer = _first(_ISSUER_RE, combined)
        verification = _first(_VERIFY_RE, combined)
        ok = rc == 0 and ("Connection established" in combined or "Protocol version" in combined)
        error_class = None if ok else classify_tls_error(combined, rc)

        if ok:
            severity = Severity.INFO
            error = None
        else:
            error_class = error_class or "handshake"
            severity = (
                Severity.CRITICAL
                if error_class in ("reset", "alert", "handshake")
                else Severity.WARNING
            )
            error = _ERROR_RU.get(error_class, "handshake не завершился")

        return self.make_result(
            success=ok,
            target=label,
            data={
                "host": host,
                "port": port,
                "sni": sni,
                "role": role,
                "ok": ok,
                "cipher": cipher,
                "protocol": protocol,
                "subject": subject,
                "issuer": issuer,
                "verify": verification,
                "error_class": error_class,
                "handshake_ms": duration,
            },
            raw=truncate(combined, 2000),
            error=error,
            severity=severity,
            duration_ms=duration,
        )

    # -- python ssl fallback ----------------------------------------------
    async def _python_probe(
        self, host: str, port: int, sni: str, role: str, label: str
    ) -> ProbeResult:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, self._python_handshake, host, port, sni
        )
        ok = result["ok"]
        error_class = None if ok else (result.get("error_class") or "handshake")
        severity = (
            Severity.INFO
            if ok
            else (
                Severity.CRITICAL
                if error_class in ("reset", "alert", "handshake")
                else Severity.WARNING
            )
        )
        return self.make_result(
            success=ok,
            target=label,
            data={
                "host": host,
                "port": port,
                "sni": sni,
                "role": role,
                "ok": ok,
                "cipher": result.get("cipher"),
                "protocol": result.get("protocol"),
                "subject": None,
                "issuer": None,
                "verify": None,
                "error_class": error_class,
                "handshake_ms": result.get("duration_ms"),
                "impl": "python-ssl",
            },
            raw=result.get("error") or "python ssl handshake",
            error=None if ok else _ERROR_RU.get(error_class, "handshake не завершился"),
            severity=severity,
            duration_ms=result.get("duration_ms") or 0.0,
        )

    def _python_handshake(self, host: str, port: int, sni: str) -> dict[str, Any]:
        start = self.time_ms()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        out: dict[str, Any] = {"ok": False, "error": None}
        try:
            sock.connect((host, port))
            with ctx.wrap_socket(sock, server_hostname=sni) as tls_sock:
                cipher_info = tls_sock.cipher()
                if cipher_info:
                    out["cipher"], out["protocol"] = cipher_info[0], cipher_info[1]
                out["ok"] = True
        except (ssl.SSLError, TimeoutError) as exc:
            text = f"{type(exc).__name__}: {exc}"
            out["error"] = text
            out["error_class"] = classify_tls_error(text)
        except OSError as exc:
            text = f"{type(exc).__name__}: {exc}"
            out["error"] = text
            out["error_class"] = classify_tls_error(text)
        finally:
            out["duration_ms"] = self.time_ms() - start
            try:
                sock.close()
            except OSError:
                pass
        return out


def _first(regex: re.Pattern[str], text: str) -> str | None:
    match = regex.search(text)
    return match.group(1).strip() if match else None
