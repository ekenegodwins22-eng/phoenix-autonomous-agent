"""Durable task records for the single-service Phoenix worker."""
from __future__ import annotations
import json
import re
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from app.config import settings
from app.db import connect, using_postgres


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect():
    conn = connect()
    task_log_id = 'BIGSERIAL PRIMARY KEY' if using_postgres() else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    schema = """
    CREATE TABLE IF NOT EXISTS tasks (
      id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, owner_id INTEGER NOT NULL,
      instruction TEXT NOT NULL, start_url TEXT, timeout_seconds INTEGER,
      status TEXT NOT NULL, progress TEXT, human_help TEXT, waiting_details TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL, result TEXT, error TEXT,
      provider TEXT, model TEXT, recovery_count INTEGER NOT NULL DEFAULT 0,
      cancel_requested INTEGER NOT NULL DEFAULT 0, worker_token TEXT, checkpoint TEXT,
      proxy_mode TEXT NOT NULL DEFAULT 'direct', proxy_identifier TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_tasks_owner_updated ON tasks(owner_id, updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_tasks_status_updated ON tasks(status, updated_at);
    CREATE TABLE IF NOT EXISTS task_logs (
      id __TASK_LOG_ID__, task_id TEXT NOT NULL,
      created_at TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL,
      FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_task_logs_task_id ON task_logs(task_id, id);
    CREATE TABLE IF NOT EXISTS task_messages (
      id __TASK_MESSAGE_ID__, task_id TEXT NOT NULL,
      owner_id INTEGER NOT NULL, created_at TEXT NOT NULL,
      role TEXT NOT NULL CHECK(role IN ('user','agent','system')),
      content TEXT NOT NULL, consumed_at TEXT,
      FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_task_messages_task ON task_messages(task_id, id);
    """.replace('__TASK_LOG_ID__', task_log_id).replace('__TASK_MESSAGE_ID__', task_log_id)
    conn.executescript(schema)
    for column, definition in (("proxy_mode", "TEXT NOT NULL DEFAULT 'direct'"), ("proxy_identifier", "TEXT")):
        try:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} {definition}")
        except Exception:
            pass
    return conn


def _decode(row):
    if not row:
        return None
    item = dict(row)
    item['waiting_details'] = json.loads(item['waiting_details']) if item.get('waiting_details') else None
    item['cancel_requested'] = bool(item.get('cancel_requested'))
    return item


def _slug(instruction: str, task_id: str) -> str:
    words = re.sub(r'[^a-z0-9]+', '-', instruction.lower()).strip('-')[:32].strip('-') or 'task'
    return f'{words}-{task_id[:6]}'


