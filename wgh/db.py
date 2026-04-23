from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from wgh.config import DB_FILE, STATE_DIR


SCHEMA = """
CREATE TABLE IF NOT EXISTS peers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    device          TEXT NOT NULL,
    tunnel_ip       TEXT NOT NULL UNIQUE,
    public_key      TEXT NOT NULL UNIQUE,
    private_key     TEXT NOT NULL,
    preshared_key   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    revoked_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_peers_name ON peers(name);
"""


@dataclass
class Peer:
    id: int
    name: str
    device: str
    tunnel_ip: str
    public_key: str
    private_key: str
    preshared_key: str
    created_at: str
    revoked_at: str | None

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    @property
    def label(self) -> str:
        return f"{self.name}-{self.device}"


def _connect() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def cursor():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row_to_peer(row: sqlite3.Row) -> Peer:
    return Peer(**{k: row[k] for k in row.keys()})


def insert_peer(
    name: str,
    device: str,
    tunnel_ip: str,
    public_key: str,
    private_key: str,
    preshared_key: str,
) -> Peer:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with cursor() as conn:
        cur = conn.execute(
            """INSERT INTO peers
               (name, device, tunnel_ip, public_key, private_key,
                preshared_key, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, device, tunnel_ip, public_key, private_key, preshared_key, now),
        )
        peer_id = cur.lastrowid
        row = conn.execute("SELECT * FROM peers WHERE id=?", (peer_id,)).fetchone()
        return _row_to_peer(row)


def revoke_peer(peer_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with cursor() as conn:
        conn.execute("UPDATE peers SET revoked_at=? WHERE id=?", (now, peer_id))


def delete_peer(peer_id: int) -> None:
    with cursor() as conn:
        conn.execute("DELETE FROM peers WHERE id=?", (peer_id,))


def list_active() -> list[Peer]:
    with cursor() as conn:
        rows = conn.execute(
            "SELECT * FROM peers WHERE revoked_at IS NULL ORDER BY id"
        ).fetchall()
        return [_row_to_peer(r) for r in rows]


def list_all() -> list[Peer]:
    with cursor() as conn:
        rows = conn.execute("SELECT * FROM peers ORDER BY id").fetchall()
        return [_row_to_peer(r) for r in rows]


def find_by_exact_label(label: str) -> Peer | None:
    """Match only on exact '<name>-<device>' label. Returns first hit in any state."""
    with cursor() as conn:
        rows = conn.execute("SELECT * FROM peers ORDER BY id").fetchall()
        for r in rows:
            p = _row_to_peer(r)
            if p.label == label:
                return p
        return None


def find_active_by_name(name: str) -> list[Peer]:
    """Return all active peers with the given user name."""
    with cursor() as conn:
        rows = conn.execute(
            "SELECT * FROM peers WHERE name=? AND revoked_at IS NULL ORDER BY id",
            (name,),
        ).fetchall()
        return [_row_to_peer(r) for r in rows]


def used_tunnel_ips() -> set[str]:
    with cursor() as conn:
        rows = conn.execute(
            "SELECT tunnel_ip FROM peers WHERE revoked_at IS NULL"
        ).fetchall()
        return {r["tunnel_ip"] for r in rows}
