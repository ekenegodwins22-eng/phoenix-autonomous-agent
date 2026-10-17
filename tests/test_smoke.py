from fastapi.testclient import TestClient
from app.main import app


def test_health():
    response = TestClient(app).get('/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'


def test_tasks_require_auth():
    response = TestClient(app).get('/tasks')
    assert response.status_code == 401


def test_registration_has_no_verification(monkeypatch, tmp_path):
    from app import config, auth
    monkeypatch.setattr(config.settings, 'database_path', str(tmp_path / 'users.db'))
    monkeypatch.setattr(auth.settings, 'database_path', str(tmp_path / 'users.db'))
    client = TestClient(app)
    response = client.post('/auth/register', json={'email':'phoenix-test@example.com','password':'correct-horse-7'})
    assert response.status_code == 201
    assert response.json()['email_verification_required'] is False
    login = client.post('/auth/login', json={'email':'phoenix-test@example.com','password':'correct-horse-7'})
    assert login.status_code == 200
    assert login.json()['access_token']


def test_task_request_accepts_provider_and_model():
    from app.models import TaskRequest
    task = TaskRequest(instruction='Inspect the page', provider='primary', model='phoenix-model-v1')
    assert task.provider == 'primary'
    assert task.model == 'phoenix-model-v1'


def test_task_request_defaults_to_auto():
    from app.models import TaskRequest
    task = TaskRequest(instruction='Inspect the page')
    assert task.provider == 'auto'
    assert task.model is None


def test_phoenix_model_catalog_parser_handles_custom_router_shapes():
    from app.providers import _extract_model_ids
    assert _extract_model_ids({"models": [{"id": "deepseek-v4-flash"}, {"name": "glm-5.3"}]}) == ["deepseek-v4-flash", "glm-5.3"]
    assert _extract_model_ids(["model-one", "model-two"]) == ["model-one", "model-two"]


def test_phoenix_chat_response_parser_handles_custom_router_shapes():
    from app.providers import _extract_chat_text
    assert _extract_chat_text({"response": "Hello"}) == "Hello"
    assert _extract_chat_text({"data": {"content": "Nested hello"}}) == "Nested hello"


def test_phoenix_headers_use_x_api_key(monkeypatch):
    from app import config
    from app.providers import _primary_headers
    monkeypatch.setattr(config.settings, 'primary_api_key', 'test-secret')
    headers = _primary_headers()
    assert headers['X-API-Key'] == 'test-secret'
    assert 'Authorization' not in headers


def test_phoenix_model_discovery_requires_key(monkeypatch):
    import asyncio
    import pytest
    from app import config
    from app.providers import ProviderError, primary_models
    monkeypatch.setattr(config.settings, 'primary_api_key', '')
    with pytest.raises(ProviderError, match='PRIMARY_API_KEY'):
        asyncio.run(primary_models())


def test_mailbox_identifiers_reject_path_traversal():
    import pytest
    from app.mailbox import MailboxClient, MailboxError
    with pytest.raises(MailboxError):
        MailboxClient._identifier('../secret', 'message ID')


def test_runner_detects_captcha_without_attempting_bypass():
    from app.runner import _challenge_hint, _challenge_info
    assert _challenge_hint('Please complete the reCAPTCHA challenge', [])
    info = _challenge_info('reCAPTCHA offers an audio accessibility challenge', [])
    assert info['type'] == 'recaptcha'
    assert 'authorized_site_accessible_alternative' in info['resolution_methods']
    assert _challenge_hint('Normal page content', []) is None


def test_task_status_supports_human_handoff():
    from app.models import TaskStatus, now_utc
    task = TaskStatus(id='task1', instruction='verify', status='waiting_human', progress='Paused', human_help='Complete CAPTCHA', created_at=now_utc(), updated_at=now_utc())
    assert task.status == 'waiting_human'
    assert task.human_help == 'Complete CAPTCHA'


def test_browser_retry_recovers_from_transient_failure():
    import asyncio
    from app.runner import _browser_retry
    attempts = {'count': 0}
    async def operation():
        attempts['count'] += 1
        if attempts['count'] == 1:
            raise RuntimeError('temporary')
        return 'ok'
    assert asyncio.run(_browser_retry(operation)) == 'ok'
    assert attempts['count'] == 2


def test_mailbox_finds_verification_code_without_returning_message_body():
    import asyncio
    from app.mailbox import MailboxClient
    client = MailboxClient()
    async def messages(**kwargs):
        return [{'id': 7, 'subject': 'Your verification code', 'body_text': 'Use 482931 to continue.'}]
    client.list_messages = messages
    result = asyncio.run(client.find_verification_code())
    assert result == {'code': '482931', 'message_id': 7, 'subject': 'Your verification code'}
    assert 'body_text' not in result


def test_phoenix_chat_and_stream_use_custom_routes_and_auth(monkeypatch):
    import asyncio
    import httpx
    from app import config, providers

    seen = []
    def handler(request):
        seen.append((request.method, request.url.path, request.headers.get('x-api-key'), request.content))
        if request.url.path == '/available-models':
            return httpx.Response(200, json={'models': [{'id': 'live-model'}]})
        if request.url.path == '/chat':
            return httpx.Response(200, json={'response': 'complete'})
        if request.url.path == '/chat/stream':
            return httpx.Response(200, content=b'data: {"token":"streamed"}\ndata: [DONE]\n', headers={'content-type': 'text/event-stream'})
        return httpx.Response(404)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(config.settings, 'primary_api_key', 'test-secret')
    monkeypatch.setattr(config.settings, 'primary_base_url', 'https://router.test')
    monkeypatch.setattr(providers.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))

    async def run():
        messages = [{'role': 'user', 'content': 'hello'}]
        assert await providers.primary_chat(messages, 'live-model') == 'complete'
        pieces = [piece async for piece in providers.primary_chat_stream(messages, 'live-model')]
        assert pieces == ['streamed']
    asyncio.run(run())
    assert [path for _, path, _, _ in seen] == ['/available-models', '/chat', '/available-models', '/chat/stream']
    assert all(key == 'test-secret' for _, _, key, _ in seen)
