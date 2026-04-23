from __future__ import annotations

import typer

from wgh import bootstrap as _bootstrap
from wgh import peers as _peers

app = typer.Typer(
    help="WireGuard server bootstrapper + peer manager.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def bootstrap(
    endpoint_host: str = typer.Option(
        None, "--endpoint-host", help="Public hostname clients connect to."
    ),
    lan_cidr: str = typer.Option(
        None, "--lan-cidr", help="Internal LAN CIDR clients should reach."
    ),
    client_dns: str = typer.Option(
        None, "--client-dns", help="DNS server clients use while connected."
    ),
) -> None:
    """Install + configure WireGuard on this server. Idempotent."""
    _bootstrap.run(
        endpoint_host=endpoint_host,
        lan_cidr=lan_cidr,
        client_dns=client_dns,
    )


@app.command("add")
def add_cmd() -> None:
    """Interactively add a new peer. Emits .conf + terminal QR."""
    _peers.add_interactive()


@app.command("list")
def list_cmd() -> None:
    """List all peers (active and revoked)."""
    _peers.list_peers()


@app.command("show")
def show_cmd(
    label: str = typer.Argument(..., help="Peer name or <name>-<device> label.")
) -> None:
    """Re-print config + QR for an existing peer."""
    _peers.show_peer(label)


@app.command("remove")
def remove_cmd(
    label: str = typer.Argument(..., help="Peer name or <name>-<device> label.")
) -> None:
    """Revoke a peer (removes from wg0 and wipes its .conf)."""
    _peers.remove_peer(label)


@app.command()
def status() -> None:
    """Run `wg show` and print active tunnel state."""
    _peers.status()


if __name__ == "__main__":
    app()
