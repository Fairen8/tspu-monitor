"""Тесты импорта конфигураций VPN-протоколов."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
import yaml

from tspu_monitor.cli import main
from tspu_monitor.vpn_import import import_config

WG_CONF = """\
[Interface]
PrivateKey = PRIVATE-KEY-DATA
Address = 10.7.0.2/32
DNS = 1.1.1.1

[Peer]
PublicKey = SERVER-PUBLIC-KEY
PresharedKey = PSK-DATA
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = vpn.example.com:51820
"""

AMNEZIA_CONF = """\
[Interface]
PrivateKey = PRIVATE
Address = 10.8.1.2/32
Jc = 5
Jmin = 50
Jmax = 150
S1 = 30
S2 = 40
H1 = 123456

[Peer]
PublicKey = AMNEZIA-PUB
Endpoint = [2001:db8::1]:55555
"""

OVPN = """\
client
dev tun
proto tcp
remote vpn.example.org 443 tcp
remote-cert-tls server
"""

SS_URI = (
    "ss://"
    + base64.b64encode(b"aes-256-gcm:pw").decode()
    + "@1.2.3.4:8388#node"
)

VLESS_URI = (
    "vless://11111111-2222-3333-4444-555555555555@1.2.3.4:443"
    "?type=tcp&security=reality&sni=www.microsoft.com&pbk=SECRET#node"
)

TROJAN_URI = "trojan://password@5.6.7.8:443?sni=cover.example#node"

VMESS_URI = "vmess://" + base64.b64encode(
    json.dumps(
        {"add": "9.9.9.9", "port": "8443", "sni": "cdn.example.com", "ps": "node"}
    ).encode()
).decode()

XRAY_JSON = json.dumps(
    {
        "outbounds": [
            {"protocol": "freedom"},
            {
                "protocol": "vless",
                "settings": {"vnext": [{"address": "10.0.0.1", "port": 8443}]},
                "streamSettings": {
                    "security": "reality",
                    "realitySettings": {"serverName": "www.microsoft.com"},
                },
            },
        ]
    }
)

SS_JSON = json.dumps(
    {
        "server": "1.2.3.4",
        "server_port": 8388,
        "method": "chacha20-ietf-poly1305",
        "password": "secret",
    }
)


def test_wireguard_conf():
    result = import_config(WG_CONF, name="wg0.conf")
    assert result.protocol == "wireguard"
    assert result.targets["wireguard_server"] == "vpn.example.com"
    assert result.targets["wireguard_port"] == 51820
    assert result.targets["wireguard_public_key"] == "SERVER-PUBLIC-KEY"
    # Приватные ключи не должны нигде сохраняться.
    assert all("PRIVATE-KEY-DATA" not in str(v) for v in result.targets.values())
    assert all("PSK-DATA" not in str(v) for v in result.targets.values())
    assert result.warnings


def test_amnezia_conf():
    result = import_config(AMNEZIA_CONF, name="amnezia.conf")
    assert result.protocol == "amnezia"
    assert result.targets["amnezia_server"] == "2001:db8::1"
    assert result.targets["amnezia_port"] == 55555
    assert any("AmneziaWG" in note for note in result.notes)


def test_openvpn_conf():
    result = import_config(OVPN, name="client.ovpn")
    assert result.protocol == "openvpn"
    assert result.targets["openvpn_server"] == "vpn.example.org"
    assert result.targets["openvpn_port"] == 443
    assert result.targets["openvpn_proto"] == "tcp"


def test_shadowsocks_uri():
    result = import_config(SS_URI)
    assert result.protocol == "shadowsocks"
    assert result.targets["shadowsocks_server"] == "1.2.3.4"
    assert result.targets["shadowsocks_port"] == 8388
    assert result.targets["shadowsocks_method"] == "aes-256-gcm"


def test_shadowsocks_uri_legacy_base64():
    legacy = "ss://" + base64.b64encode(b"aes-128-gcm:pw@5.6.7.8:1080").decode()
    result = import_config(legacy)
    assert result.targets["shadowsocks_server"] == "5.6.7.8"
    assert result.targets["shadowsocks_port"] == 1080


def test_vless_uri_reality():
    result = import_config(VLESS_URI)
    assert result.protocol == "xray"
    assert result.targets["xray_server"] == "1.2.3.4"
    assert result.targets["xray_port"] == 443
    assert result.targets["xray_reality_sni"] == "www.microsoft.com"
    assert any("pbk" in w for w in result.warnings)


def test_trojan_uri():
    result = import_config(TROJAN_URI)
    assert result.targets["xray_server"] == "5.6.7.8"
    assert result.targets["xray_reality_sni"] == "cover.example"


def test_vmess_uri():
    result = import_config(VMESS_URI)
    assert result.targets["xray_server"] == "9.9.9.9"
    assert result.targets["xray_port"] == 8443
    assert result.targets["xray_reality_sni"] == "cdn.example.com"


def test_xray_json_vless():
    result = import_config(XRAY_JSON, name="config.json")
    assert result.protocol == "xray"
    assert result.targets["xray_server"] == "10.0.0.1"
    assert result.targets["xray_port"] == 8443
    assert result.targets["xray_reality_sni"] == "www.microsoft.com"


def test_shadowsocks_json():
    result = import_config(SS_JSON, name="ss.json")
    assert result.protocol == "shadowsocks"
    assert result.targets["shadowsocks_method"] == "chacha20-ietf-poly1305"


def test_unknown_format_rejected():
    with pytest.raises(ValueError):
        import_config("совершенно произвольный текст", name="notes.txt")


def _read_targets(config) -> dict:
    path = config.config_dir / "secrets.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("targets", {})


def test_cli_import_file(tmp_path, config_factory, capsys):
    config = config_factory()
    conf = Path(tmp_path) / "wg0.conf"
    conf.write_text(WG_CONF, encoding="utf-8")

    code = main(["--config-dir", str(config.config_dir), "config", "import", str(conf)])
    assert code == 0
    targets = _read_targets(config)
    assert targets["wireguard_server"] == "vpn.example.com"
    assert targets["wireguard_port"] == 51820
    assert "SERVER-PUBLIC-KEY" in capsys.readouterr().out


def test_cli_import_dry_run_does_not_save(tmp_path, config_factory):
    config = config_factory()
    conf = Path(tmp_path) / "wg0.conf"
    conf.write_text(WG_CONF, encoding="utf-8")

    code = main(
        [
            "--config-dir",
            str(config.config_dir),
            "config",
            "import",
            str(conf),
            "--dry-run",
        ]
    )
    assert code == 0
    assert _read_targets(config)["wireguard_server"] == ""


def test_cli_import_uri(config_factory):
    config = config_factory()
    code = main(
        ["--config-dir", str(config.config_dir), "config", "import", SS_URI, "--dry-run"]
    )
    assert code == 0


def test_cli_import_bad_file(config_factory, capsys):
    config = config_factory()
    code = main(
        [
            "--config-dir",
            str(config.config_dir),
            "config",
            "import",
            "C:/definitely/missing.conf",
        ]
    )
    assert code == 1
    assert "не найден" in capsys.readouterr().err
