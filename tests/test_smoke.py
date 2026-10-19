from fastapi.testclient import TestClient
from app.main import app


def test_health():
    response = TestClient(app).get('/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'
    assert 'resources' in response.json()
    assert response.json()['agent_instructions']['loaded'] is True
    assert len(response.json()['agent_instructions']['version']) == 64


def test_health_uses_fast_browser_presence_check(monkeypatch):
    from app import main
    seen = {}
    def fake_status(*, verify_launch=True):
        seen['verify_launch'] = verify_launch
        return {'available': True, 'error': None}
    monkeypatch.setattr(main, 'browser_installation_status', fake_status)
    assert TestClient(app).get('/health').status_code == 200
    assert seen['verify_launch'] is False


def test_runtime_status_requires_auth():
    assert TestClient(app).get('/runtime/status').status_code == 401


def test_runtime_cleanup_only_removes_expired_designated_temp_files(monkeypatch, tmp_path):
    import os
    from app import config
    from app.runtime import cleanup_runtime_temp
    monkeypatch.setattr(config.settings, 'runtime_temp_dir', str(tmp_path))
    monkeypatch.setattr(config.settings, 'runtime_temp_retention_seconds', 0)
    old = tmp_path / 'old.tmp'
    old.write_text('temporary')
    os.utime(old, (1, 1))
    important = tmp_path.parent / 'important.txt'
    important.write_text('keep')
    result = cleanup_runtime_temp()
    assert result['removed_entries'] == 1
    assert not old.exists()
    assert important.exists()


def test_browser_installation_status_reports_missing_binary(monkeypatch):
    import sys
    import types
    from app import runtime
    class MissingChromium:
        executable_path = '/does/not/exist'
    class FakePlaywright:
        chromium = MissingChromium()
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', types.SimpleNamespace(sync_playwright=lambda: FakePlaywright()))
    assert runtime.browser_installation_status()['available'] is False


def test_tasks_require_auth():
    response = TestClient(app).get('/tasks')
    assert response.status_code == 401


def test_durable_tasks_have_unique_slugs_and_persist_logs(monkeypatch, tmp_path):
    from app import config, task_store
    monkeypatch.setattr(config.settings, 'database_path', str(tmp_path / 'tasks.db'))
    first = task_store.create_task(owner_id=7, instruction='Register service', start_url=None, timeout_seconds=30, provider='primary', model='model-a')
    second = task_store.create_task(owner_id=7, instruction='Register service', start_url=None, timeout_seconds=30, provider='primary', model='model-a')
    assert first['id'] != second['id']
    assert first['slug'] != second['slug']
    assert task_store.get_task(first['slug'], owner_id=7)['instruction'] == 'Register service'
    task_store.add_log(first['id'], 'Verified navigation complete')
    assert task_store.get_logs(first['id'], owner_id=7)[-1]['message'] == 'Verified navigation complete'


def test_task_claim_prevents_duplicate_workers(monkeypatch, tmp_path):
    from app import config, task_store
    monkeypatch.setattr(config.settings, 'database_path', str(tmp_path / 'claim.db'))
    task = task_store.create_task(owner_id=1, instruction='Inspect', start_url=None, timeout_seconds=30, provider='auto', model=None)
    assert task_store.claim_task(task['id'], 'worker-a') is True
    assert task_store.claim_task(task['id'], 'worker-b') is False


def test_task_cancellation_is_server_side_and_restart_does_not_replay(monkeypatch, tmp_path):
    from app import config, task_store
    monkeypatch.setattr(config.settings, 'database_path', str(tmp_path / 'recovery.db'))
    queued = task_store.create_task(owner_id=1, instruction='Queued task', start_url=None, timeout_seconds=30, provider='auto', model=None)
    recoverable = task_store.create_task(owner_id=1, instruction='Recoverable task', start_url=None, timeout_seconds=30, provider='auto', model=None)
    active = task_store.create_task(owner_id=1, instruction='Active task', start_url=None, timeout_seconds=30, provider='auto', model=None)
    assert task_store.request_cancel(queued['id']) is True
    assert task_store.is_cancel_requested(queued['id']) is True
    assert task_store.claim_task(active['id'], 'worker-a') is True
    restart_queue = task_store.recover_after_restart()
    assert recoverable['id'] in restart_queue
    assert task_store.get_task(queued['id'])['status'] == 'cancelled'
    assert task_store.get_task(active['id'])['status'] == 'failed'
    assert 'not replayed' in task_store.get_task(active['id'])['error']


def test_task_chat_messages_persist_and_are_claimed_once(monkeypatch, tmp_path):
    from app import config, task_store
    monkeypatch.setattr(config.settings, 'database_path', str(tmp_path / 'messages.db'))
    task = task_store.create_task(owner_id=3, instruction='Inspect', start_url=None, timeout_seconds=30, provider='auto', model=None)
    sent = task_store.add_message(task['id'], 3, 'user', 'Use the other button instead')
    assert sent['role'] == 'user'
    assert task_store.get_messages(task['id'], owner_id=3)[0]['content'] == 'Use the other button instead'
    claimed = task_store.claim_unconsumed_user_messages(task['id'])
    assert [message['id'] for message in claimed] == [sent['id']]
    assert task_store.claim_unconsumed_user_messages(task['id']) == []


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


def test_model_prose_gets_corrective_retry_instead_of_false_completion(monkeypatch):
    import asyncio
    from app import runner
    responses = iter(['I will inspect the page first.', '{"action":"inspect"}'])
    async def fake_chat(*args, **kwargs):
        return next(responses), 'primary', 'test-model'
    monkeypatch.setattr(runner, 'chat', fake_chat)
    action, provider, model = asyncio.run(runner._choose_action('Inspect the page', [], {'url':'https://example.test'}, 1, 'primary', 'test-model'))
    assert action == {'action': 'inspect'}
    assert provider == 'primary'
    assert model == 'test-model'


def test_agent_md_is_loaded_into_active_prompt(monkeypatch):
    import asyncio
    from app import runner
    captured = {}
    async def fake_chat(messages, **kwargs):
        captured['system'] = messages[0]['content']
        return '{"action":"inspect"}', 'primary', 'test-model'
    monkeypatch.setattr(runner, 'chat', fake_chat)
    asyncio.run(runner._choose_action('Inspect', [], {'url':'https://example.test'}, 1, 'primary', 'test-model'))
    assert 'Phoenix Agent Behavior' in captured['system']
    assert 'ACTIVE AGENT INSTRUCTIONS (version ' in captured['system']
    assert 'Do not replace execution with advice.' in captured['system']


def test_missing_agent_md_is_a_configuration_error(monkeypatch):
    import asyncio
    from pathlib import Path
    import pytest
    from app import instructions, runner
    monkeypatch.setattr(instructions, 'AGENT_PATH', Path('/tmp/phoenix-agent-file-that-does-not-exist'))
    with pytest.raises(instructions.InstructionConfigError):
        asyncio.run(runner._choose_action('Inspect', [], {'url':'https://example.test'}, 1, 'primary', 'test-model'))


def test_invalid_start_url_is_blocked_not_reported_as_completed():
    import asyncio
    import pytest
    from app.runner import TaskBlocked, execute_task
    with pytest.raises(TaskBlocked, match='Invalid start URL'):
        asyncio.run(execute_task('Open the site', start_url='not-a-url'))


def test_redacted_checkpoint_persists_last_verified_action(monkeypatch, tmp_path):
    from app import config, task_store
    monkeypatch.setattr(config.settings, 'database_path', str(tmp_path / 'checkpoint.db'))
    task = task_store.create_task(owner_id=3, instruction='Inspect', start_url=None, timeout_seconds=30, provider='auto', model=None)
    task_store.save_checkpoint(task['id'], {'step': 2, 'action': 'click', 'target': 'Next', 'url': 'https://example.test'})
    saved = task_store.get_task(task['id'])['checkpoint']
    assert 'click' in saved
    assert 'password' not in saved.lower()


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


def test_random_credentials_have_expected_properties():
    from app.credentials import generate_password, generate_username
    password = generate_password(20)
    username = generate_username('My App')
    assert len(password) == 20
    assert any(character.isupper() for character in password)
    assert any(character.isdigit() for character in password)
    assert username.startswith('myapp_')
    assert len(username) <= 26


def test_mailbox_prepare_address_reuses_active_alias():
    import asyncio
    from app.mailbox import MailboxClient
    client = MailboxClient()
    async def aliases():
        return [{'id': 12, 'address': 'alias@example.com', 'is_active': True, 'gmail_account_id': 3}]
    async def accounts():
        raise AssertionError('accounts should not be queried when a suitable alias exists')
    client.list_aliases = aliases
    client.list_accounts = accounts
    result = asyncio.run(client.prepare_address(variation_type='plus'))
    assert result['address'] == 'alias@example.com'
    assert result['alias_id'] == 12
    assert result['reused'] is True


def test_terminal_task_reopens_for_follow_up_message(monkeypatch, tmp_path):
    from app import config, task_store
    monkeypatch.setattr(config.settings, 'database_path', str(tmp_path / 'follow-up.db'))
    task = task_store.create_task(owner_id=3, instruction='Inspect', start_url=None, timeout_seconds=30, provider='auto', model=None)
    task_store.set_state(task['id'], 'completed', result='done')
    reopened = task_store.requeue_for_message(task['id'])
    assert reopened['id'] == task['id']
    assert reopened['status'] == 'queued'
    assert task_store.get_task(task['slug'], owner_id=3)['id'] == task['id']


def test_registration_email_detection_uses_email_control_and_ignores_explicit_email():
    from app.runner import _registration_needs_mailbox_email
    state = {'controls': [{'type': 'email', 'name': 'email', 'label': 'Email address'}]}
    assert _registration_needs_mailbox_email('Register a new account', state) is True
    assert _registration_needs_mailbox_email('Register using person@example.com', state) is False


def test_mailbox_restore_session_validates_email():
    import pytest
    from app.mailbox import MailboxClient, MailboxError
    with pytest.raises(MailboxError):
        MailboxClient._email('not-an-email')


def test_mailbox_bootstraps_temp_session_cookie(monkeypatch):
    import asyncio
    import httpx
    from app.mailbox import MailboxClient
    client = MailboxClient()
    seen = []
    def handler(request):
        seen.append((request.url.path, request.headers.get('cookie')))
        if request.url.path == '/aliases' and not request.headers.get('cookie'):
            return httpx.Response(401, json={'detail': 'Not authenticated'})
        if request.url.path == '/session':
            return httpx.Response(200, json={'id': 1}, headers={'set-cookie': 'temp_session=test-session; Path=/;'})
        return httpx.Response(200, json=[])
    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def request(self, method, url, **kwargs):
            request = httpx.Request(method, url, headers=kwargs.get('headers'))
            response = handler(request)
            response.request = request
            return response
        async def get(self, url, **kwargs): return await self.request('GET', url, **kwargs)
    monkeypatch.setattr(client, 'base', 'https://mailbox.test')
    monkeypatch.setattr('app.mailbox.httpx.AsyncClient', lambda **kwargs: FakeClient())
    assert asyncio.run(client.list_aliases()) == []
    assert seen == [('/aliases', None), ('/session', None), ('/aliases', 'temp_session=test-session')]


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


def test_phoenix_chat_retries_transient_502_with_bounded_backoff(monkeypatch):
    import asyncio
    import httpx
    from app import config, providers
    calls = {'chat': 0}
    def handler(request):
        if request.url.path == '/available-models':
            return httpx.Response(200, json={'models': [{'id': 'live-model'}]})
        if request.url.path == '/chat':
            calls['chat'] += 1
            if calls['chat'] < 3:
                return httpx.Response(502, json={'detail': 'upstream unavailable'})
            return httpx.Response(200, json={'response': 'recovered'})
        return httpx.Response(404)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(config.settings, 'primary_api_key', 'test-secret')
    monkeypatch.setattr(config.settings, 'primary_base_url', 'https://router.test')
    monkeypatch.setattr(config.settings, 'primary_chat_retry_attempts', 3)
    monkeypatch.setattr(config.settings, 'primary_chat_retry_base_seconds', 0)
    monkeypatch.setattr(providers.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    assert asyncio.run(providers.primary_chat([{'role':'user','content':'hello'}], 'live-model')) == 'recovered'
    assert calls['chat'] == 3


def test_phoenix_chat_does_not_retry_authentication_errors(monkeypatch):
    import asyncio
    import httpx
    from app import config, providers
    calls = {'chat': 0}
    def handler(request):
        if request.url.path == '/available-models':
            return httpx.Response(200, json={'models': [{'id': 'live-model'}]})
        calls['chat'] += 1
        return httpx.Response(401, json={'detail': 'Not authenticated'})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(config.settings, 'primary_api_key', 'test-secret')
    monkeypatch.setattr(config.settings, 'primary_base_url', 'https://router.test')
    monkeypatch.setattr(providers.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    try:
        asyncio.run(providers.primary_chat([{'role':'user','content':'hello'}], 'live-model'))
    except providers.ProviderError:
        pass
    else:
        raise AssertionError('Expected ProviderError')
    assert calls['chat'] == 1


def test_synthetic_form_data_classifies_fields_and_respects_constraints():
    from app.form_data import classify_field, constrained_value, synthetic_profile
    profile = synthetic_profile()
    assert classify_field({'type':'email','label':'Work email'}) == 'email'
    assert classify_field({'type':'number','label':'Age','min':'18','max':'65'}) == 'number'
    assert classify_field({'type':'text','label':'Unknown custom field'}) is None
    assert constrained_value({'maxlength':8}, 'first_name', profile) == 'Test'
    assert constrained_value({'minlength':12,'maxlength':20}, 'username', profile) is not None
    assert profile['full_name'].startswith(profile['first_name'])
    assert profile['email'].endswith('@example.test')


def test_synthetic_form_data_handles_html_types_and_validation_ranges():
    from app.form_data import classify_field, constrained_value, synthetic_profile
    profile = synthetic_profile()
    assert classify_field({'type':'password','name':'account_password'}) == 'password'
    assert classify_field({'tag':'select','type':'select-one','label':'Country'}) == 'select'
    assert classify_field({'tag':'textarea','label':'Description','type':'textarea'}) == 'description'
    assert constrained_value({'min':'18','max':'21'}, 'number', profile) == '18'
    assert constrained_value({'min':'2020-01-01','max':'2020-12-31'}, 'date', profile) == '2020-01-01'
