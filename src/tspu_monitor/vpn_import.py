"""Импорт конфигураций VPN-протоколов.

Принимает файлы и URI популярных клиентов и извлекает только технические
параметры сервера (адрес, порт, публичный ключ, SNI): WireGuard,
AmneziaWG, OpenVPN, Shadowsocks, Xray/v2ray (VLESS/VMess/Trojan/SS).

Секреты (приватные ключи, пароли, UUID) **никогда не сохраняются** —
из них берётся максимум метод/протокол как техническая информация.

Использование из CLI::

    tspu-monitor config import wg0.conf
    tspu-monitor config import client.ovpn
    tspu-monitor config import 'ss://YWVz...@1.2.3.4:8388#tag'
    tspu-monitor config import vless://...  --dry-run
"""

from __future__ import annotations

import base64
import dataclasses
import json
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

#: Схемы URI, которые понимает импорт.
URI_SCHEMES = ("ss://", "vless://", "vmess://", "trojan://")

#: Ключи AmneziaWG в [Interface] (обфускация WireGuard).
_AMNEZIA_KEYS = re.compile(r"^(jc|jmin|jmax|s1|s2|h[1-4])$")


def _split_host_port(value: str, default_port: int) -> tuple[str, int]:
    """Разобрать ``host:port`` (в т.ч. ``[IPv6]:port``) с порогом по умолчанию."""
    value = value.strip()
    if value.startswith("["):
        host, _, rest = value[1:].partition("]")
        rest = rest.lstrip(":")
        return host, int(rest) if rest.isdigit() else default_port
    if value.count(":") == 1:
        host, _, port = value.rpartition(":")
        if port.isdigit():
            return host, int(port)
    return value, default_port


def _b64decode(value: str) -> str:
    """Base64/base64url с восстановлением паддинга; при ошибке — как есть."""
    raw = value.strip().replace("-", "+").replace("_", "/")
    raw += "=" * (-len(raw) % 4)
    try:
        return base64.b64decode(raw).decode("utf-8", errors="replace")
    except (ValueError, UnicodeDecodeError):
        return value


@dataclasses.dataclass
class ImportResult:
    """Итог разбора конфигурации: что нашли и что сохранить."""

    protocol: str
    targets: dict[str, Any] = dataclasses.field(default_factory=dict)
    notes: list[str] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# WireGuard / AmneziaWG
# ---------------------------------------------------------------------------


def parse_wireguard(text: str) -> ImportResult:
    """Разобрать ``.conf`` WireGuard/AmneziaWG (``[Interface]``/``[Peer]``)."""
    values: dict[str, str] = {}
    section = ""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line.strip("[]").strip().lower()
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        name = key.strip().lower()
        if section:
            values[f"{section}.{name}"] = value.strip()
        else:
            values[name] = value.strip()

    interface_keys = {
        key.split(".", 1)[1] for key in values if key.startswith("interface.")
    }
    if not interface_keys:
        raise ValueError("не найден раздел [Interface] — это не WireGuard-конфиг")

    amnezia = any(_AMNEZIA_KEYS.fullmatch(name) for name in interface_keys)
    protocol = "amnezia" if amnezia else "wireguard"
    prefix = protocol

    endpoint = values.get("peer.endpoint", "")
    if not endpoint:
        raise ValueError("в [Peer] не найден Endpoint")
    host, port = _split_host_port(endpoint, 51820)
    if not host:
        raise ValueError("не удалось разобрать Endpoint")

    targets: dict[str, Any] = {
        f"{prefix}_server": host,
        f"{prefix}_port": port,
    }
    public_key = values.get("peer.publickey", "")
    if public_key:
        targets[f"{prefix}_public_key"] = public_key

    notes = [f"Endpoint: {host}:{port}"]
    if amnezia:
        junk = ", ".join(
            f"{name}={values.get(f'interface.{name}', '')}"
            for name in ("jc", "jmin", "jmax", "s1", "s2")
            if values.get(f"interface.{name}")
        )
        if junk:
            notes.append(f"AmneziaWG-обфускация: {junk}")
    warnings: list[str] = []
    if "interface.privatekey" in values or "peer.presharedkey" in values:
        warnings.append(
            "Приватные ключи найдены в конфиге, но не сохраняются — "
            "в secrets.yaml попадут только сервер, порт и публичный ключ."
        )
    return ImportResult(protocol, targets, notes, warnings)


# ---------------------------------------------------------------------------
# OpenVPN
# ---------------------------------------------------------------------------


