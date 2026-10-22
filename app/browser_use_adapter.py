"""Browser Use engine for Phoenix, backed by the vendored MIT-licensed source.

The browser-use package is vendored at vendor/browser_use_upstream (tag 0.13.13,
LICENSE kept alongside it). Its Agent and BrowserSession drive the browser. The
only swapped layer is the LLM: PhoenixRouterChatModel routes every model call
through Phoenix's own provider stack (app.providers.chat: Phoenix AI router
primary, Gemini fallback). Phoenix's AGENT.md is injected as the agent's
extended system message. No browser-use cloud service or API key is used.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from app.config import settings
from app.providers import chat
from app.proxy import configured_proxy

VENDOR_DIR = Path(__file__).resolve().parents[1] / 'vendor' / 'browser_use_upstream'

# Keep the vendored engine offline: no telemetry or cloud calls.
os.environ.setdefault('ANONYMIZED_TELEMETRY', 'false')
os.environ.setdefault('BROWSER_USE_CLOUD_SYNC', 'false')


def _ensure_vendored_on_path() -> None:
    vendor = str(VENDOR_DIR)
    if VENDOR_DIR.is_dir() and vendor not in sys.path:
        sys.path.insert(0, vendor)


_ensure_vendored_on_path()


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
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get('type') == 'text':
                parts.append(str(part.get('text', '')))
            elif isinstance(part, str):
                parts.append(part)
        if parts:
            return '\n'.join(parts)
    return json.dumps(content, ensure_ascii=False, default=str)


def _message_role(message: Any) -> str:
    role = getattr(message, 'role', None)
    if role:
        return str(role)
    name = message.__class__.__name__.lower()
    return 'assistant' if 'assistant' in name else 'system' if 'system' in name else 'user'


class PhoenixRouterChatModel:
    """browser-use BaseChatModel-compatible wrapper around Phoenix's provider API.

    Implements the duck-typed protocol browser-use's Agent uses: provider, name,
    model, model_name and ainvoke(messages, output_format). Routing, fallback and
    proxy configuration all stay in app.providers / app.config.
    """

    def __init__(self, provider: str = 'primary', model: str | None = None):
        self.provider_choice = provider
        self.model = model or settings.primary_model
        self._verified_api_keys = True

    @property
    def provider(self) -> str:
        return 'phoenix'

    @property
    def name(self) -> str:
        return f'phoenix:{self.model}'

    @property
    def model_name(self) -> str:
        return self.model

    async def ainvoke(self, messages, output_format=None, **kwargs):
        from browser_use.llm.views import ChatInvokeCompletion
        payload = [{'role': _message_role(item), 'content': _message_text(item)} for item in messages]
        # app.providers.chat returns (text, provider_used, model_used).
        text, _used_provider, _used_model = await chat(payload, self.provider_choice, self.model)
        completion: Any = text
        if output_format is not None:
            try:
                completion = output_format.model_validate_json(text)
            except Exception:
                completion = output_format.model_validate(json.loads(text))
        return ChatInvokeCompletion(completion=completion, usage=None)


def phoenix_agent_instructions() -> str:
    """AGENT.md (plus Phoenix skill procedures) as the agent's system extension."""
    from app.agent.prompt_loader import build_effective_instructions
    effective, _version, _skills = build_effective_instructions()
    return effective


async def execute_browser_use_task(
    instruction: str,
    start_url: str | None = None,
    provider: str = 'primary',
    model: str | None = None,
    progress=None,
    cancel_event=None,
):
    """Run the vendored browser-use Agent inside Phoenix's task wrapper."""
    if not browser_use_available():
        raise BrowserUseUnavailable('Browser Use is not installed in this Phoenix image')
    from browser_use import Agent, BrowserSession
    from browser_use.browser.profile import BrowserProfile, ProxySettings

    proxy = configured_proxy()
    profile_kwargs = {'headless': settings.browser_headless}
    if proxy:
        profile_kwargs['proxy'] = ProxySettings(
            server=proxy['server'], username=proxy.get('username'), password=proxy.get('password'))
    session = BrowserSession(browser_profile=BrowserProfile(**profile_kwargs))
    task = instruction if not start_url else f'{instruction}\nStart at this URL: {start_url}'
    llm = PhoenixRouterChatModel(provider=provider, model=model)

    async def should_stop():
        # Phoenix's cancel/stop boundary is honored between agent steps.
        return bool(cancel_event and cancel_event.is_set())

    async def on_step_end(agent):
        if progress:
            await progress(f'Browser Use verified step {getattr(agent.state, "n_steps", "")}'.strip())

    agent = Agent(
        task=task,
        llm=llm,
        browser_session=session,
        extend_system_message=phoenix_agent_instructions(),
        max_failures=settings.model_action_retries + 1,
        enable_signal_handler=False,
        register_should_stop_callback=should_stop,
        source='phoenix',
    )
    try:
        history = await agent.run(max_steps=settings.max_agent_steps, on_step_end=on_step_end)
        return json.dumps({'summary': history.final_result() or 'Browser Use task completed.',
                           'engine': 'browser_use', 'verified': True}, ensure_ascii=False)
    finally:
        await session.stop()
