"""Owner-scoped Phoenix settings (agent prompt, step budget, browser options) persisted in the Phoenix DB."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from app.db import connect

DEFAULTS = {
    'agent': {'system_prompt': '', 'max_steps': None},
    'browser': {'headless': None, 'viewport_width': 1280, 'viewport_height': 800, 'user_agent': '', 'slow_mo_ms': 0},
}


def _ensure():
    with connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS owner_settings (owner_id INTEGER PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL)')


def _merge(data: dict) -> dict:
    out = {'agent': dict(DEFAULTS['agent']), 'browser': dict(DEFAULTS['browser'])}
    for section in out:
        if isinstance(data.get(section), dict):
            out[section].update({k: v for k, v in data[section].items() if k in out[section]})
    return out


def get_settings(owner_id: int) -> dict:
    _ensure()
    with connect() as conn:
        row = conn.execute('SELECT data, updated_at FROM owner_settings WHERE owner_id=?', (owner_id,)).fetchone()
    if not row:
        return {**_merge({}), 'updated_at': None}
    return {**_merge(json.loads(row['data'])), 'updated_at': row['updated_at']}


def save_settings(owner_id: int, data: dict) -> dict:
    _ensure()
    clean = _merge(data)
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute('DELETE FROM owner_settings WHERE owner_id=?', (owner_id,))
        conn.execute('INSERT INTO owner_settings (owner_id,data,updated_at) VALUES (?,?,?)', (owner_id, json.dumps(clean), now))
        conn.commit()
    return {**clean, 'updated_at': now}