def parse_openvpn(text: str) -> ImportResult:
    """Разобрать ``.ovpn``: строка ``remote host [port] [proto]``."""
    remote: tuple[str, int, str] | None = None
    proto = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        parts = line.split()
        keyword = parts[0].lower()
        if keyword == "proto" and len(parts) >= 2:
            proto = parts[1].lower()
        elif keyword == "remote" and len(parts) >= 2 and remote is None:
            host = parts[1]
            port = 1194
            line_proto = ""
            if len(parts) >= 3 and parts[2].isdigit():
                port = int(parts[2])
            if len(parts) >= 4:
                line_proto = parts[3].lower()
            elif len(parts) >= 3 and not parts[2].isdigit():
                line_proto = parts[2].lower()
            remote = (host, port, line_proto)
    if remote is None:
        raise ValueError("не найдена строка 'remote host port' — это не OpenVPN-конфиг")

    host, port, line_proto = remote
    targets: dict[str, Any] = {
        "openvpn_server": host,
        "openvpn_port": port,
    }
    effective_proto = line_proto or proto
    if effective_proto:
        targets["openvpn_proto"] = effective_proto
    note = f"remote {host} {port} {effective_proto or ''}".strip()
    return ImportResult("openvpn", targets, [note], [])


# ---------------------------------------------------------------------------
# Shadowsocks
# ---------------------------------------------------------------------------


def parse_shadowsocks_uri(uri: str) -> ImportResult:
    """SIP002 ``ss://userinfo@host:port`` и legacy ``ss://base64(...)``."""
    body = uri[len("ss://"):].split("#", 1)[0]
    if "@" in body:
        userinfo, hostport = body.rsplit("@", 1)
        decoded = _b64decode(unquote(userinfo))
    else:
        decoded_all = _b64decode(body)
        if "@" not in decoded_all:
            raise ValueError("не удалось разобрать ss:// (нет заданных пользователя/хоста)")
        userinfo, hostport = decoded_all.rsplit("@", 1)
        decoded = userinfo
    host, port = _split_host_port(hostport, 8388)
    method = decoded.split(":", 1)[0] if ":" in decoded else decoded
    targets: dict[str, Any] = {
        "shadowsocks_server": host,
        "shadowsocks_port": port,
    }
    notes = [f"Сервер: {host}:{port}"]
    if method:
        targets["shadowsocks_method"] = method
        notes.append(f"Метод шифрования: {method}")
    return ImportResult("shadowsocks", targets, notes, [])


def parse_shadowsocks_json(data: dict[str, Any]) -> ImportResult:
    """JSON-конфиг shadowsocks-libev/Outline (``server``/``server_port``)."""
    host = str(data.get("server") or "")
    port = int(data.get("server_port") or 8388)
    if not host:
        raise ValueError("в JSON нет поля server")
    targets: dict[str, Any] = {
        "shadowsocks_server": host,
        "shadowsocks_port": port,
    }
    notes = [f"Сервер: {host}:{port}"]
    method = str(data.get("method") or "")
    if method:
        targets["shadowsocks_method"] = method
        notes.append(f"Метод шифрования: {method}")
    return ImportResult("shadowsocks", targets, notes, [])


# ---------------------------------------------------------------------------
# Xray / v2ray URI и JSON
# ---------------------------------------------------------------------------


def parse_vless_uri(uri: str) -> ImportResult:
    parsed = urlparse(uri)
    host = parsed.hostname or ""
    port = parsed.port or 443
    if not host:
        raise ValueError("в vless:// не найден адрес сервера")
    params = parse_qs(parsed.query)
    sni = (params.get("sni") or params.get("serverName") or [""])[0]
    security = (params.get("security") or [""])[0]
    network = (params.get("type") or [""])[0]
    targets: dict[str, Any] = {"xray_server": host, "xray_port": port}
    notes = [f"VLESS: {host}:{port}"]
    if security:
        notes.append(f"Защита: {security}")
    if network:
        notes.append(f"Транспорт: {network}")
    if sni:
        targets["xray_reality_sni"] = sni
        notes.append(f"SNI: {sni}")
    warnings = []
    if params.get("pbk"):
        warnings.append("Reality-ключ (pbk) не сохраняется — только SNI.")
    return ImportResult("xray", targets, notes, warnings)


def parse_trojan_uri(uri: str) -> ImportResult:
    parsed = urlparse(uri)
    host = parsed.hostname or ""
    port = parsed.port or 443
    if not host:
        raise ValueError("в trojan:// не найден адрес сервера")
    params = parse_qs(parsed.query)
    sni = (params.get("sni") or params.get("peer") or [""])[0]
    targets: dict[str, Any] = {"xray_server": host, "xray_port": port}
    notes = [f"Trojan: {host}:{port}"]
    if sni:
        targets["xray_reality_sni"] = sni
        notes.append(f"SNI: {sni}")
    return ImportResult("xray", targets, notes, [])


