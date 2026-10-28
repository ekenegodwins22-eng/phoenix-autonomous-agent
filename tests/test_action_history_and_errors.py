"""Action history, real error surfacing and download passthrough for the browser-use engine."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import browser_use_adapter as adapter

ROOT = Path(__file__).resolve().parents[1]


def _action(**data):
    return SimpleNamespace(model_dump=lambda exclude_none=True, mode='json': dict(data))


def _item(actions=(), errors=(), extracted=None, is_done=False):
    results = [SimpleNamespace(error=e, extracted_content=extracted, is_done=is_done) for e in errors]
    if not results:
        results = [SimpleNamespace(error=None, extracted_content=extracted, is_done=is_done)]
    return SimpleNamespace(model_output=SimpleNamespace(action=list(actions)), result=results)


class _History:
    def __init__(self, items):
        self.history = items

    def final_result(self):
        return self.history[-1].result[-1].extracted_content if self.history else None

    def is_done(self):
        return bool(self.history and self.history[-1].result[-1].is_done)


def test_summarize_history_lists_each_action_with_step_and_target():
    history = _History([
        _item([_action(navigate={'url': 'https://example.com'})]),
        _item([_action(click={'index': 4})]),
        _item([_action(done={'text': 'found it'})], extracted='found it', is_done=True),
    ])
    out = adapter.summarize_history(history)
    assert out['done'] is True
    assert out['final'] == 'found it'
    assert out['steps'] == 3
    assert [(a['step'], a['action'], a['target']) for a in out['actions']] == [
        (1, 'navigate', 'https://example.com'),
        (2, 'click', '4'),
        (3, 'done', None),
    ]


def test_summarize_history_redacts_typed_values():
    history = _History([_item([_action(input_text={'index': 2, 'text': 'hunter2-secret'})])])
    out = adapter.summarize_history(history)
    assert out['actions'][0]['params']['text'] == '[redacted]'
    assert 'hunter2-secret' not in json.dumps(out)


def test_summarize_history_surfaces_the_real_last_error_and_not_done():
    history = _History([
        _item([_action(click={'index': 1})], errors=['Element not found: index 1']),
        _item([_action(wait={'seconds': 1})], errors=['Model call failed: 502 upstream unavailable']),
    ])
    out = adapter.summarize_history(history)
    assert out['done'] is False
    assert out['last_error'] == 'Model call failed: 502 upstream unavailable'


def test_describe_step_reports_actions_and_failure_text():
    agent = SimpleNamespace(
        state=SimpleNamespace(n_steps=5),
        history=SimpleNamespace(history=[_item([_action(scroll={'down': True})], errors=['timeout while scrolling'])]),
    )
    line = adapter._describe_step(agent)
    assert line.startswith('Step 5: scroll')
    assert 'timeout while scrolling' in line


def test_incomplete_run_is_not_reported_as_verified(monkeypatch):
    """A run that never reached done must raise TaskIncomplete with the real reason, not succeed."""
    import asyncio
    from app.runner import TaskIncomplete

    class FakeSession:
        downloaded_files = []

        def __init__(self, **kwargs):
            pass

        async def stop(self):
            return None

    class FakeAgent:
        def __init__(self, **kwargs):
            self.state = SimpleNamespace(n_steps=0)

        async def run(self, max_steps, on_step_start=None, on_step_end=None):
            return _History([_item([_action(click={'index': 1})], errors=['Model call failed: 500 boom'])])

    import browser_use
    import browser_use.browser.profile as profile
    monkeypatch.setattr(browser_use, 'Agent', FakeAgent, raising=False)
    monkeypatch.setattr(browser_use, 'BrowserSession', FakeSession, raising=False)
    monkeypatch.setattr(profile, 'BrowserProfile', lambda **kw: SimpleNamespace(**kw), raising=False)
    monkeypatch.setattr(adapter, 'browser_use_available', lambda: True)
    monkeypatch.setattr(adapter, '_browser_profile_kwargs', lambda proxy_mode='direct', browser_target=None: {})
    monkeypatch.setattr(adapter, 'PhoenixRouterChatModel', lambda **kw: object())

    with pytest.raises(TaskIncomplete) as exc:
        asyncio.run(adapter.execute_browser_use_task('do the thing'))
    assert 'Model call failed: 500 boom' in str(exc.value)


def test_console_renders_result_actions_not_raw_json():
    html = (ROOT / 'app' / 'static' / 'console.html').read_text()
    assert 'function resultHTML(' in html
    assert 'resultHTML(t.result)' in html
    assert 'Actions (' in html
    assert 'Downloaded: ' in html
