def test_next_queued_task_returns_oldest_unclaimed_task(monkeypatch, tmp_path):
    from app import config, task_store
    monkeypatch.setattr(config.settings, 'database_url', None)
    monkeypatch.setattr(config.settings, 'database_path', str(tmp_path / 'tasks.db'))
    first = task_store.create_task(owner_id=1, instruction='first queued task', start_url=None, timeout_seconds=30, provider='auto', model=None)
    second = task_store.create_task(owner_id=1, instruction='second queued task', start_url=None, timeout_seconds=30, provider='auto', model=None)
    assert task_store.next_queued_task() == first['id']
    assert task_store.claim_task(first['id'], 'worker-a') is True
    assert task_store.next_queued_task() == second['id']


def test_overflow_worker_module_has_queue_loop():
    from pathlib import Path
    source = Path('app/overflow_worker.py').read_text()
    assert 'next_queued_task' in source
    assert 'run_task(task_id)' in source
