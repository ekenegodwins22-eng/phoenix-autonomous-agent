from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).parents[1]


def test_console_route_serves_dashboard():
    client = TestClient(app)
    response = client.get('/console')
    assert response.status_code == 200
    assert 'Phoenix Console' in response.text
    assert 'text/html' in response.headers['content-type']


def test_console_uses_existing_rest_api_only():
    html = (ROOT / 'app' / 'static' / 'console.html').read_text()
    for path in ("'/auth/login'", "'/auth/register'", "'/tasks'", "'/models'", "'/proxy/status'", "'/runtime/status'", "'/logs'", "'/messages'", "/screenshot'", "action('cancel')", "action('retry')", "'/resume'"):
        assert path in html, path
    # the console must never receive or embed provider secrets
    assert 'PRIMARY_API_KEY' not in html and 'X-API-Key' not in html


def test_console_has_human_pause_and_checkpoint_views():
    html = (ROOT / 'app' / 'static' / 'console.html').read_text()
    for marker in ('pauseBanner', 'resumeBtn', 'Checkpoint &amp; resume', 'waiting_human', 'Models &amp; proxy', 'logs'):
        assert marker in html, marker


def test_console_carries_mit_attribution():
    html = (ROOT / 'app' / 'static' / 'console.html').read_text()
    assert 'browser-use' in html and 'MIT' in html
    notice = (ROOT / 'NOTICE.md').read_text()
    assert 'MIT License' in notice and 'browser-use' in notice


def test_console_wires_effort_uploads_artifacts_and_share():
    html = (ROOT / 'app' / 'static' / 'console.html').read_text()
    for path in ("uploads:'/uploads'", "artifacts:'/artifacts'", "summary:'/summary'", "homeEffort", "taskAttach", "shareBtn", "filesBtn", "artifactList"):
        assert path in html, path


def test_upload_artifacts_and_summary_round_trip(tmp_path, monkeypatch):
    from app import main as m
    client = TestClient(app)
    email = 'console-upload@example.com'
    client.post('/auth/register', json={'email': email, 'password': 'correct-horse-7'})
    token = client.post('/auth/login', json={'email': email, 'password': 'correct-horse-7'}).json()['access_token']
    h = {'Authorization': 'Bearer ' + token}
    monkeypatch.setattr(m, 'UPLOAD_ROOT', tmp_path)
    async def _noop(task_id):
        return None
    monkeypatch.setattr(m, 'start_task', _noop)
    created = client.post('/tasks', json={'instruction': 'check upload wiring', 'effort': 'low'}, headers=h)
    assert created.status_code == 202, created.text
    body = created.json()
    assert body['effort'] == 'low'
    ref = body['slug']
    up = client.post(f'/tasks/{ref}/uploads', headers=h, files={'file': ('notes.txt', b'hello', 'text/plain')})
    assert up.status_code == 201, up.text
    arts = client.get(f'/tasks/{ref}/artifacts', headers=h).json()['artifacts']
    assert arts and arts[0]['name'] == 'notes.txt'
    dl = client.get(arts[0]['url'], headers=h)
    assert dl.status_code == 200 and dl.content == b'hello'
    summary = client.get(f'/tasks/{ref}/summary', headers=h).json()
    assert 'check upload wiring' in summary['summary'] and 'Effort: low' in summary['summary']
    assert client.post(f'/tasks/{ref}/uploads', headers=h, files={'file': ('x.txt', b'', 'text/plain')}).status_code == 422


def test_console_task_view_has_framed_live_browser_hero():
    html = (ROOT / 'app' / 'static' / 'console.html').read_text()
    for marker in ('heroBrowser', 'heroShot', 'heroRefresh', 'heroRef', 'object-fit:contain', 'aspect-ratio:16/9', 'refreshHero', 'setInterval(refreshHero,2000)'):
        assert marker in html, marker
    # the hero polls the same endpoint as the screenshot pane
    assert "EP.shot" in html and "'/tasks/'+encodeURIComponent(ref)+EP.shot" in html
