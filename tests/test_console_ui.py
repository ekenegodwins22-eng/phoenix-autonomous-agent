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
