"""Runtime configuration. Values can be overridden by /etc/wireguard-helper/settings.toml."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

STATE_DIR = Path("/etc/wireguard-helper")
SETTINGS_FILE = STATE_DIR / "settings.toml"
DB_FILE = STATE_DIR / "peers.db"
CLIENTS_DIR = STATE_DIR / "clients"
WG_DIR = Path("/etc/wireguard")
WG_CONF = WG_DIR / "wg0.conf"
WG_IFACE = "wg0"


@dataclass
class Settings:
    endpoint_host: str = "vpn.example.com"
    listen_port: int = 51820
    tunnel_cidr: str = "10.8.0.0/24"
    server_tunnel_ip: str = "10.8.0.1"
    lan_cidr: str = "10.0.0.0/24"
    client_dns: str = "10.0.0.14"
    client_allowed_ips: list[str] = field(
        default_factory=lambda: ["10.0.0.0/24", "10.8.0.0/24"]
    )
    persistent_keepalive: int = 25

    @property
    def endpoint(self) -> str:
        return f"{self.endpoint_host}:{self.listen_port}"


def load() -> Settings:
    s = Settings()
    if SETTINGS_FILE.exists():
        data = tomllib.loads(SETTINGS_FILE.read_text())
        for k, v in data.items():
            if hasattr(s, k):
                setattr(s, k, v)
    return s


def write_default(overrides: dict | None = None) -> Settings:
    """Persist settings.toml so an admin can edit it later."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    s = Settings()
    if overrides:
        for k, v in overrides.items():
            if hasattr(s, k):
                setattr(s, k, v)
    lines = [
        f'endpoint_host = "{s.endpoint_host}"',
        f"listen_port = {s.listen_port}",
        f'tunnel_cidr = "{s.tunnel_cidr}"',
        f'server_tunnel_ip = "{s.server_tunnel_ip}"',
        f'lan_cidr = "{s.lan_cidr}"',
        f'client_dns = "{s.client_dns}"',
        "client_allowed_ips = ["
        + ", ".join(f'"{x}"' for x in s.client_allowed_ips)
        + "]",
        f"persistent_keepalive = {s.persistent_keepalive}",
    ]
    SETTINGS_FILE.write_text("\n".join(lines) + "\n")
    return s
