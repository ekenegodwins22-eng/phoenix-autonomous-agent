import asyncio


def test_browser_use_adapter_is_optional_and_lazy():
    from app.browser_use_adapter import browser_use_available

    assert isinstance(browser_use_available(), bool)


def test_runner_dispatches_to_browser_use_only_when_configured(monkeypatch):
    from app import runner

    calls = []

    async def fake_execute(*args):
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


def test_phoenix_engine_remains_the_default():
    from app.config import settings

    assert settings.execution_engine == 'phoenix'


def test_phoenix_router_model_implements_browser_use_invoke_contract(monkeypatch):
    from types import SimpleNamespace
    from app import browser_use_adapter

    seen = []

    async def fake_chat(messages, provider, model):
        seen.append((messages, provider, model))
        return 'browser-use response'

    monkeypatch.setattr(browser_use_adapter, 'chat', fake_chat)
    model = browser_use_adapter.PhoenixRouterChatModel(provider='primary', model='test-model')
    completion = asyncio.run(model.ainvoke([SimpleNamespace(role='user', content='Inspect the page')]))

    assert completion.completion == 'browser-use response'
    assert seen == [([{'role': 'user', 'content': 'Inspect the page'}], 'primary', 'test-model')]
