"""Tests for fast mode, deep research, owner settings, usage stats and config export/import."""
import json

from fastapi.testclient import TestClient

from app import config, run_options, settings_store, task_store
from app.main import app
from app.models import TaskRequest


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr(config.settings, 'database_url', '')
    monkeypatch.setattr(config.settings, 'database_path', str(tmp_path / 'features.db'))


def _login(client):
    client.post('/auth/register', json={'email': 'feat@example.com', 'password': 'correct-horse-1'})
    r = client.post('/auth/login', json={'email': 'feat@example.com', 'password': 'correct-horse-1'})
    assert r.status_code == 200, r.text
    return {'Authorization': 'Bearer ' + r.json()['access_token']}


def test_task_request_accepts_fast_and_deep_flags():
    req = TaskRequest(instruction='research the docs', fast_mode=True, deep_research=True)
    assert req.fast_mode is True and req.deep_research is True
    default = TaskRequest(instruction='research the docs')
    assert default.fast_mode is False and default.deep_research is False


def test_task_row_persists_fast_and_deep_flags(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    t = task_store.create_task(owner_id=7, instruction='check prices', start_url=None, timeout_seconds=60,
                               provider='auto', model=None, fast_mode=True, deep_research=False)
    got = task_store.get_task(t['id'])
    assert got['fast_mode'] is True and got['deep_research'] is False


def test_usage_endpoint_returns_real_counts_without_balance(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    client = TestClient(app)
    headers = _login(client)
    client.post('/tasks', headers=headers, json={'instruction': 'first real task', 'fast_mode': True})
    client.post('/tasks', headers=headers, json={'instruction': 'second deep task', 'deep_research': True})
    r = client.get('/usage', headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['balance'] is None and body['unit'] == 'tasks'
    assert body['stats']['total_tasks'] == 2
    assert body['stats']['fast_mode_tasks'] == 1
    assert body['stats']['deep_research_tasks'] == 1


def test_settings_round_trip_and_validation(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    client = TestClient(app)
    headers = _login(client)
    payload = {'agent': {'system_prompt': 'prefer official sites', 'max_steps': 12},
               'browser': {'headless': True, 'viewport_width': 1024, 'viewport_height': 768, 'user_agent': '', 'slow_mo_ms': 0}}
    assert client.put('/settings', headers=headers, json=payload).status_code == 200
    got = client.get('/settings', headers=headers).json()
    assert got['agent']['system_prompt'] == 'prefer official sites'
    assert got['agent']['max_steps'] == 12
    assert got['browser']['viewport_width'] == 1024
    bad = client.put('/settings', headers=headers, json={'agent': {'max_steps': 9999}})
    assert bad.status_code == 422


def test_config_export_import_round_trip(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    client = TestClient(app)
    headers = _login(client)
    client.put('/settings', headers=headers, json={'agent': {'system_prompt': 'be brief', 'max_steps': None},
                                                   'browser': {'headless': False, 'viewport_width': 1280, 'viewport_height': 800, 'user_agent': '', 'slow_mo_ms': 0}})
    exported = client.get('/config/export', headers=headers)
    assert exported.status_code == 200
    doc = json.loads(exported.text)
    assert doc['format'] == 'phoenix-config' and doc['settings']['agent']['system_prompt'] == 'be brief'
    doc['task'] = {'instruction': 'compare two pricing pages', 'fast_mode': True, 'deep_research': True}
    imported = client.post('/config/import', headers=headers, json=doc)
    assert imported.status_code == 200, imported.text
    assert imported.json()['task']['deep_research'] is True


def test_config_import_rejects_wrong_format(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    client = TestClient(app)
    headers = _login(client)
    r = client.post('/config/import', headers=headers, json={'format': 'other', 'settings': {}})
    assert r.status_code == 422


def test_runner_options_reach_contextvar():
    token = run_options.run_options.set({'fast_mode': True, 'system_prompt': 'x', 'max_steps': 5, 'browser': {}})
    try:
        opts = run_options.current()
        assert opts['fast_mode'] is True and opts['max_steps'] == 5
    finally:
        run_options.run_options.reset(token)


def test_console_has_new_tabs_and_controls():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / 'app' / 'static' / 'console.html').read_text()
    for marker in ('data-tab="agent"', 'data-tab="browser"', 'data-tab="config"', 'agentSave', 'browserSave',
                   'cfgExport', 'cfgFile', 'homeDeep', 'usagePill', '/usage', '/config/export', '/config/import'):
        assert marker in html, marker
    assert '$<span id="balance">' not in html


def test_worker_applies_owner_settings_and_flags(monkeypatch, tmp_path):
    """The worker must publish owner settings + fast/deep flags to the runner before execution starts."""
    import asyncio
    from app import main as main_mod
    _setup(monkeypatch, tmp_path)
    settings_store.save_settings(9, {'agent': {'system_prompt': 'owner rule', 'max_steps': 4},
                                     'browser': {'headless': True, 'viewport_width': 900, 'viewport_height': 700, 'user_agent': '', 'slow_mo_ms': 0}})
    item = task_store.create_task(owner_id=9, instruction='run a quick check', start_url=None, timeout_seconds=60,
                                  provider='auto', model=None, fast_mode=True, deep_research=True)
    seen = {}

    async def fake_execute(*args, **kwargs):
        seen.update(run_options.current())
        return '{"summary": "done", "actions": []}'

    monkeypatch.setattr(main_mod, 'execute_task', fake_execute)
    monkeypatch.setattr(main_mod, '_write_research_report', lambda *a, **k: None)
    asyncio.run(main_mod.run_task(item['id']))
    assert seen['fast_mode'] is True
    assert seen['deep_research'] is True
    assert seen['system_prompt'] == 'owner rule'
    assert seen['max_steps'] == 4
    assert seen['browser']['viewport_width'] == 900
