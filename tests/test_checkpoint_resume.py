"""Checkpoint-resume tests: durable checkpoints, restart recovery, human pauses and safety rules."""
import asyncio
import json
import sqlite3

import pytest

from app import checkpoint as cp
from app import config, runner, task_store


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(config.settings, 'database_url', '')
    monkeypatch.setattr(config.settings, 'database_path', str(tmp_path / 'resume.db'))
    return task_store


def _task(store, instruction='Inspect the site', owner=1):
    return store.create_task(owner_id=owner, instruction=instruction, start_url=None, timeout_seconds=60, provider='auto', model=None)


def _set_status(store, task_id, status, checkpoint=None):
    with store._connect() as conn:
        conn.execute('UPDATE tasks SET status=?, worker_token=? WHERE id=?', (status, 'old-worker', task_id))
        if checkpoint is not None:
            conn.execute('UPDATE tasks SET checkpoint=? WHERE id=?', (checkpoint, task_id))
        conn.commit()


# --- checkpoint format: save, reload, version, redaction ---------------------

def test_checkpoint_round_trips_through_the_store(store):
    task = _task(store)
    store.save_checkpoint(task['id'], {'phase': 'verified', 'step': 3, 'action': 'goto', 'url': 'https://example.test/a?token=SECRET#frag', 'verified': True})
    status, state = store.read_checkpoint(task['id'])
    assert status == 'ok'
    assert state['version'] == cp.CHECKPOINT_VERSION
    assert state['step'] == 3 and state['action'] == 'goto'
    assert state['url'] == 'https://example.test/a'  # query and fragment dropped
    assert 'SECRET' not in store.get_task(task['id'])['checkpoint']


def test_checkpoint_never_serializes_secrets(store):
    task = _task(store)
    secret_state = {
        'phase': 'verified', 'step': 1, 'action': 'fill', 'verified': True,
        'password': 'hunter2', 'api_key': 'sk-live-123', 'cookie': 'session=abc',
        'otp': '991122', 'generated_value': 'Zq9!generated', 'value': 'typed-value',
        'url': 'https://user:pw@example.test/reset?code=777',
        'history': [{'action': 'fill', 'target': 'Password', 'result': {'value': 'hunter2'}, 'error': 'boom hunter2'},
                    {'tool': 'user_message', 'message': 'my password is hunter2'}],
    }
    store.save_checkpoint(task['id'], secret_state)
    raw = store.get_task(task['id'])['checkpoint']
    for leaked in ('hunter2', 'sk-live-123', 'session=abc', '991122', 'Zq9!generated', 'typed-value', 'user:pw', 'code=777'):
        assert leaked not in raw, leaked
    assert json.loads(raw)['version'] == cp.CHECKPOINT_VERSION


def test_verification_link_url_is_never_persisted(store):
    task = _task(store)
    store.save_checkpoint(task['id'], {'phase': 'verified', 'step': 2, 'action': 'mailbox_open_verification_link', 'url': 'https://verify.example/confirm?token=ONE-TIME', 'verified': True})
    raw = store.get_task(task['id'])['checkpoint']
    assert 'ONE-TIME' not in raw and 'verify.example' not in raw


def test_checkpoint_size_is_bounded_by_trimming_history(store):
    task = _task(store)
    history = [{'action': 'click', 'target': 'x' * 100, 'ok': True} for _ in range(400)]
    store.save_checkpoint(task['id'], {'phase': 'verified', 'step': 400, 'action': 'click', 'verified': True, 'url': 'https://e.test/', 'history': history})
    raw = store.get_task(task['id'])['checkpoint']
    assert len(raw.encode()) <= cp.MAX_BYTES
    assert json.loads(raw)['step'] == 400


# --- invalid, missing, legacy and outdated checkpoints -----------------------

def test_missing_and_invalid_checkpoints_are_classified():
    assert cp.decode(None) == ('missing', None)
    assert cp.decode('') == ('missing', None)
    assert cp.decode('not json{')[0] == 'invalid'
    assert cp.decode('[1,2]')[0] == 'invalid'
    assert cp.decode('{"version": "two"}')[0] == 'invalid'
    assert cp.decode('{"version": 2, "phase": "bogus", "step": 1}')[0] == 'invalid'


