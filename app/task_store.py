"""Durable task records for the single-service Phoenix worker."""
from __future__ import annotations
import json
import re
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from app.config import settings
from app.db import connect, using_postgres


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_SCHEMA_ADVISORY_KEY = 727272  # arbitrary constant; all app processes share this lock
_schema_ready_for: set[str] = set()
_schema_guard = threading.Lock()


def _is_deadlock(exc: Exception) -> bool:
    return type(exc).__name__ == 'DeadlockDetected' or getattr(exc, 'sqlstate', None) == '40P01'


def _schema_key() -> str:
    from app.db import using_postgres as _pg
    return settings.database_url if _pg() else str(settings.database_path)


def _connect():
    conn = connect()
    key = _schema_key()
    if key not in _schema_ready_for:
        with _schema_guard:
            if key not in _schema_ready_for:
                _run_schema_once(conn)
                _schema_ready_for.add(key)
    return conn


def _run_schema_once(conn) -> None:
    """Run schema DDL once per process, serialized across processes by a Postgres advisory lock.

    Every _connect() used to run the DDL, so concurrent requests and the startup hook each
    took AccessExclusiveLock on tasks and deadlocked. Deadlocks are retried.
    """
    for attempt in range(5):
        try:
            if using_postgres():
                conn.execute('SELECT pg_advisory_xact_lock(%s)' % _SCHEMA_ADVISORY_KEY)
            _create_schema(conn)
            conn.commit()
            return
        except Exception as exc:
            conn.rollback()
            if _is_deadlock(exc) and attempt < 4:
                time.sleep(0.2 * (attempt + 1))
                continue
            raise


def _create_schema(conn) -> None:

    task_log_id = 'BIGSERIAL PRIMARY KEY' if using_postgres() else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    schema = """
    CREATE TABLE IF NOT EXISTS tasks (
      id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, owner_id INTEGER NOT NULL,
      instruction TEXT NOT NULL, start_url TEXT, timeout_seconds INTEGER,
      status TEXT NOT NULL, progress TEXT, human_help TEXT, waiting_details TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL, result TEXT, error TEXT,
      provider TEXT, model TEXT, recovery_count INTEGER NOT NULL DEFAULT 0,
      cancel_requested INTEGER NOT NULL DEFAULT 0, worker_token TEXT, checkpoint TEXT,
      proxy_mode TEXT NOT NULL DEFAULT 'direct', proxy_identifier TEXT, proxy_id INTEGER
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
    CREATE TABLE IF NOT EXISTS task_files (
      id __TASK_LOG_ID__, task_id TEXT NOT NULL, owner_id INTEGER NOT NULL,
      name TEXT NOT NULL, content_type TEXT, size INTEGER NOT NULL, path TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_task_files_task ON task_files(task_id, id);
    """.replace('__TASK_LOG_ID__', task_log_id).replace('__TASK_MESSAGE_ID__', task_log_id)
    conn.executescript(schema)
    for column, definition in (("proxy_mode", "TEXT NOT NULL DEFAULT 'direct'"), ("proxy_identifier", "TEXT"), ("effort", "TEXT"), ("attachment_count", "INTEGER NOT NULL DEFAULT 0"), ("fast_mode", "INTEGER NOT NULL DEFAULT 0"), ("deep_research", "INTEGER NOT NULL DEFAULT 0"), ("browser_target", "TEXT")):
        if using_postgres():
            # IF NOT EXISTS avoids a duplicate-column error from concurrent
            # web/worker startup migrations aborting the PostgreSQL transaction.
            conn.execute(f"ALTER TABLE tasks ADD COLUMN IF NOT EXISTS {column} {definition}")
        else:
            try:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} {definition}")
            except Exception:
                pass


def _decode(row):
    if not row:
        return None
    item = dict(row)
    item['waiting_details'] = json.loads(item['waiting_details']) if item.get('waiting_details') else None
    item['cancel_requested'] = bool(item.get('cancel_requested'))
    item['fast_mode'] = bool(item.get('fast_mode'))
    item['deep_research'] = bool(item.get('deep_research'))
    return item


def _slug(instruction: str, task_id: str) -> str:
    words = re.sub(r'[^a-z0-9]+', '-', instruction.lower()).strip('-')[:32].strip('-') or 'task'
    return f'{words}-{task_id[:6]}'


