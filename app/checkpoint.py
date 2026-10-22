"""Versioned, redacted task checkpoints.

Format version 2 is a small allowlisted envelope stored in the existing
``tasks.checkpoint`` column. Anything not on the allowlist (passwords, generated
credentials, cookies, OTPs, verification links, form values, page excerpts) is
dropped before the checkpoint is written, and again whenever one is read.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit, urlunsplit

CHECKPOINT_VERSION = 2
MAX_BYTES = 32_000
MAX_HISTORY = 60
PHASES = frozenset({'started', 'challenge', 'pending', 'error', 'verified', 'done'})
# Actions whose outcome is external and cannot be safely repeated after an interruption.
SIDE_EFFECT_ACTIONS = frozenset({
    'click', 'fill', 'fill_form', 'fill_generated', 'select', 'press',
    'mailbox_prepare_email', 'mailbox_fill_verification_code', 'mailbox_open_verification_link',
    'mailbox_create_alias', 'mailbox_update_alias', 'mailbox_delete_alias',
})
_ALLOWED_KEYS = frozenset({
    'version', 'phase', 'step', 'action', 'target', 'url', 'title', 'verified', 'history',
    'proxy_mode', 'proxy_identifier', 'mailbox_email_attempted', 'mailbox_alias_id',
    'generated_credentials_used', 'registration', 'challenge', 'error_type', 'engine',
    'summary', 'resume_pending',
})
_SAFE_TOKEN = re.compile(r'^[A-Za-z0-9_:.-]{1,64}$')


def scrub_url(value) -> str | None:
    """Keep scheme, host, port and path. Drop userinfo, query and fragment (tokens often live there)."""
    if not isinstance(value, str) or not value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return None
    netloc = parts.hostname + (f':{parts.port}' if parts.port else '')
    return urlunsplit((parts.scheme, netloc, parts.path or '/', '', ''))


def _text(value, limit: int) -> str | None:
    if value is None:
        return None
    return str(value)[:limit]


def _history_entry(item) -> dict | None:
    if not isinstance(item, dict):
        return None
    name = item.get('action') or item.get('tool')
    if not name:
        return None
    # Form values, action results and error text are never kept (they can carry OTPs or personal data).
    # User chat text is never kept: people paste passwords and codes into task chat.
    return {'action': _text(name, 40), 'target': _text(item.get('target'), 120), 'ok': 'error' not in item}


def clean(state: dict) -> dict:
    """Return an allowlisted, redacted copy of a checkpoint dict."""
    raw = {key: value for key, value in dict(state or {}).items() if key in _ALLOWED_KEYS}
    action = _text(raw.get('action'), 40)
    out: dict = {'version': CHECKPOINT_VERSION}
    phase = raw.get('phase') or 'verified'
    out['phase'] = phase if phase in PHASES else 'verified'
    out['step'] = int(raw['step']) if isinstance(raw.get('step'), int) and raw['step'] >= 0 else 0
    out['action'] = action
    out['target'] = _text(raw.get('target'), 200)
    if action == 'mailbox_open_verification_link':
        out['url'] = None  # verification URLs are single-use tokens; never persist them
    else:
        out['url'] = scrub_url(raw.get('url'))
    out['title'] = _text(raw.get('title'), 200)
    out['verified'] = bool(raw.get('verified', False))
    history = raw.get('history') if isinstance(raw.get('history'), list) else []
    out['history'] = [entry for entry in (_history_entry(item) for item in history[-MAX_HISTORY:]) if entry]
    out['proxy_mode'] = _text(raw.get('proxy_mode'), 20)
    out['proxy_identifier'] = _text(raw.get('proxy_identifier'), 200)
    out['mailbox_email_attempted'] = bool(raw.get('mailbox_email_attempted', False))
    alias = raw.get('mailbox_alias_id')
    out['mailbox_alias_id'] = str(alias) if alias is not None and _SAFE_TOKEN.match(str(alias)) else None
    out['generated_credentials_used'] = bool(raw.get('generated_credentials_used', False))
    registration = raw.get('registration')
    if isinstance(registration, dict):
        out['registration'] = {'state': _text(registration.get('state'), 60), 'url': scrub_url(registration.get('url'))}
    else:
        out['registration'] = None
    challenge = raw.get('challenge')
    if isinstance(challenge, dict):
        challenge = challenge.get('type')
    out['challenge'] = _text(challenge, 60)
    out['error_type'] = _text(raw.get('error_type'), 60)
    out['engine'] = _text(raw.get('engine'), 40)
    out['summary'] = _text(raw.get('summary'), 500)
    out['resume_pending'] = bool(raw.get('resume_pending', False))
    return out


def dumps(state: dict) -> str:
    """Serialize a redacted checkpoint, trimming old history if it would exceed the size bound."""
    data = clean(state)
    text = json.dumps(data, ensure_ascii=False, sort_keys=True)
    while len(text.encode('utf-8')) > MAX_BYTES and data['history']:
        data['history'] = data['history'][len(data['history']) // 2 + 1:]
        text = json.dumps(data, ensure_ascii=False, sort_keys=True)
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('checkpoint exceeds the size bound even after trimming history')
    return text


def decode(raw) -> tuple[str, dict | None]:
    """Read a stored checkpoint. Returns (status, state) with status in
    ok, missing, invalid, legacy or unsupported_version. Only 'ok' carries state."""
    if raw is None or raw == '':
        return 'missing', None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return 'invalid', None
    if not isinstance(data, dict):
        return 'invalid', None
    if 'version' not in data:
        return 'legacy', None  # pre-v2 rows: last action only, no history, cannot be safely resumed
    version = data.get('version')
    if not isinstance(version, int) or isinstance(version, bool):
        return 'invalid', None
    if version > CHECKPOINT_VERSION:
        return 'unsupported_version', None
    if version != CHECKPOINT_VERSION:
        return 'legacy', None
    if not isinstance(data.get('step'), int) or data.get('phase') not in PHASES:
        return 'invalid', None
    if not isinstance(data.get('history', []), list):
        return 'invalid', None
    return 'ok', clean(data)


def resume_decision(state: dict, engine: str = 'phoenix') -> tuple[bool, str | None]:
    """Decide whether a checkpoint can be continued without repeating side effects."""
    if (engine or '').lower() != 'phoenix' or state.get('engine') == 'browser_use':
        return False, 'the browser_use engine cannot resume from a Phoenix checkpoint'
    phase = state.get('phase')
    action = state.get('action')
    if phase == 'done':
        return False, ('the task had reached its final step but the completed result was not saved; '
                       'review the logs before retrying')
    if phase in {'pending', 'error'} and action in SIDE_EFFECT_ACTIONS:
        target = state.get('target') or 'the page'
        return False, (f'the outcome of {action} on {target} was not confirmed before the interruption; '
                       'it was not repeated. Inspect the site, then continue manually or retry')
    if state.get('generated_credentials_used'):
        return False, ('a generated credential was created in this run and cannot be restored after a restart; '
                       'the run was not resumed. Inspect the site before retrying')
    if state.get('step', 0) > 0 and not state.get('url'):
        return False, 'no restorable page URL was recorded at the last checkpoint'
    return True, None