def create_task(*, owner_id: int, instruction: str, start_url: str | None, timeout_seconds: int | None, provider: str, model: str | None, proxy_mode: str = 'direct', proxy_identifier: str | None = None) -> dict:
    task_id = secrets.token_hex(8)
    slug = _slug(instruction, task_id)
    now = _now()
    with _connect() as conn:
        conn.execute("INSERT INTO tasks (id,slug,owner_id,instruction,start_url,timeout_seconds,status,progress,created_at,updated_at,provider,model,proxy_mode,proxy_identifier) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (task_id,slug,owner_id,instruction,start_url,timeout_seconds,'queued','Queued; waiting for the execution slot.',now,now,provider,model,proxy_mode,proxy_identifier))
        conn.execute("INSERT INTO task_logs (task_id,created_at,level,message) VALUES (?,?,?,?)", (task_id,now,'info','Task accepted and queued.'))
        conn.commit()
    return get_task(task_id)


def get_task(task_ref: str, owner_id: int | None = None) -> dict | None:
    with _connect() as conn:
        if owner_id is None:
            row = conn.execute('SELECT * FROM tasks WHERE id=? OR slug=?', (task_ref,task_ref)).fetchone()
        else:
            row = conn.execute('SELECT * FROM tasks WHERE owner_id=? AND (id=? OR slug=?)', (owner_id,task_ref,task_ref)).fetchone()
    return _decode(row)


def list_tasks(owner_id: int, limit: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute('SELECT * FROM tasks WHERE owner_id=? ORDER BY updated_at DESC LIMIT ?', (owner_id,limit)).fetchall()
    return [_decode(row) for row in rows]


def get_logs(task_id: str, owner_id: int | None = None, limit: int = 200) -> list[dict]:
    with _connect() as conn:
        if owner_id is not None and not conn.execute('SELECT 1 FROM tasks WHERE id=? AND owner_id=?', (task_id,owner_id)).fetchone():
            return []
        rows = conn.execute('SELECT created_at,level,message FROM task_logs WHERE task_id=? ORDER BY id DESC LIMIT ?', (task_id,limit)).fetchall()
    return [dict(row) for row in reversed(rows)]


def add_message(task_id: str, owner_id: int, role: str, content: str) -> dict:
    content = str(content).strip()[:8000]
    if not content:
        raise ValueError('Message cannot be empty')
    now = _now()
    with _connect() as conn:
        cur = conn.execute('INSERT INTO task_messages (task_id,owner_id,created_at,role,content) VALUES (?,?,?,?,?)', (task_id,owner_id,now,role,content))
        conn.commit()
        message_id = cur.lastrowid
    return {'id': message_id, 'task_id': task_id, 'created_at': now, 'role': role, 'content': content}


def get_messages(task_id: str, owner_id: int | None = None, limit: int = 200) -> list[dict]:
    with _connect() as conn:
        if owner_id is not None and not conn.execute('SELECT 1 FROM tasks WHERE id=? AND owner_id=?', (task_id,owner_id)).fetchone():
            return []
        rows = conn.execute('SELECT id,task_id,created_at,role,content FROM task_messages WHERE task_id=? ORDER BY id DESC LIMIT ?', (task_id,limit)).fetchall()
    return [dict(row) for row in reversed(rows)]


def claim_unconsumed_user_messages(task_id: str) -> list[dict]:
    now = _now()
    with _connect() as conn:
        rows = conn.execute("SELECT id,task_id,created_at,role,content FROM task_messages WHERE task_id=? AND role='user' AND consumed_at IS NULL ORDER BY id", (task_id,)).fetchall()
        if rows:
            conn.execute("UPDATE task_messages SET consumed_at=? WHERE task_id=? AND role='user' AND consumed_at IS NULL", (now,task_id))
            conn.commit()
    return [dict(row) for row in rows]


def _update(task_id: str, **fields):
    fields['updated_at'] = _now()
    if 'waiting_details' in fields and fields['waiting_details'] is not None:
        fields['waiting_details'] = json.dumps(fields['waiting_details'], ensure_ascii=False)
    fields = {key: int(value) if isinstance(value, bool) else value for key,value in fields.items()}
    columns = ', '.join(f'{key}=?' for key in fields)
    with _connect() as conn:
        conn.execute(f'UPDATE tasks SET {columns} WHERE id=?', (*fields.values(), task_id))
        conn.commit()


def set_state(task_id: str, status: str, *, progress: str | None = None, error: str | None = None, result: str | None = None, human_help: str | None = None, waiting_details=None, checkpoint: str | None = None):
    values = {'status':status}
    for key,value in {'progress':progress,'error':error,'result':result,'human_help':human_help,'waiting_details':waiting_details,'checkpoint':checkpoint}.items():
        if value is not None: values[key] = value
    _update(task_id, **values)
    if progress: add_log(task_id, progress)
    if error: add_log(task_id, f'Error: {error}', level='error')


def add_log(task_id: str, message: str, level: str = 'info'):
    with _connect() as conn:
        conn.execute('INSERT INTO task_logs (task_id,created_at,level,message) VALUES (?,?,?,?)', (task_id,_now(),level,str(message)[:2000]))
        conn.commit()


def save_checkpoint(task_id: str, checkpoint: dict):
    """Persist only the caller-provided redacted execution checkpoint."""
    _update(task_id, checkpoint=json.dumps(checkpoint, ensure_ascii=False)[:8000])


def claim_task(task_id: str, worker_token: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("UPDATE tasks SET status='running', worker_token=?, cancel_requested=0, updated_at=? WHERE id=? AND status='queued' AND worker_token IS NULL", (worker_token,_now(),task_id))
        conn.commit()
        return cur.rowcount == 1


def request_cancel(task_id: str) -> bool:
    with _connect() as conn:
        now = _now()
        cur = conn.execute("UPDATE tasks SET status='cancelled', cancel_requested=1, progress='Task cancelled before execution started.', updated_at=? WHERE id=? AND status='queued'", (now,task_id))
        if cur.rowcount == 0:
            cur = conn.execute("UPDATE tasks SET cancel_requested=1, updated_at=? WHERE id=? AND status IN ('running','waiting_human','recovering')", (now,task_id))
        if cur.rowcount:
            conn.execute('INSERT INTO task_logs (task_id,created_at,level,message) VALUES (?,?,?,?)', (task_id,now,'info','Server-side cancellation requested.'))
        conn.commit()
        return cur.rowcount == 1


def requeue_for_message(task_id: str) -> dict | None:
    """Reopen a terminal task when its owner sends a follow-up instruction."""
    with _connect() as conn:
        row = conn.execute('SELECT status FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row or row['status'] not in {'completed', 'failed', 'cancelled'}:
            return None
        now = _now()
        conn.execute("UPDATE tasks SET status='queued', progress='Follow-up instruction received; task re-queued.', error=NULL, result=NULL, human_help=NULL, waiting_details=NULL, cancel_requested=0, worker_token=NULL, updated_at=? WHERE id=?", (now, task_id))
        conn.execute('INSERT INTO task_logs (task_id,created_at,level,message) VALUES (?,?,?,?)', (task_id, now, 'info', 'Follow-up instruction received; existing task session re-queued.'))
        conn.commit()
    return get_task(task_id)


def is_cancel_requested(task_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute('SELECT cancel_requested FROM tasks WHERE id=?', (task_id,)).fetchone()
    return bool(row and row['cancel_requested'])


def release_worker(task_id: str):
    _update(task_id, worker_token=None)


def queue_retry(task_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT recovery_count,status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row or row['status'] != 'failed' or row['recovery_count'] >= settings.max_task_recovery_attempts:
            return None
        conn.execute("UPDATE tasks SET status='queued', progress='Retry queued after explicit user request.', error=NULL, result=NULL, human_help=NULL, waiting_details=NULL, cancel_requested=0, worker_token=NULL, recovery_count=recovery_count+1, updated_at=? WHERE id=?", (_now(),task_id))
        conn.execute('INSERT INTO task_logs (task_id,created_at,level,message) VALUES (?,?,?,?)', (task_id,_now(),'recovery','Explicit retry requested; task re-queued.'))
        conn.commit()
    return get_task(task_id)


def recover_after_restart() -> list[str]:
    """Only queued work is eligible. Running/human tasks are failed to avoid duplicate side effects."""
    with _connect() as conn:
        rows = conn.execute("SELECT id,status FROM tasks WHERE status IN ('running','waiting_human','recovering')").fetchall()
        ids = [row['id'] for row in rows]
        now = _now()
        for task_id in ids:
            conn.execute("UPDATE tasks SET status='failed', error=?, progress=?, human_help=NULL, waiting_details=NULL, worker_token=NULL, updated_at=? WHERE id=?", ('Worker restarted while task was active; execution was not replayed because a browser action outcome may be uncertain.', 'Task stopped safely after worker restart; manual review is required before retrying.', now,task_id))
            conn.execute('INSERT INTO task_logs (task_id,created_at,level,message) VALUES (?,?,?,?)', (task_id,now,'recovery','Worker restart detected; active task not replayed to prevent duplicate side effects.'))
        queued = conn.execute("SELECT id FROM tasks WHERE status='queued' ORDER BY created_at").fetchall()
        conn.commit()
    return [row['id'] for row in queued]
