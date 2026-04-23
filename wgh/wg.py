"""Wrappers around the `wg` / `wg-quick` / `ip` / `systemctl` CLIs."""
from __future__ import annotations

import ipaddress
import re
import subprocess
from textwrap import dedent

from wgh.config import WG_CONF, WG_DIR, WG_IFACE, Settings


class WgError(RuntimeError):
    pass


def run(cmd: list[str], *, check: bool = True, input_: str | None = None) -> str:
    try:
        result = subprocess.run(
            cmd,
            check=check,
            capture_output=True,
            text=True,
            input=input_,
        )
    except FileNotFoundError as e:
        raise WgError(f"Command not found: {cmd[0]}") from e
    if check and result.returncode != 0:
        raise WgError(
            f"Command failed: {' '.join(cmd)}\nstderr: {result.stderr.strip()}"
        )
    return result.stdout


def gen_private_key() -> str:
    return run(["wg", "genkey"]).strip()


def derive_public_key(private_key: str) -> str:
    return run(["wg", "pubkey"], input_=private_key + "\n").strip()


def gen_preshared_key() -> str:
    return run(["wg", "genpsk"]).strip()


def detect_lan_interface(server_lan_ip: str) -> str:
    """Find interface whose IPv4 matches given server IP."""
    out = run(["ip", "-o", "-4", "addr", "show"])
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[3].split("/")[0] == server_lan_ip:
            return parts[1]
    raise WgError(f"No interface has IP {server_lan_ip}")


def default_route_interface() -> str:
    out = run(["ip", "-o", "-4", "route", "show", "default"])
    m = re.search(r"\bdev\s+(\S+)", out)
    if not m:
        raise WgError("Cannot determine default route interface")
    return m.group(1)


def next_tunnel_ip(settings: Settings, used: set[str]) -> str:
    net = ipaddress.ip_network(settings.tunnel_cidr, strict=False)
    server_ip = ipaddress.ip_address(settings.server_tunnel_ip)
    for host in net.hosts():
        if host == server_ip:
            continue
        if str(host) in used:
            continue
        return str(host)
    raise WgError(f"Tunnel subnet {settings.tunnel_cidr} exhausted")


def render_server_conf(
    settings: Settings,
    server_private_key: str,
    nat_iface: str,
    peers_block: str = "",
) -> str:
    body = dedent(
        f"""\
        # Managed by wireguard-helper. Peers appended below.
        [Interface]
        Address = {settings.server_tunnel_ip}/{settings.tunnel_cidr.split('/')[1]}
        ListenPort = {settings.listen_port}
        PrivateKey = {server_private_key}
        PostUp   = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -o {nat_iface} -j MASQUERADE
        PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -o {nat_iface} -j MASQUERADE
        """
    )
    if peers_block:
        body += "\n" + peers_block.rstrip() + "\n"
    return body


def render_peer_block(name: str, device: str, public_key: str, preshared_key: str,
                     tunnel_ip: str) -> str:
    return dedent(
        f"""\
        [Peer]
        # {name} / {device}
        PublicKey = {public_key}
        PresharedKey = {preshared_key}
        AllowedIPs = {tunnel_ip}/32
        """
    )


def render_client_conf(
    settings: Settings,
    peer_private_key: str,
    peer_tunnel_ip: str,
    preshared_key: str,
    server_public_key: str,
) -> str:
    allowed = ", ".join(settings.client_allowed_ips)
    return dedent(
        f"""\
        [Interface]
        PrivateKey = {peer_private_key}
        Address = {peer_tunnel_ip}/32
        DNS = {settings.client_dns}

        [Peer]
        PublicKey = {server_public_key}
        PresharedKey = {preshared_key}
        Endpoint = {settings.endpoint}
        AllowedIPs = {allowed}
        PersistentKeepalive = {settings.persistent_keepalive}
        """
    )


def write_server_conf(contents: str) -> None:
    WG_DIR.mkdir(parents=True, exist_ok=True)
    WG_CONF.write_text(contents)
    WG_CONF.chmod(0o600)


def read_server_conf() -> str:
    return WG_CONF.read_text() if WG_CONF.exists() else ""


def server_private_key_from_conf() -> str:
    for line in read_server_conf().splitlines():
        line = line.strip()
        if line.startswith("PrivateKey"):
            return line.split("=", 1)[1].strip().lstrip("= ").strip()
    raise WgError("Server PrivateKey not found in wg0.conf")


def server_public_key() -> str:
    return derive_public_key(server_private_key_from_conf())


def syncconf() -> None:
    """Apply wg0.conf to the running interface without disrupting peers."""
    run(["bash", "-c", f"wg syncconf {WG_IFACE} <(wg-quick strip {WG_IFACE})"])


def wg_quick_up() -> None:
    run(["systemctl", "enable", "--now", f"wg-quick@{WG_IFACE}"])


def wg_quick_restart() -> None:
    run(["systemctl", "restart", f"wg-quick@{WG_IFACE}"])


def wg_show() -> str:
    return run(["wg", "show"], check=False)


def iface_up() -> bool:
    out = run(["ip", "-o", "link", "show", WG_IFACE], check=False)
    return WG_IFACE in out
