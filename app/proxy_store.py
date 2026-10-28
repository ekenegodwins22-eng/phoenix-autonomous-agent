"""Proxy store: custom and scraped proxies per owner, with masked credentials.

Per-task proxy_id resolves to a row here at run time. Passwords are stored server-side and are
never returned by the API (only a mask). Local browser launches pass the proxy to Playwright;
remote CDP attach does not (see browser_use_adapter).
"""
from __future__ import annotations
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import urlparse
import httpx
from app.db import using_postgres
from app.task_store import _connect as _task_connect


def _connect():
    """Open the shared DB and make sure the proxies table exists (idempotent)."""
    conn = _task_connect()
    ensure_schema(conn)
    return conn

SCRAPE_SOURCES = {
    "http": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "socks5": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
}
SCRAPE_LIMIT = 25
_HOST_PORT = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}$")
_SCHEMES = {"http", "socks5"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn) -> None:
    id_col = "BIGSERIAL PRIMARY KEY" if using_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS proxies (id {id_col}, owner_id INTEGER NOT NULL, label TEXT NOT NULL, "
        "server TEXT NOT NULL, username TEXT, password TEXT, source TEXT NOT NULL DEFAULT 'custom', "
        "created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)"
    )


def mask(value: str | None) -> str | None:
    if not value:
        return None
    return value[:1] + "***" if len(value) <= 2 else value[:2] + "***"


def _public(row) -> dict:
    return {
        "id": row["id"],
        "label": row["label"],
        "server": row["server"],
        "username": row["username"] or None,
        "password": "********" if row["password"] else None,
        "source": row["source"],
        "created_at": row["created_at"],
        "active": bool(row["active"]),
    }


def validate_server(server: str) -> str:
    """Accept scheme://host:port with http or socks5 only. Credentials go in username/password."""
    server = (server or "").strip().rstrip("/")
    parsed = urlparse(server)
    if parsed.scheme not in _SCHEMES or not parsed.hostname or not parsed.port:
        raise ValueError("Proxy server must look like http://host:port or socks5://host:port")
    if parsed.username or parsed.password:
        raise ValueError("Put the username and password in their own fields, not in the server URL")
    return server


def list_proxies(owner_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM proxies WHERE owner_id=? ORDER BY id DESC", (owner_id,)).fetchall()
    return [_public(r) for r in rows]


def create_proxy(owner_id: int, *, label: str, server: str, username: str | None, password: str | None,
                 source: str = "custom") -> dict:
    server = validate_server(server)
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO proxies (owner_id,label,server,username,password,source,created_at,active) VALUES (?,?,?,?,?,?,?,1)",
            (owner_id, label.strip() or server, server, (username or None), (password or None), source, _now()),
        )
        conn.commit()
        new_id = cur.lastrowid if not using_postgres() else conn.execute(
            "SELECT id FROM proxies WHERE owner_id=? ORDER BY id DESC LIMIT 1", (owner_id,)).fetchone()["id"]
    return get_proxy(owner_id, new_id)


def get_proxy(owner_id: int, proxy_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM proxies WHERE id=? AND owner_id=?", (proxy_id, owner_id)).fetchone()
    return _public(row) if row else None


def update_proxy(owner_id: int, proxy_id: int, fields: dict) -> dict | None:
    allowed = {"label", "server", "username", "password", "active"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "server" in updates:
        updates["server"] = validate_server(updates["server"])
    if "active" in updates:
        updates["active"] = 1 if updates["active"] else 0
    if updates:
        cols = ", ".join(f"{k}=?" for k in updates)
        with _connect() as conn:
            conn.execute(f"UPDATE proxies SET {cols} WHERE id=? AND owner_id=?", (*updates.values(), proxy_id, owner_id))
            conn.commit()
    return get_proxy(owner_id, proxy_id)


def delete_proxy(owner_id: int, proxy_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM proxies WHERE id=? AND owner_id=?", (proxy_id, owner_id))
        conn.commit()
        return (cur.rowcount or 0) > 0


def resolve_for_run(owner_id: int, proxy_id: int) -> dict | None:
    """Full proxy dict (with password) for the runner. Never returned to the API."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM proxies WHERE id=? AND owner_id=? AND active=1", (proxy_id, owner_id)).fetchone()
    if not row:
        return None
    return {"server": row["server"], "username": row["username"] or None, "password": row["password"] or None,
            "identifier": row["server"]}


def parse_scraped(text: str, scheme: str = "http", limit: int = SCRAPE_LIMIT) -> list[dict]:
    """Pure parser: keep well-formed host:port lines, dedupe, cap at limit. No network."""
    out, seen = [], set()
    for line in text.splitlines():
        hp = line.strip()
        if not _HOST_PORT.match(hp):
            continue
        host, port = hp.rsplit(":", 1)
        if any(int(o) > 255 for o in host.split(".")) or not (1 <= int(port) <= 65535):
            continue
        if hp in seen:
            continue
        seen.add(hp)
        out.append({"server": f"{scheme}://{hp}"})
        if len(out) >= limit:
            break
    return out


async def scrape_free(owner_id: int, limit: int = SCRAPE_LIMIT) -> dict:
    """Fetch free public proxies (TheSpeedX lists), keep up to `limit` well-formed ones, save as scraper source."""
    seen: set[str] = set()
    candidates: list[tuple[str, str]] = []
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for scheme, url in SCRAPE_SOURCES.items():
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError:
                continue
            for line in resp.text.splitlines():
                hp = line.strip()
                if _HOST_PORT.match(hp) and hp not in seen:
                    seen.add(hp)
                    candidates.append((scheme, hp))
    added = []
    for scheme, hp in candidates:
        if len(added) >= limit:
            break
        host, port = hp.rsplit(":", 1)
        if not (1 <= int(port) <= 65535):
            continue
        server = f"{scheme}://{hp}"
        added.append(create_proxy(owner_id, label=f"scraped {scheme} {hp}", server=server,
                                  username=None, password=None, source="scraper"))
    return {"added": added, "count": len(added), "limit": limit}
