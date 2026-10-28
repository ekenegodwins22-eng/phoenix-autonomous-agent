"""Per-task browser target: remote (BROWSER_WS) vs local, with env fallback when missing."""
from pathlib import Path

from app import browser_use_adapter as adapter
from app import config, run_options, task_store
from app.models import TaskRequest

ROOT = Path(__file__).parents[1]
WS = 'ws://remote.example:3000'


def _set_ws(monkeypatch, ws):
    if ws:
        monkeypatch.setenv('BROWSER_WS', ws)
    else:
        monkeypatch.delenv('BROWSER_WS', raising=False)


def _kw(target):
    tok = run_options.run_options.set({'browser': {}})
    try:
        return adapter._browser_profile_kwargs('direct', target)
    finally:
        run_options.run_options.reset(tok)


def test_request_accepts_browser_target_and_defaults_to_none():
    assert TaskRequest(instruction='open the site', browser_target='local').browser_target == 'local'
    assert TaskRequest(instruction='open the site').browser_target is None


def test_remote_target_attaches_over_cdp(monkeypatch):
    _set_ws(monkeypatch, WS)
    assert _kw('remote')['cdp_url'] == WS


def test_local_target_never_attaches_even_when_ws_set(monkeypatch):
    _set_ws(monkeypatch, WS)
    assert 'cdp_url' not in _kw('local')


def test_missing_target_keeps_env_behavior(monkeypatch):
    _set_ws(monkeypatch, WS)
    assert _kw(None)['cdp_url'] == WS
    _set_ws(monkeypatch, '')
    assert 'cdp_url' not in _kw(None)


def test_task_row_persists_browser_target(monkeypatch, tmp_path):
    monkeypatch.setattr(config.settings, 'database_url', '')
    monkeypatch.setattr(config.settings, 'database_path', str(tmp_path / 'target.db'))
    item = task_store.create_task(owner_id=1, instruction='check page', start_url=None, timeout_seconds=None,
                                  provider='auto', model=None, browser_target='local')
    assert task_store.get_task(item['id'])['browser_target'] == 'local'
    item2 = task_store.create_task(owner_id=1, instruction='check page two', start_url=None, timeout_seconds=None,
                                   provider='auto', model=None)
    assert task_store.get_task(item2['id'])['browser_target'] is None


def test_console_has_browser_dropdown_and_sends_target():
    html = (ROOT / 'app' / 'static' / 'console.html').read_text()
    assert 'id="homeBrowser"' in html
    assert 'value="remote"' in html and 'value="local"' in html
    assert 'Remote browser (worker)' in html and 'Local browser' in html
    assert 'body.browser_target=cfg.browser' in html
