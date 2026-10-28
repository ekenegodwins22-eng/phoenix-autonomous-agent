from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).parents[1]


def test_root_serves_landing_page():
    response = TestClient(app).get('/')
    assert response.status_code == 200
    assert 'text/html' in response.headers['content-type']
    assert 'Autonomous browsing' in response.text
    assert 'Background tasks' in response.text
    assert 'Checkpoint resume' in response.text
    assert '/console#signup' in response.text


def test_console_still_served_and_auth_hash_supported():
    response = TestClient(app).get('/console')
    assert response.status_code == 200
    assert 'Phoenix Console' in response.text
    html = (ROOT / 'app' / 'static' / 'console.html').read_text()
    assert "location.hash==='#signup'" in html
    assert 'id="authForm"' in html and 'id="email"' in html and 'id="password"' in html
