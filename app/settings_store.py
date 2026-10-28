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


PROVIDER_KEY_NAMES = ('gemini', 'openai', 'anthropic')


def _ensure_provider_keys():
    with connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS owner_provider_keys (owner_id INTEGER NOT NULL, provider TEXT NOT NULL, api_key TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (owner_id, provider))')


def list_provider_keys(owner_id: int) -> dict:
    """Return {provider: api_key} the owner saved in the console settings (opt-in fallbacks)."""
    _ensure_provider_keys()
    with connect() as conn:
        rows = conn.execute('SELECT provider, api_key FROM owner_provider_keys WHERE owner_id=?', (owner_id,)).fetchall()
    return {r['provider']: r['api_key'] for r in rows if r['provider'] in PROVIDER_KEY_NAMES}


def provider_key_status(owner_id: int) -> dict:
    """Masked status only: {provider: {'saved': True, 'hint': 'abcd...'}}."""
    return {p: {'saved': True, 'hint': '...' + k[-4:] if len(k) > 4 else '****'} for p, k in list_provider_keys(owner_id).items()}


def save_provider_key(owner_id: int, provider: str, api_key: str) -> None:
    if provider not in PROVIDER_KEY_NAMES: raise ValueError('unsupported provider')
    _ensure_provider_keys()
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute('DELETE FROM owner_provider_keys WHERE owner_id=? AND provider=?', (owner_id, provider))
        conn.execute('INSERT INTO owner_provider_keys (owner_id,provider,api_key,updated_at) VALUES (?,?,?,?)', (owner_id, provider, api_key.strip(), now))
        conn.commit()


def delete_provider_key(owner_id: int, provider: str) -> None:
    _ensure_provider_keys()
    with connect() as conn:
        conn.execute('DELETE FROM owner_provider_keys WHERE owner_id=? AND provider=?', (owner_id, provider))
        conn.commit()
