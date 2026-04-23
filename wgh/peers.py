"""Peer lifecycle: add, list, show, revoke."""
from __future__ import annotations

import os
import re
from pathlib import Path

import questionary
import typer

from wgh import bootstrap, config, db, qr, wg


SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}[a-z0-9]$")


def _require_bootstrapped() -> config.Settings:
    if not config.WG_CONF.exists():
        typer.secho(
            "Server not bootstrapped yet. Run `sudo wgh bootstrap` first.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)
    return config.load()


def _slugify(raw: str) -> str:
    s = raw.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def _rewrite_server_conf(settings: config.Settings) -> None:
    """Rebuild wg0.conf [Interface] + all active peers from the db."""
    # Preserve whatever PostUp/PostDown / NAT iface choice bootstrap picked:
    # we only rewrite [Peer] stanzas, keeping the existing [Interface] block.
    existing = wg.read_server_conf()
    if not existing:
        raise wg.WgError("wg0.conf missing — run bootstrap first")

    # Split out the [Interface] section (keep it verbatim).
    interface_lines: list[str] = []
    in_peer = False
    for line in existing.splitlines():
        if line.strip().startswith("[Peer]"):
            in_peer = True
            continue
        if line.strip().startswith("[Interface]"):
            in_peer = False
        if not in_peer:
            interface_lines.append(line)

    peer_blocks = [
        wg.render_peer_block(
            p.name, p.device, p.public_key, p.preshared_key, p.tunnel_ip
        )
        for p in db.list_active()
    ]
    body = "\n".join(interface_lines).rstrip() + "\n"
    if peer_blocks:
        body += "\n" + "\n".join(peer_blocks)
    wg.write_server_conf(body)


def _apply_live() -> None:
    """Push wg0.conf to the running interface without dropping existing peers."""
    if wg.iface_up():
        wg.syncconf()


def _prompt_peer_fields() -> tuple[str, str]:
    while True:
        raw_name = questionary.text(
            "User name (e.g. 'alice'):", validate=lambda v: bool(v.strip())
        ).ask()
        if raw_name is None:
            raise typer.Abort()
        name = _slugify(raw_name)
        if SAFE_NAME.match(name):
            break
        typer.secho(
            f"  '{name}' not a valid slug. Use 2-32 chars, letters/digits/hyphen.",
            fg=typer.colors.YELLOW,
        )
    raw_device = questionary.text(
        "Device label (e.g. 'laptop', 'iphone'):",
        default="laptop",
        validate=lambda v: bool(v.strip()),
    ).ask()
    if raw_device is None:
        raise typer.Abort()
    device = _slugify(raw_device)
    return name, device


def add_interactive() -> None:
    bootstrap.ensure_root()
    settings = _require_bootstrapped()
    db.init()

    name, device = _prompt_peer_fields()
    label = f"{name}-{device}"

    if db.find_by_exact_label(label):
        typer.secho(
            f"Peer '{label}' already exists. Remove it first with `wgh remove {label}`.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    tunnel_ip = wg.next_tunnel_ip(settings, db.used_tunnel_ips())
    priv = wg.gen_private_key()
    pub = wg.derive_public_key(priv)
    psk = wg.gen_preshared_key()

    peer = db.insert_peer(
        name=name,
        device=device,
        tunnel_ip=tunnel_ip,
        public_key=pub,
        private_key=priv,
        preshared_key=psk,
    )

    _rewrite_server_conf(settings)
    _apply_live()

    _emit(peer, settings)
    typer.secho(f"\nAdded peer {peer.label} @ {peer.tunnel_ip}", fg=typer.colors.GREEN, bold=True)


def _emit(peer: db.Peer, settings: config.Settings) -> None:
    server_pub = wg.server_public_key()
    client_conf = wg.render_client_conf(
        settings=settings,
        peer_private_key=peer.private_key,
        peer_tunnel_ip=peer.tunnel_ip,
        preshared_key=peer.preshared_key,
        server_public_key=server_pub,
    )
    config.CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
    config.CLIENTS_DIR.chmod(0o700)
    conf_path = config.CLIENTS_DIR / f"{peer.label}.conf"
    conf_path.write_text(client_conf)
    conf_path.chmod(0o600)

    typer.secho(f"\nClient config: {conf_path}", fg=typer.colors.BRIGHT_WHITE)
    typer.echo("  Windows: import this .conf in WireGuard app.")
    typer.echo("  iOS/Android: scan the QR below in the WireGuard app.\n")
    typer.echo(qr.render_terminal(client_conf))
    typer.echo("  (If the QR looks squashed, widen your terminal window.)\n")
    typer.echo("To view or copy the raw config from this shell:")
    typer.secho(f"  sudo cat {conf_path}", fg=typer.colors.BRIGHT_BLUE)
    typer.echo("To copy it to your own laptop over SSH:")
    typer.secho(
        f"  scp user@server:{conf_path} ./{peer.label}.conf",
        fg=typer.colors.BRIGHT_BLUE,
    )


def list_peers() -> None:
    db.init()
    rows = db.list_all()
    if not rows:
        typer.echo("No peers yet. Run `wgh add` to create one.")
        return
    typer.echo(f"{'ID':<4}{'LABEL':<30}{'TUNNEL IP':<14}{'STATUS':<10}CREATED")
    for p in rows:
        status = "active" if p.active else "revoked"
        typer.echo(
            f"{p.id:<4}{p.label:<30}{p.tunnel_ip:<14}{status:<10}{p.created_at}"
        )


def _resolve(identifier: str) -> db.Peer:
    """Map a bare name or full '<name>-<device>' label to a single peer.

    - Exact label match wins (covers both active and revoked peers so we can
      surface a useful 'revoked' error instead of 'not found').
    - Otherwise, fall back to matching active peers by user name. If multiple
      active devices share the name, refuse and list them so the caller can
      disambiguate.
    """
    exact = db.find_by_exact_label(identifier)
    if exact:
        return exact

    matches = db.find_active_by_name(identifier)
    if not matches:
        typer.secho(f"No peer matches '{identifier}'.", fg=typer.colors.RED)
        raise typer.Exit(1)
    if len(matches) > 1:
        typer.secho(
            f"'{identifier}' matches {len(matches)} active peers. "
            "Specify the full label:",
            fg=typer.colors.RED,
        )
        for p in matches:
            typer.echo(f"  {p.label:<30} {p.tunnel_ip}")
        raise typer.Exit(1)
    return matches[0]


def show_peer(label: str) -> None:
    settings = _require_bootstrapped()
    db.init()
    peer = _resolve(label)
    if not peer.active:
        typer.secho(
            f"Peer '{peer.label}' is revoked — cannot show config.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)
    _emit(peer, settings)


def _disconnect_and_delete_conf(peer: db.Peer, settings: config.Settings) -> None:
    """Tear down the peer's wg0 membership and wipe its client .conf."""
    _rewrite_server_conf(settings)
    _apply_live()
    conf_path = config.CLIENTS_DIR / f"{peer.label}.conf"
    if conf_path.exists():
        conf_path.unlink()


def revoke_peer(label: str) -> None:
    """Soft-revoke: mark revoked in DB, drop from wg0, delete client .conf.

    The DB row stays for audit. The tunnel IP remains retired unless the row
    is later hard-deleted with `wgh remove`.
    """
    bootstrap.ensure_root()
    settings = _require_bootstrapped()
    db.init()
    peer = _resolve(label)
    if not peer.active:
        typer.secho(f"Peer '{peer.label}' already revoked.", fg=typer.colors.YELLOW)
        return
    if not questionary.confirm(
        f"Revoke peer {peer.label} ({peer.tunnel_ip})? "
        "(keeps an audit record; use `wgh remove` afterwards to free the IP)",
        default=False,
    ).ask():
        raise typer.Abort()

    db.revoke_peer(peer.id)
    _disconnect_and_delete_conf(peer, settings)
    typer.secho(f"Revoked {peer.label}.", fg=typer.colors.GREEN)


def remove_peer(label: str) -> None:
    """Hard-delete: wipe DB row and free the tunnel IP for reuse.

    If the peer is still active, disconnects it from wg0 first.
    """
    bootstrap.ensure_root()
    settings = _require_bootstrapped()
    db.init()
    peer = _resolve(label)

    prompt = (
        f"Permanently delete peer {peer.label} ({peer.tunnel_ip})? "
        "DB row wiped, IP returns to the pool. This cannot be undone."
    )
    if not questionary.confirm(prompt, default=False).ask():
        raise typer.Abort()

    if peer.active:
        db.revoke_peer(peer.id)
        _disconnect_and_delete_conf(peer, settings)

    db.delete_peer(peer.id)
    typer.secho(f"Removed {peer.label}.", fg=typer.colors.GREEN)


def status() -> None:
    typer.echo(wg.wg_show())