def test_legacy_pre_version_checkpoint_is_not_resumable(store):
    # Rows written before this format only carry the last action; they must not be resumed blindly.
    legacy = json.dumps({'step': 2, 'action': 'click', 'target': 'Next', 'url': 'https://example.test'})
    status, state = cp.decode(legacy)
    assert status == 'legacy' and state is None


def test_future_checkpoint_version_is_refused_not_misread():
    status, state = cp.decode(json.dumps({'version': 99, 'phase': 'verified', 'step': 1}))
    assert status == 'unsupported_version' and state is None


def test_invalid_checkpoint_fails_task_on_restart_without_replay(store):
    task = _task(store)
    _set_status(store, task['id'], 'running', checkpoint='{broken')
    queued = store.recover_after_restart()
    current = store.get_task(task['id'])
    assert current['status'] == 'failed'
    assert 'invalid' in current['error']
    assert task['id'] not in queued


def test_missing_checkpoint_fails_task_on_restart_without_replay(store):
    task = _task(store)
    _set_status(store, task['id'], 'running', checkpoint=None)
    store.recover_after_restart()
    assert store.get_task(task['id'])['status'] == 'failed'


# --- restart recovery of a running task ---------------------------------------

def test_running_task_with_verified_checkpoint_is_recovering_not_failed(store):
    task = _task(store)
    store.save_checkpoint(task['id'], {'phase': 'verified', 'step': 4, 'action': 'goto', 'url': 'https://example.test/step4', 'verified': True})
    _set_status(store, task['id'], 'running', checkpoint=store.get_task(task['id'])['checkpoint'])
    recovered = store.recover_after_restart()
    assert task['id'] in recovered
    current = store.get_task(task['id'])
    assert current['status'] == 'recovering'
    assert current['worker_token'] is None


def test_recovering_task_can_be_claimed_once(store):
    task = _task(store)
    store.save_checkpoint(task['id'], {'phase': 'verified', 'step': 1, 'action': 'goto', 'url': 'https://e.test/', 'verified': True})
    _set_status(store, task['id'], 'recovering', checkpoint=store.get_task(task['id'])['checkpoint'])
    with store._connect() as conn:
        conn.execute('UPDATE tasks SET worker_token=NULL WHERE id=?', (task['id'],))  # as recover_after_restart leaves it
        conn.commit()
    assert store.claim_task(task['id'], 'worker-a') is True
    assert store.claim_task(task['id'], 'worker-b') is False


def test_restart_does_not_resume_after_uncertain_side_effect(store):
    task = _task(store)
    store.save_checkpoint(task['id'], {'phase': 'pending', 'step': 3, 'action': 'click', 'target': 'Place order', 'url': 'https://shop.test/cart', 'verified': False})
    _set_status(store, task['id'], 'running', checkpoint=store.get_task(task['id'])['checkpoint'])
    store.recover_after_restart()
    current = store.get_task(task['id'])
    assert current['status'] == 'failed'
    assert 'not repeated' in current['error']
    assert 'Place order' in current['error']


def test_error_during_side_effect_is_not_replayed(store):
    task = _task(store)
    store.save_checkpoint(task['id'], {'phase': 'error', 'step': 5, 'action': 'fill_form', 'target': 'Signup', 'url': 'https://e.test/signup', 'verified': False, 'error_type': 'TimeoutError'})
    _set_status(store, task['id'], 'running', checkpoint=store.get_task(task['id'])['checkpoint'])
    store.recover_after_restart()
    assert store.get_task(task['id'])['status'] == 'failed'


# --- recovery of a task waiting on human verification ------------------------

def test_waiting_human_task_keeps_pending_challenge_across_restart(store):
    task = _task(store)
    store.save_checkpoint(task['id'], {'phase': 'challenge', 'step': 2, 'action': 'goto', 'url': 'https://e.test/login', 'verified': True, 'challenge': 'recaptcha'})
    _set_status(store, task['id'], 'waiting_human', checkpoint=store.get_task(task['id'])['checkpoint'])
    with store._connect() as conn:
        conn.execute("UPDATE tasks SET waiting_details=? WHERE id=?", (json.dumps({'type': 'recaptcha'}), task['id']))
        conn.commit()
    ids = store.recover_waiting_on_restart()
    current = store.get_task(task['id'])
    assert task['id'] in ids
    assert current['status'] == 'recovering'
    assert store.read_checkpoint(task['id'])[1]['challenge'] == 'recaptcha'


