import asyncio
from pathlib import Path


def test_provider_502_identifies_selected_model(monkeypatch):
    import httpx
    from app import config, providers

    def handler(request):
        if request.url.path == '/v1/models':
            return httpx.Response(200, json={'data': [{'id': 'bad-model'}]})
        return httpx.Response(502, text='error code: 502')

    real_client = httpx.AsyncClient
    monkeypatch.setattr(config.settings, 'ollama_api_key', 'test-secret')
    monkeypatch.setattr(config.settings, 'ollama_base_url', 'https://router.test/v1')
    monkeypatch.setattr(config.settings, 'primary_model_cache_seconds', 0)
    monkeypatch.setattr(config.settings, 'primary_chat_retry_attempts', 1)
    monkeypatch.setattr(providers.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    try:
        asyncio.run(providers.engine_chat('ollama', [{'role': 'user', 'content': 'hello'}], 'bad-model'))
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
        return httpx.Response(200, json={'data': [{'id': 'recovered-model'}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(config.settings, 'ollama_api_key', 'test-secret')
    monkeypatch.setattr(config.settings, 'ollama_base_url', 'https://retry-router.test/v1')
    monkeypatch.setattr(config.settings, 'primary_model_cache_seconds', 0)
    monkeypatch.setattr(config.settings, 'primary_model_discovery_retry_base_seconds', 0)
    monkeypatch.setattr(providers.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    assert asyncio.run(providers.engine_models('ollama')) == ['recovered-model']
    assert calls['count'] == 2


def test_frontends_use_adaptive_polling_and_stop_at_terminal_states():
    for name in ('legacy/index-make-the-next-move.html', 'task.html'):
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
    assert 'no-store' in task_shell.headers.get('cache-control', '')


def test_completion_path_persists_done_checkpoint_without_claiming_proof():
    source = (Path(__file__).parents[1] / 'app' / 'runner.py').read_text()
    assert "action='done'" in source
    assert "'done', history" in source
    assert 'await save_checkpoint' in source
    # The model's own completion claim is not Phoenix-verified evidence.
    assert "verified=False, proxy_mode" in source


def test_task_status_exposes_redacted_checkpoint():
    from app.models import TaskStatus, now_utc
    task = TaskStatus(id='task', instruction='Inspect', status='completed', created_at=now_utc(), updated_at=now_utc(), checkpoint='{"action":"done","verified":true}')
    assert 'verified' in task.checkpoint


def test_model_discovery_404_falls_back_to_primary_model_only(monkeypatch):
    import httpx
    from app import config, providers

    def handler(request):
        return httpx.Response(404, text='not found')

    real_client = httpx.AsyncClient
    monkeypatch.setattr(config.settings, 'ollama_api_key', 'test-secret')
    monkeypatch.setattr(config.settings, 'ollama_base_url', 'http://ollama.test/v1')
    monkeypatch.setattr(config.settings, 'ollama_model', 'qwen2.5:0.5b')
    monkeypatch.setattr(config.settings, 'primary_model_cache_seconds', 0)
    monkeypatch.setattr(providers.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    assert asyncio.run(providers.engine_models('ollama')) == ['qwen2.5:0.5b']


def test_auto_chat_never_uses_gemini_without_a_saved_key(monkeypatch):
    import httpx
    from app import config, providers

    def handler(request):
        if request.url.path == '/v1/models':
            return httpx.Response(200, json={'data': [{'id': 'qwen2.5:0.5b'}]})
        return httpx.Response(500, text='boom')

    real_client = httpx.AsyncClient
    monkeypatch.setattr(config.settings, 'ollama_api_key', 'test-secret')
    monkeypatch.setattr(config.settings, 'ollama_base_url', 'http://ollama.test/v1')
    monkeypatch.setattr(config.settings, 'ollama_model', 'qwen2.5:0.5b')
    monkeypatch.setattr(config.settings, 'primary_model_cache_seconds', 0)
    monkeypatch.setattr(config.settings, 'primary_chat_retry_attempts', 1)
    monkeypatch.setattr(config.settings, 'gemini_api_key', '')
    monkeypatch.setattr(providers, '_user_keys', lambda: {})
    monkeypatch.setattr(providers.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    try:
        asyncio.run(providers.chat([{'role': 'user', 'content': 'hi'}], 'auto', None))
    except providers.ProviderError as exc:
        assert 'Gemini' not in str(exc)
    else:
        raise AssertionError('Expected ProviderError')


def test_auto_chat_uses_user_saved_key_after_primary_fails(monkeypatch):
    import httpx
    from app import config, providers

    def handler(request):
        if request.url.host == 'ollama.test':
            return httpx.Response(500, text='boom')
        if request.url.host == 'api.openai.com':
            return httpx.Response(200, json={'choices': [{'message': {'content': 'from openai'}}]})
        return httpx.Response(404)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(config.settings, 'ollama_api_key', 'test-secret')
    monkeypatch.setattr(config.settings, 'ollama_base_url', 'http://ollama.test/v1')
    monkeypatch.setattr(config.settings, 'ollama_model', 'qwen2.5:0.5b')
    monkeypatch.setattr(config.settings, 'primary_model_cache_seconds', 0)
    monkeypatch.setattr(config.settings, 'primary_chat_retry_attempts', 1)
    monkeypatch.setattr(providers, '_user_keys', lambda: {'openai': 'sk-user'})
    monkeypatch.setattr(providers.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    text, used, _ = asyncio.run(providers.chat([{'role': 'user', 'content': 'hi'}], 'auto', None))
    assert (text, used) == ('from openai', 'openai')