def create_task(*, owner_id: int, instruction: str, start_url: str | None, timeout_seconds: int | None, provider: str, model: str | None, proxy_mode: str = 'direct', proxy_identifier: str | None = None, effort: str | None = None, fast_mode: bool = False, deep_research: bool = False, browser_target: str | None = None, proxy_id: int | None = None) -> dict:
    task_id = secrets.token_hex(8)
    slug = _slug(instruction, task_id)
    now = _now()
    with _connect() as conn:
        conn.execute("INSERT INTO tasks (id,slug,owner_id,instruction,start_url,timeout_seconds,status,progress,created_at,updated_at,provider,model,proxy_mode,proxy_identifier,effort,fast_mode,deep_research,browser_target,proxy_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (task_id,slug,owner_id,instruction,start_url,timeout_seconds,'queued','Queued; waiting for the execution slot.',now,now,provider,model,proxy_mode,proxy_identifier,effort,int(bool(fast_mode)),int(bool(deep_research)),browser_target if browser_target in ('remote','local') else None,proxy_id))
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
        # RETURNING works on both SQLite and PostgreSQL; psycopg cursors have no lastrowid.
        cur = conn.execute('INSERT INTO task_messages (task_id,owner_id,created_at,role,content) VALUES (?,?,?,?,?) RETURNING id', (task_id,owner_id,now,role,content))
        message_id = cur.fetchone()['id']
        conn.commit()
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
            ids = [row['id'] for row in rows]
            placeholders = ','.join('?' for _ in ids)
            conn.execute(
                f"UPDATE task_messages SET consumed_at=? WHERE task_id=? AND role='user' AND consumed_at IS NULL AND id IN ({placeholders})",
                (now, task_id, *ids),
            )
            conn.commit()
    return [dict(row) for row in rows]


def add_file(task_id: str, owner_id: int, name: str, content_type: str | None, size: int, path: str) -> dict:
    now = _now()
    with _connect() as conn:
        cur = conn.execute('INSERT INTO task_files (task_id,owner_id,name,content_type,size,path,created_at) VALUES (?,?,?,?,?,?,?) RETURNING id', (task_id, owner_id, name, content_type, size, path, now))
        file_id = cur.fetchone()['id']
        conn.execute('UPDATE tasks SET attachment_count=attachment_count+1 WHERE id=?', (task_id,))
        conn.commit()
    return {'id': file_id, 'task_id': task_id, 'name': name, 'content_type': content_type, 'size': size, 'created_at': now}


def list_files(task_id: str, owner_id: int | None = None) -> list[dict]:
    with _connect() as conn:
        if owner_id is not None and not conn.execute('SELECT 1 FROM tasks WHERE id=? AND owner_id=?', (task_id, owner_id)).fetchone():
            return []
        rows = conn.execute('SELECT id,task_id,name,content_type,size,path,created_at FROM task_files WHERE task_id=? ORDER BY id', (task_id,)).fetchall()
    return [dict(row) for row in rows]


def get_file(task_id: str, file_id: int, owner_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute('SELECT f.id,f.task_id,f.name,f.content_type,f.size,f.path FROM task_files f JOIN tasks t ON t.id=f.task_id WHERE f.task_id=? AND f.id=? AND t.owner_id=?', (task_id, file_id, owner_id)).fetchone()
    return dict(row) if row else None


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
    """Persist a versioned, redacted checkpoint in one atomic UPDATE."""
    from app import checkpoint as checkpoint_format
    payload = checkpoint_format.dumps(checkpoint)
    with _connect() as conn:
        conn.execute('UPDATE tasks SET checkpoint=?, updated_at=? WHERE id=?', (payload, _now(), task_id))
        conn.commit()


def read_checkpoint(task_id: str) -> tuple[str, dict | None]:
    """Return (status, state) for the stored checkpoint: ok, missing, invalid, legacy or unsupported_version."""
    from app import checkpoint as checkpoint_format
    item = get_task(task_id)
    return checkpoint_format.decode(item.get('checkpoint') if item else None)


def claim_task(task_id: str, worker_token: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("UPDATE tasks SET status='running', worker_token=?, cancel_requested=0, updated_at=? WHERE id=? AND status IN ('queued','recovering') AND worker_token IS NULL", (worker_token,_now(),task_id))
        conn.commit()
        return cur.rowcount == 1


def next_queued_task() -> str | None:
    """Return one queued task; the worker immediately performs the atomic claim."""
    with _connect() as conn:
        row = conn.execute("SELECT id FROM tasks WHERE status='queued' AND worker_token IS NULL ORDER BY created_at LIMIT 1").fetchone()
    if not row:
        return None
    return row['id']


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


def set_model_choice(task_id: str, provider: str, model: str | None) -> dict | None:
    """Persist the model the user picked for this task; the running agent re-reads it each step."""
    with _connect() as conn:
        conn.execute('UPDATE tasks SET provider=?, model=?, updated_at=? WHERE id=?', (provider or 'auto', model or None, _now(), task_id))
        conn.commit()
    return get_task(task_id)


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
        if not row or row['status'] not in {'failed','cancelled'} or row['recovery_count'] >= settings.max_task_recovery_attempts:
            return None
        conn.execute("UPDATE tasks SET status='queued', progress='Retry queued after explicit user request.', error=NULL, result=NULL, human_help=NULL, waiting_details=NULL, cancel_requested=0, worker_token=NULL, recovery_count=recovery_count+1, updated_at=? WHERE id=?", (_now(),task_id))
        conn.execute('INSERT INTO task_logs (task_id,created_at,level,message) VALUES (?,?,?,?)', (task_id,_now(),'recovery','Explicit retry requested; task re-queued.'))
        conn.commit()
    return get_task(task_id)


def recover_after_restart() -> list[str]:
    """Recover interrupted tasks from their verified checkpoint, or stop them with a clear reason.

    Rows in ``running``/``recovering`` are reset to ``recovering`` only when their checkpoint allows
    a safe resume. A checkpoint in ``pending``/``error`` for a side-effect action, or a missing or
    invalid checkpoint, fails the task with a reason; nothing is replayed blindly.
    ``waiting_human`` rows are handled by ``recover_waiting_on_restart``.
    Returns the ids of queued tasks plus the ids of tasks moved to ``recovering``.
    """
    from app import checkpoint as checkpoint_format
    with _connect() as conn:
        rows = conn.execute("SELECT id,status,checkpoint,instruction,recovery_count FROM tasks WHERE status IN ('running','recovering')").fetchall()
        now = _now()
        recovering = []
        for row in rows:
            status, state = checkpoint_format.decode(row['checkpoint'])
            allowed, reason = (False, f'checkpoint {status}') if status != 'ok' else checkpoint_format.resume_decision(state)
            if allowed:
                conn.execute("UPDATE tasks SET status='recovering', progress=?, worker_token=NULL, updated_at=? WHERE id=?", ('Worker restarted; resuming from the last verified checkpoint.', now, row['id']))
                conn.execute('INSERT INTO task_logs (task_id,created_at,level,message) VALUES (?,?,?,?)', (row['id'], now, 'recovery', f'Worker restart detected; resuming from verified checkpoint at step {state["step"]}.'))
                recovering.append(row['id'])
            else:
                # No resumable checkpoint (e.g. the worker died before the first one was
                # written). The task stopped safely, so re-queue it automatically instead
                # of failing it, bounded by the same recovery-attempt cap as manual retries.
                attempts = int(getattr(settings, 'max_task_recovery_attempts', 2) or 0)
                if status == 'missing':
                    # Nothing was ever checkpointed: the worker died before the task did
                    # anything, so there is nothing half-done to protect. Re-queue freely
                    # (free-plan restarts are frequent); the per-task retry cap for the
                    # UI retry button is unaffected.
                    conn.execute("UPDATE tasks SET status='queued', progress='Worker restarted before any checkpoint; task re-queued automatically.', error=NULL, human_help=NULL, waiting_details=NULL, worker_token=NULL, updated_at=? WHERE id=?", (now, row['id']))
                    conn.execute('INSERT INTO task_logs (task_id,created_at,level,message) VALUES (?,?,?,?)', (row['id'], now, 'recovery', f'Worker restart detected; task had not started ({reason}); re-queued automatically.'))
                elif attempts > row['recovery_count']:
                    conn.execute("UPDATE tasks SET status='queued', progress='Worker restarted before any checkpoint; task re-queued automatically.', error=NULL, human_help=NULL, waiting_details=NULL, worker_token=NULL, recovery_count=recovery_count+1, updated_at=? WHERE id=?", (now, row['id']))
                    conn.execute('INSERT INTO task_logs (task_id,created_at,level,message) VALUES (?,?,?,?)', (row['id'], now, 'recovery', f'Worker restart detected; not resumable ({reason}); re-queued automatically.'))
                else:
                    conn.execute("UPDATE tasks SET status='failed', error=?, progress=?, human_help=NULL, waiting_details=NULL, worker_token=NULL, updated_at=? WHERE id=?", (f'Worker restarted; not resumed automatically: {reason}. Press Retry to run the task again.', 'Task stopped safely after a worker restart; nothing was half-done, retrying is safe.', now, row['id']))
                    conn.execute('INSERT INTO task_logs (task_id,created_at,level,message) VALUES (?,?,?,?)', (row['id'], now, 'recovery', f'Worker restart detected; not resumed: {reason}.'))
        queued = conn.execute("SELECT id FROM tasks WHERE status='queued' ORDER BY created_at").fetchall()
        conn.commit()
    return [row['id'] for row in queued] + recovering


def recover_waiting_on_restart() -> list[str]:
    """Tasks paused for human verification keep their pending challenge and resume after restart.

    The browser session cannot survive a restart, so the task is re-queued into ``recovering``.
    The runner restores the last verified URL and detects the challenge again before pausing.
    """
    from app import checkpoint as checkpoint_format
    with _connect() as conn:
        rows = conn.execute("SELECT id,checkpoint FROM tasks WHERE status='waiting_human'").fetchall()
        now = _now()
        ids = []
        for row in rows:
            status, state = checkpoint_format.decode(row['checkpoint'])
            if status == 'ok' and state.get('url') and state.get('phase') in {'challenge', 'verified'}:
                conn.execute("UPDATE tasks SET status='recovering', progress=?, worker_token=NULL, updated_at=? WHERE id=?", ('Worker restarted while waiting for human verification; restoring the page to re-check the challenge.', now, row['id']))
                conn.execute('INSERT INTO task_logs (task_id,created_at,level,message) VALUES (?,?,?,?)', (row['id'], now, 'recovery', 'Worker restart detected while paused for human verification; the pending challenge was kept and the page will be re-checked.'))
                ids.append(row['id'])
            else:
                conn.execute("UPDATE tasks SET status='failed', error=?, progress=?, human_help=NULL, waiting_details=NULL, worker_token=NULL, updated_at=? WHERE id=?", ('Worker restarted while paused for human verification; no safe checkpoint to restore the page.', 'Task stopped safely after a worker restart; nothing was half-done, retrying is safe.', now, row['id']))
                conn.execute('INSERT INTO task_logs (task_id,created_at,level,message) VALUES (?,?,?,?)', (row['id'], now, 'recovery', 'Worker restart detected while paused; no safe checkpoint, so the task was stopped.'))
        conn.commit()
    return ids


def usage_stats(owner_id: int) -> dict:
    """Phoenix-native usage: task counts, runtime and uploaded file totals for one owner."""
    with _connect() as conn:
        rows = conn.execute('SELECT status, created_at, updated_at, fast_mode, deep_research FROM tasks WHERE owner_id=?', (owner_id,)).fetchall()
        files = conn.execute('SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS bytes FROM task_files WHERE owner_id=?', (owner_id,)).fetchone()
    now = datetime.now(timezone.utc)
    by_status: dict[str, int] = {}
    runtime = 0.0
    fast = deep = 0
    for row in rows:
        by_status[row['status']] = by_status.get(row['status'], 0) + 1
        try:
            start = datetime.fromisoformat(str(row['created_at']))
            end = now if row['status'] in ('queued', 'running', 'recovering', 'waiting_human') else datetime.fromisoformat(str(row['updated_at']))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            runtime += max(0.0, (end - start).total_seconds())
        except (TypeError, ValueError):
            pass
        fast += int(bool(row['fast_mode']))
        deep += int(bool(row['deep_research']))
    return {
        'total_tasks': len(rows),
        'by_status': by_status,
        'completed': by_status.get('completed', 0),
        'failed': by_status.get('failed', 0),
        'active': sum(by_status.get(k, 0) for k in ('queued', 'running', 'recovering', 'waiting_human')),
        'runtime_seconds': round(runtime, 1),
        'fast_mode_tasks': fast,
        'deep_research_tasks': deep,
        'files': int(files['n'] or 0),
        'file_bytes': int(files['bytes'] or 0),
    }


def usage_stats(owner_id: int) -> dict:
    """Real Phoenix-native usage stats for one owner, computed from the tasks table."""
    with connect() as conn:
        rows = conn.execute("SELECT status, fast_mode, deep_research, created_at, updated_at FROM tasks WHERE owner_id=?", (owner_id,)).fetchall()
        files = conn.execute("SELECT COUNT(*) AS n FROM task_files WHERE owner_id=?", (owner_id,)).fetchone()
    from datetime import datetime as _dt
    runtime = 0
    completed = failed = active = 0
    deep = fast = 0
    for r in rows:
        if r['status'] == 'completed': completed += 1
        elif r['status'] in ('failed', 'cancelled'): failed += 1
        else: active += 1
        if r['fast_mode']: fast += 1
        if r['deep_research']: deep += 1
        try:
            runtime += max(0, int((_dt.fromisoformat(r['updated_at']) - _dt.fromisoformat(r['created_at'])).total_seconds()))
        except Exception:
            pass
    return {'total_tasks': len(rows), 'completed': completed, 'failed': failed, 'active': active,
            'runtime_seconds': runtime, 'deep_research_tasks': deep, 'fast_mode_tasks': fast,
            'files': int(files['n']) if files else 0}


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
