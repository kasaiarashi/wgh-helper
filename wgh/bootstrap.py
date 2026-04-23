"""One-shot server bootstrap. Idempotent — safe to re-run."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import typer

from wgh import config, db, wg


SYSCTL_FILE = Path("/etc/sysctl.d/99-wireguard-helper.conf")
SYSCTL_BODY = "net.ipv4.ip_forward=1\n"


def _info(msg: str) -> None:
    typer.secho(f"  • {msg}", fg=typer.colors.CYAN)


def _ok(msg: str) -> None:
    typer.secho(f"  ✓ {msg}", fg=typer.colors.GREEN)


def _warn(msg: str) -> None:
    typer.secho(f"  ! {msg}", fg=typer.colors.YELLOW)


def ensure_root() -> None:
    if os.geteuid() != 0:
        typer.secho("This command must be run as root (sudo).", fg=typer.colors.RED)
        raise typer.Exit(1)


def ensure_wireguard_installed() -> None:
    if shutil.which("wg") and shutil.which("wg-quick"):
        _ok("wireguard already installed")
        return
    _info("installing wireguard via apt")
    wg.run(["apt-get", "update"])
    wg.run(["apt-get", "install", "-y", "wireguard", "iptables"])
    _ok("wireguard installed")


def ensure_ip_forward() -> None:
    if SYSCTL_FILE.exists() and "net.ipv4.ip_forward=1" in SYSCTL_FILE.read_text():
        _ok("ip_forward already persisted")
    else:
        SYSCTL_FILE.write_text(SYSCTL_BODY)
        _ok(f"wrote {SYSCTL_FILE}")
    wg.run(["sysctl", "-w", "net.ipv4.ip_forward=1"])


def ensure_server_keys(settings: config.Settings) -> tuple[str, str]:
    """Ensure server private key exists. Return (private, public)."""
    priv_path = config.WG_DIR / "server_private.key"
    pub_path = config.WG_DIR / "server_public.key"
    config.WG_DIR.mkdir(parents=True, exist_ok=True)

    if priv_path.exists():
        priv = priv_path.read_text().strip()
        _ok("server keypair already exists")
    else:
        priv = wg.gen_private_key()
        priv_path.write_text(priv + "\n")
        priv_path.chmod(0o600)
        _ok("generated server keypair")

    pub = wg.derive_public_key(priv)
    pub_path.write_text(pub + "\n")
    pub_path.chmod(0o644)
    return priv, pub


def _extract_peers_block(existing: str) -> str:
    """Preserve existing [Peer] stanzas when we rewrite [Interface]."""
    if not existing:
        return ""
    lines = existing.splitlines()
    out: list[str] = []
    in_peer = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[Peer]"):
            in_peer = True
            out.append(line)
        elif stripped.startswith("[Interface]"):
            in_peer = False
        elif in_peer:
            out.append(line)
    return "\n".join(out).strip()


def write_wg_conf(settings: config.Settings, server_priv: str, nat_iface: str) -> None:
    existing = wg.read_server_conf()
    peers_block = _extract_peers_block(existing)
    body = wg.render_server_conf(settings, server_priv, nat_iface, peers_block)
    wg.write_server_conf(body)
    _ok(f"wrote {config.WG_CONF}")


def open_firewall(settings: config.Settings) -> None:
    """Open UDP listen port in UFW if it's active. Skip silently otherwise."""
    ufw = shutil.which("ufw")
    if not ufw:
        return
    status = wg.run(["ufw", "status"], check=False)
    if "Status: active" not in status:
        _info("ufw present but inactive — skipping firewall rule")
        return
    rule = f"{settings.listen_port}/udp"
    if rule in status:
        _ok(f"ufw already allows {rule}")
    else:
        wg.run(["ufw", "allow", rule])
        _ok(f"ufw allow {rule}")


def bring_up_interface() -> None:
    if wg.iface_up():
        wg.wg_quick_restart()
        _ok(f"restarted wg-quick@{config.WG_IFACE}")
    else:
        wg.wg_quick_up()
        _ok(f"enabled + started wg-quick@{config.WG_IFACE}")


def run(
    endpoint_host: str | None = None,
    lan_cidr: str | None = None,
    client_dns: str | None = None,
) -> None:
    ensure_root()
    typer.secho("Bootstrapping WireGuard server…", fg=typer.colors.BRIGHT_WHITE, bold=True)

    # Persist settings first so subsequent calls reload the same values.
    overrides: dict = {}
    if endpoint_host:
        overrides["endpoint_host"] = endpoint_host
    if lan_cidr:
        overrides["lan_cidr"] = lan_cidr
    if client_dns:
        overrides["client_dns"] = client_dns
    if not config.SETTINGS_FILE.exists():
        config.write_default(overrides)
        _ok(f"wrote {config.SETTINGS_FILE}")
    elif overrides:
        config.write_default({**vars(config.load()), **overrides})
        _ok(f"updated {config.SETTINGS_FILE}")
    else:
        _ok(f"settings already at {config.SETTINGS_FILE}")

    settings = config.load()

    db.init()
    _ok(f"initialised db at {config.DB_FILE}")

    config.CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
    config.CLIENTS_DIR.chmod(0o700)

    ensure_wireguard_installed()
    ensure_ip_forward()

    # Pick NAT interface: either the one holding the LAN IP used for routing,
    # or the default-route interface as a fallback.
    try:
        nat_iface = wg.default_route_interface()
    except wg.WgError:
        nat_iface = wg.detect_lan_interface("10.0.0.14")
    _ok(f"NAT egress interface: {nat_iface}")

    server_priv, server_pub = ensure_server_keys(settings)
    write_wg_conf(settings, server_priv, nat_iface)
    open_firewall(settings)
    bring_up_interface()

    typer.secho("\nBootstrap complete.", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  Server public key: {server_pub}")
    typer.echo(f"  Endpoint:          {settings.endpoint}")
    typer.echo(f"  Tunnel subnet:     {settings.tunnel_cidr}")
    typer.echo(f"  Clients will route: {', '.join(settings.client_allowed_ips)}")
    typer.echo("\nNext: run `wgh add` to create a peer.")