def parse_vmess_uri(uri: str) -> ImportResult:
    body = uri[len("vmess://"):].strip()
    decoded = _b64decode(body)
    try:
        data = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise ValueError(f"vmess:// не декодируется в JSON: {exc}") from exc
    host = str(data.get("add") or "")
    port = int(data.get("port") or 443)
    if not host:
        raise ValueError("в vmess:// нет адреса (add)")
    targets: dict[str, Any] = {"xray_server": host, "xray_port": port}
    notes = [f"VMess: {host}:{port}"]
    sni = str(data.get("sni") or data.get("host") or "")
    if sni:
        targets["xray_reality_sni"] = sni
        notes.append(f"SNI: {sni}")
    return ImportResult("xray", targets, notes, [])


def parse_xray_json(text: str) -> ImportResult:
    """JSON-конфиг Xray/v2ray: outbound vless/vmess/trojan/shadowsocks."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"не JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("ожидался JSON-объект")

    outbounds = data.get("outbounds") or []
    for outbound in outbounds:
        if not isinstance(outbound, dict):
            continue
        protocol = str(outbound.get("protocol") or "")
        settings = outbound.get("settings") or {}
        stream = outbound.get("streamSettings") or {}
        reality = stream.get("realitySettings") or {}
        tls = stream.get("tlsSettings") or {}
        sni = str(reality.get("serverName") or tls.get("serverName") or "")

        if protocol in ("vless", "vmess"):
            vnext = (settings.get("vnext") or [{}])[0] if settings else {}
            host = str(vnext.get("address") or vnext.get("host") or "")
            port = int(vnext.get("port") or 443)
            if host:
                targets: dict[str, Any] = {"xray_server": host, "xray_port": port}
                notes = [f"{protocol.upper()}: {host}:{port}"]
                if sni:
                    targets["xray_reality_sni"] = sni
                    notes.append(f"SNI: {sni}")
                warnings = []
                if reality.get("publicKey"):
                    warnings.append("Reality publicKey не сохраняется — только SNI.")
                return ImportResult("xray", targets, notes, warnings)
        elif protocol == "trojan":
            servers = (settings.get("servers") or [{}])[0] if settings else {}
            host = str(servers.get("address") or "")
            port = int(servers.get("port") or 443)
            if host:
                targets = {"xray_server": host, "xray_port": port}
                notes = [f"Trojan: {host}:{port}"]
                if sni:
                    targets["xray_reality_sni"] = sni
                    notes.append(f"SNI: {sni}")
                return ImportResult("xray", targets, notes, [])
        elif protocol == "shadowsocks":
            servers = (settings.get("servers") or [{}])[0] if settings else {}
            host = str(servers.get("address") or "")
            port = int(servers.get("port") or 8388)
            method = str(servers.get("method") or "")
            if host:
                targets = {"shadowsocks_server": host, "shadowsocks_port": port}
                notes = [f"Shadowsocks: {host}:{port}"]
                if method:
                    targets["shadowsocks_method"] = method
                    notes.append(f"Метод шифрования: {method}")
                return ImportResult("shadowsocks", targets, notes, [])
    raise ValueError("в JSON нет поддерживаемого outbound (vless/vmess/trojan/shadowsocks)")


# ---------------------------------------------------------------------------
# Определение формата
# ---------------------------------------------------------------------------


def import_config(text: str, name: str = "") -> ImportResult:
    """Определить формат по содержимому и разобрать конфигурацию.

    ``name`` — имя файла (для подсказок по расширению), может быть пустым.
    """
    stripped = text.strip()
    if stripped.startswith(URI_SCHEMES):
        scheme = stripped.split(":", 1)[0].lower()
        if scheme == "ss":
            return parse_shadowsocks_uri(stripped)
        if scheme == "vless":
            return parse_vless_uri(stripped)
        if scheme == "trojan":
            return parse_trojan_uri(stripped)
        return parse_vmess_uri(stripped)

    lower_name = name.lower()
    if lower_name.endswith((".conf", ".wg")) or "[Interface]" in text:
        return parse_wireguard(text)
    if lower_name.endswith(".ovpn") or re.search(r"(?mi)^\s*remote\s+\S+", text):
        return parse_openvpn(text)
    if stripped.startswith("{"):
        try:
            return parse_xray_json(stripped)
        except ValueError as xray_error:
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                raise xray_error from None
            if isinstance(data, dict) and data.get("server"):
                return parse_shadowsocks_json(data)
            raise xray_error from None
    raise ValueError(
        "не удалось определить формат. Поддерживаются: WireGuard/AmneziaWG "
        "(.conf), OpenVPN (.ovpn), Xray/v2ray (.json), URI ss://, vless://, "
        "vmess://, trojan://"
    )
