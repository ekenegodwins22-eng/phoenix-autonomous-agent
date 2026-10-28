import asyncio


def test_browser_use_adapter_is_optional_and_lazy():
    from app.browser_use_adapter import browser_use_available

    assert isinstance(browser_use_available(), bool)


def test_runner_dispatches_to_browser_use_only_when_configured(monkeypatch):
    from app import runner

    calls = []

    async def fake_execute(*args, **kwargs):
        calls.append(args)
        return '{"engine":"browser_use"}'

    async def save_checkpoint(value):
        calls.append(value)

    monkeypatch.setattr(runner.settings, 'execution_engine', 'browser_use')
    monkeypatch.setattr('app.browser_use_adapter.execute_browser_use_task', fake_execute)
    result = asyncio.run(runner.execute_task('Inspect the page', save_checkpoint=save_checkpoint))

    assert result == '{"engine":"browser_use"}'
    assert calls[0][0] == 'Inspect the page'
    assert calls[1]['engine'] == 'browser_use'


def test_vendored_browser_use_is_the_default_engine():
    from app.config import settings

    assert settings.execution_engine == 'browser_use'


def test_phoenix_router_model_implements_browser_use_invoke_contract(monkeypatch):
    from types import SimpleNamespace
    from app import browser_use_adapter

    seen = []

    async def fake_chat(messages, provider, model):
        seen.append((messages, provider, model))
        return 'browser-use response', 'primary', model

    monkeypatch.setattr(browser_use_adapter, 'chat', fake_chat)
    model = browser_use_adapter.PhoenixRouterChatModel(provider='primary', model='test-model')
    completion = asyncio.run(model.ainvoke([SimpleNamespace(role='user', content='Inspect the page')]))

    assert completion.completion == 'browser-use response'
    assert seen == [([{'role': 'user', 'content': 'Inspect the page'}], 'primary', 'test-model')]


def test_vendored_browser_use_is_the_engine_on_path():
    from app import browser_use_adapter
    import browser_use

    vendor = str(browser_use_adapter.VENDOR_DIR)
    assert browser_use.__file__.startswith(vendor)
    assert (browser_use_adapter.VENDOR_DIR / 'LICENSE').read_text().startswith('MIT License')


def test_vendored_agent_and_session_import():
    from browser_use import Agent, BrowserSession
    import inspect

    params = inspect.signature(Agent.__init__).parameters
    assert 'extend_system_message' in params
    assert 'register_should_stop_callback' in params
    assert BrowserSession is not None


def test_phoenix_router_model_satisfies_browser_use_llm_protocol(monkeypatch):
    from types import SimpleNamespace
    from app import browser_use_adapter
    from pydantic import BaseModel

    async def fake_chat(messages, provider, model):
        return '{"ok": true}', 'primary', model

    class Out(BaseModel):
        ok: bool

    monkeypatch.setattr(browser_use_adapter, 'chat', fake_chat)
    model = browser_use_adapter.PhoenixRouterChatModel(model='m1')
    assert model.provider == 'phoenix'
    assert model.model_name == 'm1' and model.model == 'm1'
    completion = asyncio.run(model.ainvoke([SimpleNamespace(role='user', content='hi')], Out))
    assert completion.completion.ok is True


def test_agent_md_is_injected_into_engine_system_message():
    from app import browser_use_adapter
    from app.agent.prompt_loader import AGENT_PATH

    injected = browser_use_adapter.phoenix_agent_instructions()
    assert '# AUTHORITATIVE AGENT.md' in injected
    assert AGENT_PATH.read_text(encoding='utf-8').strip().splitlines()[0] in injected


def test_engine_does_not_use_browser_use_cloud(monkeypatch):
    import os
    from app import browser_use_adapter  # noqa: F401
    assert os.environ.get('ANONYMIZED_TELEMETRY') == 'false'
    assert os.environ.get('BROWSER_USE_CLOUD_SYNC') == 'false'


def test_strip_code_fences_before_json_parse():
    from app import browser_use_adapter as adapter

    assert adapter._strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert adapter._strip_code_fences('```\n{"a": 1}\n```') == '{"a": 1}'
    assert adapter._strip_code_fences('{"a": 1}') == '{"a": 1}'


def test_action_retry_recovers_from_empty_then_fenced_output(monkeypatch):
    from types import SimpleNamespace
    from pydantic import BaseModel
    from app import browser_use_adapter

    class Action(BaseModel):
        name: str

    responses = iter(['', '```json\n{"name": "click"}\n```'])
    seen = []

    async def fake_chat(messages, provider, model):
        seen.append(messages)
        return next(responses), 'primary', model

    monkeypatch.setattr(browser_use_adapter, 'chat', fake_chat)
    model = browser_use_adapter.PhoenixRouterChatModel(provider='primary', model='tiny')
    completion = asyncio.run(model.ainvoke([SimpleNamespace(role='user', content='go')], Action))

    assert completion.completion == Action(name='click')
    assert len(seen) == 2
    assert seen[1][-1] == {'role': 'system', 'content': browser_use_adapter.JSON_ONLY_NUDGE}


def test_action_retry_gives_up_after_three_attempts_and_shortens_snapshot(monkeypatch):
    from types import SimpleNamespace
    from pydantic import BaseModel
    from app import browser_use_adapter

    class Action(BaseModel):
        name: str

    seen = []

    async def fake_chat(messages, provider, model):
        seen.append(messages)
        return 'not json at all', 'primary', model

    monkeypatch.setattr(browser_use_adapter, 'chat', fake_chat)
    model = browser_use_adapter.PhoenixRouterChatModel(provider='primary', model='tiny')
    big_page = 'x' * 20000
    try:
        asyncio.run(model.ainvoke([SimpleNamespace(role='user', content=big_page)], Action))
        raise AssertionError('expected parse failure')
    except ValueError:
        pass
    assert len(seen) == browser_use_adapter.MAX_ACTION_ATTEMPTS == 3
    assert len(seen[0][0]['content']) == 20000
    assert len(seen[1][0]['content']) < 6100
    assert len(seen[2][0]['content']) < 6100
