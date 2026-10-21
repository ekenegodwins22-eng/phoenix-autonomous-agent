"""Optional Browser Use Python-agent integration for Phoenix.

Phoenix's existing runner remains the default because it owns mailbox actions,
human-verification handoff, durable checkpoints, and task chat. This adapter
lets an explicitly configured task use the MIT-licensed Browser Use Agent and
BrowserSession while keeping Phoenix's provider and cancellation boundary.
"""
from __future__ import annotations
import json
from typing import Any
from app.config import settings
from app.providers import chat
from app.proxy import configured_proxy


class BrowserUseUnavailable(RuntimeError):
    pass


def browser_use_available() -> bool:
    try:
        import browser_use  # noqa: F401
        return True
    except ImportError:
        return False


def _message_text(message: Any) -> str:
    content = getattr(message, 'content', message)
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def _message_role(message: Any) -> str:
    role = getattr(message, 'role', None)
    if role:
        return str(role)
    name = message.__class__.__name__.lower()
    return 'assistant' if 'assistant' in name else 'system' if 'system' in name else 'user'


class PhoenixRouterChatModel:
    """Browser Use BaseChatModel-compatible wrapper around Phoenix's provider API."""

    def __init__(self, provider: str = 'primary', model: str | None = None):
        self.provider = provider
        self.model = model or settings.primary_model
        self.name = f'phoenix:{self.model}'

    @property
    def model_name(self) -> str:
        return self.model

    async def ainvoke(self, messages, output_format=None, **kwargs):
        from browser_use.llm.views import ChatInvokeCompletion
        payload = [{'role': _message_role(item), 'content': _message_text(item)} for item in messages]
        text = await chat(payload, self.provider, self.model)
        completion: Any = text
        if output_format is not None:
            try:
                completion = output_format.model_validate_json(text)
            except Exception:
                completion = output_format.model_validate(json.loads(text))
        return ChatInvokeCompletion(completion=completion, usage=None)


async def execute_browser_use_task(
    instruction: str,
    start_url: str | None = None,
    provider: str = 'primary',
    model: str | None = None,
    progress=None,
    cancel_event=None,
):
    """Run Browser Use Agent with Phoenix's configured model and cancellation boundary."""
    if not browser_use_available():
        raise BrowserUseUnavailable('Browser Use is not installed in this Phoenix image')
    from browser_use import Agent, BrowserSession
    from browser_use.browser.profile import BrowserProfile, ProxySettings

    proxy = configured_proxy()
    profile_kwargs = {'headless': settings.browser_headless}
    if proxy:
        profile_kwargs['proxy'] = ProxySettings(server=proxy['server'], username=proxy.get('username'), password=proxy.get('password'))
    session = BrowserSession(browser_profile=BrowserProfile(**profile_kwargs))
    task = instruction if not start_url else f'{instruction}\nStart at this URL: {start_url}'
    llm = PhoenixRouterChatModel(provider=provider, model=model)

    async def should_stop():
        return bool(cancel_event and cancel_event.is_set())

    async def on_step_end(agent):
        if progress:
            await progress(f'Browser Use verified step {getattr(agent.state, "n_steps", "")}'.strip())

    agent = Agent(
        task=task,
        llm=llm,
        browser_session=session,
        max_failures=settings.model_action_retries + 1,
        enable_signal_handler=False,
        task_id=None,
        register_should_stop_callback=should_stop,
        source='phoenix',
    )
    try:
        history = await agent.run(max_steps=settings.max_agent_steps, on_step_end=on_step_end)
        return json.dumps({'summary': history.final_result() or 'Browser Use task completed.', 'engine': 'browser_use', 'verified': True}, ensure_ascii=False)
    finally:
        await session.stop()