def test_waiting_human_without_restorable_page_fails_with_reason(store):
    task = _task(store)
    _set_status(store, task['id'], 'waiting_human', checkpoint=None)
    store.recover_waiting_on_restart()
    current = store.get_task(task['id'])
    assert current['status'] == 'failed'
    assert 'no safe checkpoint' in current['error']


# --- resume from last verified step without duplicating completed actions -----

def test_resume_skips_completed_steps_and_restores_history(monkeypatch):
    seen = {'goto': [], 'steps': []}

    class FakePage:
        url = 'about:blank'
        async def goto(self, url, **kwargs):
            seen['goto'].append(url)
            self.url = url
        async def title(self):
            return 'T'

    class FakeSession:
        async def __aenter__(self):
            return FakePage()
        async def __aexit__(self, *a):
            return False

    async def fake_snapshot(page):
        return {'url': page.url, 'title': 'T', 'body': 'ok', 'controls': [], 'challenge': None}

    async def fake_choose(instruction, history, state, step, provider_choice='auto', model=None):
        seen['steps'].append(step)
        return {'action': 'done', 'summary': 'finished'}, 'primary', 'model-a'

    saved = []
    async def save(cp_state):
        saved.append(cp_state)

    monkeypatch.setattr(runner.browser, 'session', lambda proxy=None: FakeSession())
    monkeypatch.setattr(runner, '_snapshot', fake_snapshot)
    monkeypatch.setattr(runner, '_choose_action', fake_choose)
    monkeypatch.setattr(runner.settings, 'execution_engine', 'phoenix')
    resume = {'version': 2, 'phase': 'verified', 'step': 4, 'action': 'click', 'url': 'https://e.test/step4',
              'history': [{'action': 'goto', 'target': None, 'ok': True}, {'action': 'click', 'target': 'Next', 'ok': True}]}
    result = asyncio.run(runner.execute_task('Finish the form', resume_from=resume, save_checkpoint=save))
    assert seen['goto'] == ['https://e.test/step4']  # restored at last verified URL, once
    assert seen['steps'] == [5]  # continues after step 4; completed steps are not re-run
    assert 'finished' in result
    assert saved[-1]['phase'] == 'done'
    assert saved[-1]['verified'] is False  # model claim, not Phoenix-verified proof


def test_resume_refuses_browser_use_engine_checkpoint():
    allowed, reason = cp.resume_decision({'phase': 'verified', 'step': 1, 'url': 'https://e.test/', 'engine': 'browser_use'}, engine='browser_use')
    assert allowed is False
    assert 'browser_use' in reason


def test_resume_decision_blocks_done_and_credential_states():
    assert cp.resume_decision({'phase': 'done', 'step': 3, 'url': 'https://e.test/'})[0] is False
    assert cp.resume_decision({'phase': 'verified', 'step': 2, 'url': 'https://e.test/', 'generated_credentials_used': True})[0] is False
    assert cp.resume_decision({'phase': 'verified', 'step': 2, 'url': 'https://e.test/'})[0] is True


# --- browser restoration failure ---------------------------------------------

def test_failed_browser_restoration_stops_safely(monkeypatch):
    class BrokenPage:
        url = 'about:blank'
        async def goto(self, url, **kwargs):
            raise RuntimeError('net::ERR_CONNECTION_RESET')
        async def title(self):
            return ''

    class FakeSession:
        async def __aenter__(self):
            return BrokenPage()
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(runner.browser, 'session', lambda proxy=None: FakeSession())
    monkeypatch.setattr(runner.settings, 'browser_retry_attempts', 1)
    monkeypatch.setattr(runner.settings, 'execution_engine', 'phoenix')
    resume = {'version': 2, 'phase': 'verified', 'step': 2, 'action': 'goto', 'url': 'https://e.test/x', 'history': []}
    with pytest.raises(RuntimeError):
        asyncio.run(runner.execute_task('Continue', resume_from=resume))


def test_restored_page_on_different_site_is_blocked(monkeypatch):
    class RedirectedPage:
        url = 'https://other.example/landing'
        async def goto(self, url, **kwargs):
            self.url = 'https://other.example/landing'
        async def title(self):
            return ''

    class FakeSession:
        async def __aenter__(self):
            return RedirectedPage()
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(runner.browser, 'session', lambda proxy=None: FakeSession())
    monkeypatch.setattr(runner.settings, 'execution_engine', 'phoenix')
    resume = {'version': 2, 'phase': 'verified', 'step': 2, 'action': 'goto', 'url': 'https://e.test/x', 'history': []}
    with pytest.raises(runner.TaskBlocked):
        asyncio.run(runner.execute_task('Continue', resume_from=resume))


