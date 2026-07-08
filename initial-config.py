#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "paho-mqtt>=2.1.0",
# ]
# ///

from __future__ import annotations

import argparse
import binascii
import json
import socket
import ssl
import struct
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

MAGIC_PACKET = b"\xef\xcc\x3b\x29\x00"
AUTH_PACKET_TYPE = 0xF0
_OP_LEGACY_SERVER_CONNECT = getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)


@dataclass(frozen=True)
class ProvisionConfig:
    blid: str
    password: str
    host: str
    port: int
    ca_cert: Path
    wifi_ssid: str
    wifi_password: str
    wifi_sec: int
    timezone: str
    country: str
    ntp_hosts: str
    command_delay: float
    post_password_delay: float

    @classmethod
    def load(cls, path: Path) -> ProvisionConfig:
        data = tomllib.loads(path.read_text())
        robot = data["robot"]
        wifi = data["wifi"]
        locale = data.get("locale", {})
        timing = data.get("provisioning", {})
        base = path.parent

        cfg = cls(
            blid=str(robot["blid"]),
            password=str(robot["password"]),
            host=str(robot.get("host", "192.168.10.1")),
            port=int(robot.get("port", 8883)),
            ca_cert=(base / robot.get("ca_cert", "robot-ca.pem")).resolve(),
            wifi_ssid=str(wifi["ssid"]),
            wifi_password=str(wifi["password"]),
            wifi_sec=int(wifi.get("sec", 7)),
            timezone=str(locale.get("timezone", "America/Chicago")),
            country=str(locale.get("country", "US")),
            ntp_hosts=str(
                locale.get(
                    "ntp_hosts",
                    "0.pool.ntp.org 1.pool.ntp.org 2.pool.ntp.org 3.pool.ntp.org",
                )
            ),
            command_delay=float(timing.get("command_delay_seconds", 1.0)),
            post_password_delay=float(timing.get("post_password_delay_seconds", 2.0)),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not self.blid.isdigit():
            raise ValueError("robot.blid must be numeric (Roomba soft-AP SSID)")
        if not self.password.startswith(":1:"):
            raise ValueError("robot.password must use :1:<timestamp>:<secret> format")
        if not self.ca_cert.is_file():
            raise FileNotFoundError(f"CA certificate not found: {self.ca_cert}")


def roomba_ssl_context(*, ca_cert: Path | None = None) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # SECLEVEL=0: Roomba presents SHA1-signed certs that OpenSSL 3 rejects otherwise.
    ctx.set_ciphers("DEFAULT:!DH:@SECLEVEL=0")
    ctx.options |= _OP_LEGACY_SERVER_CONNECT
    if ca_cert is not None:
        ctx.load_verify_locations(ca_cert)
    return ctx


def build_auth_packet(password: str) -> bytes:
    payload = MAGIC_PACKET + password.encode("ascii")
    return bytes([AUTH_PACKET_TYPE, len(payload)]) + payload


def read_auth_response(sock: ssl.SSLSocket, payload_len: int) -> bytes:
    data = b""
    expected = payload_len + 2  # type byte + length byte + payload

    while len(data) < expected:
        chunk = sock.recv(1024)
        if not chunk:
            break
        print(f"recv {len(chunk)} bytes: {binascii.hexlify(chunk).decode()}", flush=True)
        data += chunk
        if len(data) >= 2:
            expected = struct.unpack("B", data[1:2])[0] + 2

    return data


def ssid_hex(ssid: str) -> str:
    return ssid.encode("utf-8").hex()


def mqtt_commands(cfg: ProvisionConfig) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("delta", {"state": {"timezone": cfg.timezone}}),
        ("wifictl", {"state": {"ntphosts": cfg.ntp_hosts}}),
        ("delta", {"state": {"country": cfg.country}}),
        (
            "wifictl",
            {
                "state": {
                    "wlcfg": {
                        "pass": cfg.wifi_password,
                        "sec": cfg.wifi_sec,
                        "ssid": ssid_hex(cfg.wifi_ssid),
                    }
                }
            },
        ),
        ("wifictl", {"state": {"chkssid": True}}),
        ("wifictl", {"state": {"wactivate": True}}),
        ("wifictl", {"state": {"get": "netinfo"}}),
        ("wifictl", {"state": {"uap": False}}),
    ]


def push_password(cfg: ProvisionConfig) -> None:
    packet = build_auth_packet(cfg.password)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)

    with roomba_ssl_context().wrap_socket(sock, server_hostname=cfg.host) as tls:
        try:
            tls.connect((cfg.host, cfg.port))
        except OSError as exc:
            sys.exit(f"password push: connect failed: {exc}")

        tls.send(packet)
        response = read_auth_response(tls, len(packet) - 2)

    print(f"auth response ({len(response)} bytes): {binascii.hexlify(response).decode()}")

    if len(response) <= 7 or response[:1] != bytes([AUTH_PACKET_TYPE]):
        sys.exit(
            "password push failed: expected MQTT auth echo starting with f0. "
            "Is the robot in provisioning mode (Home+Spot) and are you on its AP?"
        )

    time.sleep(cfg.post_password_delay)


def provision_wifi(cfg: ProvisionConfig) -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, cfg.blid)
    client.tls_set_context(roomba_ssl_context(ca_cert=cfg.ca_cert))
    client.tls_insecure_set(True)
    client.username_pw_set(cfg.blid, cfg.password)

    try:
        client.connect(cfg.host, cfg.port, keepalive=60)
    except OSError as exc:
        sys.exit(f"mqtt connect failed: {exc}")

    time.sleep(1)
    for topic, payload in mqtt_commands(cfg):
        body = json.dumps(payload, separators=(",", ":"))
        print(f"publish {topic} {body}", flush=True)
        client.publish(topic, body)
        time.sleep(cfg.command_delay)

    client.disconnect()


def parse_args(argv: list[str]) -> argparse.Namespace:
    default_config = Path(__file__).resolve().parent / "provision.toml"
    parser = argparse.ArgumentParser(
        description="Provision Wi-Fi on a Roomba 900-series robot in soft-AP mode.",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=default_config,
        help=f"path to TOML config (default: {default_config.name})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv or sys.argv[1:])
    config_path = args.config.expanduser().resolve()

    if not config_path.is_file():
        example = config_path.parent / "provision.toml.example"
        hint = f"copy {example.name} to {config_path.name}" if example.is_file() else "create a config file"
        sys.exit(f"config not found: {config_path} ({hint})")

    try:
        cfg = ProvisionConfig.load(config_path)
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as exc:
        sys.exit(f"invalid config: {exc}")

    push_password(cfg)
    provision_wifi(cfg)


if __name__ == "__main__":
    main()
