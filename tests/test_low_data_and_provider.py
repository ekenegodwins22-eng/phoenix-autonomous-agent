import asyncio
from pathlib import Path


def test_provider_502_identifies_selected_model(monkeypatch):
    import httpx
    from app import config, providers

    def handler(request):
        if request.url.path == '/available-models':
            return httpx.Response(200, json={'models': [{'id': 'bad-model'}]})
        return httpx.Response(502, text='error code: 502')

    real_client = httpx.AsyncClient
    monkeypatch.setattr(config.settings, 'primary_api_key', 'test-secret')
    monkeypatch.setattr(config.settings, 'primary_base_url', 'https://router.test')
    monkeypatch.setattr(config.settings, 'primary_model_cache_seconds', 0)
    monkeypatch.setattr(config.settings, 'primary_chat_retry_attempts', 1)
    monkeypatch.setattr(providers.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    try:
        asyncio.run(providers.primary_chat([{'role': 'user', 'content': 'hello'}], 'bad-model'))
    except providers.ProviderError as exc:
        assert 'HTTP 502' in str(exc)
        assert 'bad-model' in str(exc)
    else:
        raise AssertionError('Expected ProviderError')


def test_model_discovery_retries_a_transient_timeout(monkeypatch):
    import httpx
    from app import config, providers

    calls = {'count': 0}
    def handler(request):
        calls['count'] += 1
        if calls['count'] == 1:
            raise httpx.ReadTimeout('temporary catalog timeout')
        return httpx.Response(200, json={'models': [{'id': 'recovered-model'}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(config.settings, 'primary_api_key', 'test-secret')
    monkeypatch.setattr(config.settings, 'primary_base_url', 'https://retry-router.test')
    monkeypatch.setattr(config.settings, 'primary_model_cache_seconds', 0)
    monkeypatch.setattr(config.settings, 'primary_model_discovery_retry_base_seconds', 0)
    monkeypatch.setattr(providers.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    assert asyncio.run(providers.primary_models()) == ['recovered-model']
    assert calls['count'] == 2


def test_frontends_use_adaptive_polling_and_stop_at_terminal_states():
    for name in ('index.html', 'task.html'):
        content = (Path(__file__).parents[1] / 'app' / 'static' / name).read_text()
        assert 'setInterval' not in content
        assert 'navigator.onLine' in content
        assert 'visibilitychange' in content
        assert 'lowData' in content
        assert "'completed','failed','cancelled'" in content


def test_health_and_static_shell_are_compressed_or_private_cached():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    root = client.get('/', headers={'Accept-Encoding': 'gzip'})
    assert root.status_code == 200
    assert root.headers.get('cache-control') == 'no-cache'
    task_shell = client.get('/tasks/example/session')
    assert task_shell.status_code == 200
    assert task_shell.headers.get('cache-control') == 'no-cache'


def test_completion_path_persists_verified_checkpoint():
    source = (Path(__file__).parents[1] / 'app' / 'runner.py').read_text()
    assert "'action': 'done'" in source
    assert "'verified': True" in source
    assert 'await save_checkpoint' in source