# --- human pause and timeout -------------------------------------------------

def test_human_pause_time_is_not_counted_against_execution_budget(monkeypatch):
    from app import main
    lock = asyncio.Lock()

    async def run():
        clock = main._ActiveClock(0.5, lock)
        await clock.acquire()
        async def long_execution():
            await asyncio.sleep(0.1)
            await clock.pause()           # human waits longer than the budget
            await asyncio.sleep(1.2)
            await clock.resume()
            await asyncio.sleep(0.1)
            return 'ok'
        execution = asyncio.create_task(long_execution())
        return await main._run_with_active_budget(execution, clock)

    assert asyncio.run(run()) == 'ok'


def test_budget_still_enforced_during_active_execution(monkeypatch):
    from app import main
    lock = asyncio.Lock()

    async def run():
        clock = main._ActiveClock(0.2, lock)
        await clock.acquire()
        async def busy():
            await asyncio.sleep(5)
        execution = asyncio.create_task(busy())
        with pytest.raises(TimeoutError):
            await main._run_with_active_budget(execution, clock)

    asyncio.run(run())


def test_execution_lock_is_released_while_waiting_for_human():
    from app import main
    lock = asyncio.Lock()

    async def run():
        clock = main._ActiveClock(60, lock)
        await clock.acquire()
        await clock.pause()
        other_got_lock = lock.locked() is False
        await clock.resume()
        clock.release()
        return other_got_lock

    assert asyncio.run(run()) is True


# --- migration compatibility -------------------------------------------------

def test_existing_database_without_new_columns_is_migrated_additively(monkeypatch, tmp_path):
    """A pre-existing SQLite table (no checkpoint/proxy columns) must open and migrate without data loss."""
    path = tmp_path / 'old.db'
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, owner_id INTEGER NOT NULL, instruction TEXT NOT NULL, start_url TEXT, timeout_seconds INTEGER, status TEXT NOT NULL, progress TEXT, human_help TEXT, waiting_details TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, result TEXT, error TEXT, provider TEXT, model TEXT, recovery_count INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0, worker_token TEXT)")
    conn.execute("INSERT INTO tasks (id,slug,owner_id,instruction,status,created_at,updated_at) VALUES ('t1','s1',1,'old task','completed','2026-01-01','2026-01-01')")
    conn.commit(); conn.close()
    monkeypatch.setattr(config.settings, 'database_url', '')
    monkeypatch.setattr(config.settings, 'database_path', str(path))
    old = task_store.get_task('t1')
    assert old['instruction'] == 'old task'
    assert old['status'] == 'completed'
    assert old['proxy_mode'] == 'direct'
    status, state = task_store.read_checkpoint('t1')
    assert status == 'missing'


def test_legacy_checkpoint_row_is_read_safely_after_upgrade(store):
    task = _task(store)
    _set_status(store, task['id'], 'running', checkpoint=json.dumps({'step': 2, 'action': 'click', 'target': 'Next'}))
    assert store.read_checkpoint(task['id'])[0] == 'legacy'
    store.recover_after_restart()
    assert store.get_task(task['id'])['status'] == 'failed'


def test_run_task_passes_verified_checkpoint_to_recovering_task(store, monkeypatch):
    """Regression: the recovering status must be read before the claim flips it to running."""
    from app import main
    task = _task(store)
    store.save_checkpoint(task['id'], {'phase': 'verified', 'step': 2, 'action': 'goto', 'url': 'https://e.test/step2', 'verified': True})
    _set_status(store, task['id'], 'running', checkpoint=store.get_task(task['id'])['checkpoint'])
    store.recover_after_restart()
    seen = {}

    async def fake_execute(*args, **kwargs):
        seen['resume_from'] = kwargs.get('resume_from')
        return json.dumps({'summary': 'ok'})

    monkeypatch.setattr(main, 'execute_task', fake_execute)
    asyncio.run(main.run_task(task['id']))
    assert seen['resume_from']['step'] == 2
    assert seen['resume_from']['url'] == 'https://e.test/step2'
    assert store.get_task(task['id'])['status'] == 'completed'
