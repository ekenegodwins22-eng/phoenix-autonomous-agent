from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_home_ui_has_authenticated_recent_run_activity():
    html = (ROOT / 'app' / 'static' / 'index.html').read_text()
    assert 'Continue where you left off' in html
    assert 'id="recentRuns"' in html
    assert 'async function loadRecentTasks' in html
    assert "api('/tasks')" in html
    assert 'recent-run-state' in html


def test_frontend_keeps_durable_task_session_controls():
    html = (ROOT / 'app' / 'static' / 'task.html').read_text()
    for marker in ("/tasks/'+ref+'/messages", 'browser-stream', 'browser-action', 'lowDataButton', 'stopLiveBrowser'):
        assert marker in html
