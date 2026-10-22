import asyncio
from fastapi.testclient import TestClient
from app import browser_use_adapter as adapter
from app import run_options


def test_latest_frame_empty_until_captured():
    assert adapter.latest_frame('no-such-task') is None
    adapter._latest_frames['t1'] = b'\x89PNG'
    assert adapter.latest_frame('t1') == b'\x89PNG'
    adapter._latest_frames.pop('t1', None)


def test_profile_kwargs_map_owner_browser_settings():
    tok = run_options.run_options.set({'browser': {'headless': False, 'viewport_width': 1024,
                                                   'viewport_height': 768, 'user_agent': 'UA/1'}})
    try:
        kw = adapter._browser_profile_kwargs('direct')
    finally:
        run_options.run_options.reset(tok)
    assert kw['headless'] is False
    assert kw['viewport'] == {'width': 1024, 'height': 768}
    assert kw['user_agent'] == 'UA/1'
    assert 'proxy' not in kw


def test_profile_kwargs_defaults_when_no_owner_settings():
    kw = adapter._browser_profile_kwargs('direct')
    assert 'viewport' not in kw and 'user_agent' not in kw
    assert isinstance(kw['headless'], bool)


def test_screenshot_404_for_unknown_task_and_409_without_browser(monkeypatch):
    from app import main
    client = TestClient(main.app)
    email = 'shot_test@example.com'
    client.post('/auth/register', json={'email': email, 'password': 'pass-1234-long'})
    r = client.post('/auth/login', json={'email': email, 'password': 'pass-1234-long'})
    token = r.json().get('access_token')
    h = {'Authorization': f'Bearer {token}'}
    assert client.get('/tasks/does-not-exist/screenshot', headers=h).status_code == 404
