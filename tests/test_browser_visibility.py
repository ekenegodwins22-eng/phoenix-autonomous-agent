from pathlib import Path


def test_task_session_contains_authenticated_remote_browser_controls():
    html = (Path(__file__).parents[1] / 'app' / 'static' / 'task.html').read_text()
    assert 'Remote browser verification' in html
    assert "'/tasks/'+ref+'/screenshot'" in html
    assert "'/tasks/'+ref+'/browser-stream'" in html
    assert "'/tasks/'+ref+'/browser-action'" in html
    assert 'Start live browser' in html
    assert "control('resume')" in html


def test_browser_visibility_endpoints_require_authentication():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    assert client.get('/tasks/example/screenshot').status_code == 401
    assert client.get('/tasks/example/browser-stream').status_code == 401
    assert client.post('/tasks/example/browser-action', json={'action': 'inspect'}).status_code == 401


def test_task_session_shell_is_available_for_mobile_reconnect():
    from fastapi.testclient import TestClient
    from app.main import app
    response = TestClient(app).get('/tasks/example/session')
    assert response.status_code == 200
    assert 'Remote browser verification' in response.text
    assert response.headers.get('cache-control') == 'no-cache'
