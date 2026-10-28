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


# Latest PNG captured from the vendored engine's live browser session, keyed by task id.
# Served by GET /tasks/{ref}/screenshot while the agent is browsing.
_latest_frames: dict[str, bytes] = {}


def latest_frame(task_id: str) -> bytes | None:
    return _latest_frames.get(task_id)


def _browser_profile_kwargs(proxy_mode: str = 'direct', browser_target: str | None = None) -> dict:
    """Map owner browser settings (console Browser tab) onto browser-use's BrowserProfile."""
    from app import run_options as run_opts
    bopts = run_opts.current().get('browser') or {}
    headless = bopts.get('headless') if bopts.get('headless') is not None else settings.browser_headless
    kwargs: dict = {'headless': bool(headless)}
    # Render/Docker runs as a non-root user without a usable Chrome sandbox: Chrome exits before CDP.
    kwargs['chromium_sandbox'] = False
    kwargs['args'] = ['--no-sandbox', '--no-zygote', '--disable-setuid-sandbox', '--disable-gpu-sandbox', '--disable-dev-shm-usage']
    if bopts.get('viewport_width') and bopts.get('viewport_height'):
        kwargs['viewport'] = {'width': int(bopts['viewport_width']), 'height': int(bopts['viewport_height'])}
    if bopts.get('user_agent'):
        kwargs['user_agent'] = str(bopts['user_agent'])
    proxy = configured_proxy() if proxy_mode == 'configured' else None
    if proxy:
        from browser_use.browser.profile import ProxySettings
        kwargs['proxy'] = ProxySettings(server=proxy['server'], username=proxy.get('username'), password=proxy.get('password'))
    # Remote browserless Chrome: attach over CDP instead of launching a local chromium.
    # Unset BROWSER_WS keeps the existing local-launch behavior unchanged.
    import os
    browser_ws = (os.environ.get('BROWSER_WS') or '').strip()
    # Per-task target from the console: 'local' always launches locally, 'remote' attaches to BROWSER_WS.
    # A missing/unknown target keeps the env-based behavior above.
    if browser_target == 'local':
        kwargs.pop('cdp_url', None)
    elif browser_ws:
        kwargs['cdp_url'] = browser_ws
    return kwargs


_REDACT_KEYS = ('text', 'value')

# Local CDP attach: the /json/version fetch already bypasses proxies for localhost, but the
# websocket connect (websockets lib) honours HTTP(S)_PROXY and can hang on 127.0.0.1.
# Keep loopback traffic off any configured proxy so the CDP attach reaches Chrome directly.
def _ensure_loopback_no_proxy() -> None:
    import os
    current = [x.strip() for x in (os.environ.get('NO_PROXY') or os.environ.get('no_proxy') or '').split(',') if x.strip()]
    for host in ('127.0.0.1', 'localhost', '::1'):
        if host not in current:
            current.append(host)
    os.environ['NO_PROXY'] = os.environ['no_proxy'] = ','.join(current)


_ensure_loopback_no_proxy()


def _step_actions(item) -> list[tuple[str, dict]]:
    """Return (action_name, params) pairs the model chose in one history item."""
    out: list[tuple[str, dict]] = []
    model_output = getattr(item, 'model_output', None)
    for act in (getattr(model_output, 'action', None) or []):
        try:
            data = act.model_dump(exclude_none=True, mode='json')
        except Exception:
            continue
        if not data:
            continue
        name = next(iter(data))
        params = data.get(name)
        out.append((str(name), params if isinstance(params, dict) else ({} if params is None else {'value': params})))
    return out


def _redact_params(name: str, params: dict) -> dict:
    """Never keep typed form values in the stored/displayed history."""
    lowered = name.lower()
    if any(word in lowered for word in ('input', 'type', 'fill')):
        return {k: ('[redacted]' if k in _REDACT_KEYS else v) for k, v in params.items()}
    return dict(params)


def _last_error(item) -> str | None:
    for result in (getattr(item, 'result', None) or []):
        error = getattr(result, 'error', None)
        if error:
            return str(error).strip()[:400]
    return None


def summarize_history(history) -> dict:
    """Turn browser-use's AgentHistoryList into Phoenix's action log, done flag and last error."""
    items = list(getattr(history, 'history', None) or [])
    actions: list[dict] = []
    for index, item in enumerate(items, start=1):
        for name, params in _step_actions(item):
            target = params.get('index', params.get('url'))
            actions.append({'step': index, 'action': name, 'target': None if target is None else str(target)[:300],
                            'params': _redact_params(name, params)})
    last_error = None
    for item in items:
        error = _last_error(item)
        if error:
            last_error = error
    final = None
    try:
        final = history.final_result()
    except Exception:
        final = None
    done = False
    try:
        done = bool(history.is_done())
    except Exception:
        done = False
    return {'actions': actions, 'done': done, 'final': final, 'last_error': last_error, 'steps': len(items)}


def _describe_step(agent) -> str:
    """One console log line per step: the chosen actions, or the real error that step produced."""
    n = getattr(getattr(agent, 'state', None), 'n_steps', '')
    items = list(getattr(getattr(agent, 'history', None), 'history', None) or [])
    if not items:
        return f'Step {n}: no action recorded'
    item = items[-1]
    names = [name for name, _ in _step_actions(item)]
    error = _last_error(item)
    line = f"Step {n}: {', '.join(names) if names else 'no action chosen'}"
    if error:
        line += f' failed: {error[:200]}'
    return line


def _save_downloads(task_id: str | None, paths: list[str]) -> list[str]:
    """Copy files the browser downloaded into the task's Files store so the console can offer them."""
    if not task_id or not paths:
        return []
    import shutil
    from app import task_store
    from app.config import settings as cfg
    item = task_store.get_task(task_id)
    if not item:
        return []
    base = Path(cfg.database_path).resolve().parent / 'uploads' / item['id']
    base.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for raw in paths:
        src = Path(raw)
        if not src.is_file():
            continue
        dest = base / f'download-{src.name}'
        shutil.copyfile(src, dest)
        task_store.add_file(task_id, item['owner_id'], dest.name, None, dest.stat().st_size, str(dest))
        saved.append(dest.name)
    return saved


async def execute_browser_use_task(
    instruction: str,
    start_url: str | None = None,
    provider: str = 'primary',
    model: str | None = None,
    progress=None,
    cancel_event=None,
    proxy_mode: str = 'direct',
    task_id: str | None = None,
    browser_target: str | None = None,
):
    """Run the vendored browser-use Agent inside Phoenix's task wrapper."""
    if not browser_use_available():
        raise BrowserUseUnavailable('Browser Use is not installed in this Phoenix image')
    from browser_use import Agent, BrowserSession
    from browser_use.browser.profile import BrowserProfile
    from app import run_options as run_opts

    opts = run_opts.current()
    session = BrowserSession(browser_profile=BrowserProfile(**_browser_profile_kwargs(proxy_mode, browser_target)))
    task = instruction if not start_url else f'{instruction}\nStart at this URL: {start_url}'
    llm = PhoenixRouterChatModel(provider=provider, model=model)
    system_extension = phoenix_agent_instructions()
    owner_prompt = (opts.get('system_prompt') or '').strip()
    if owner_prompt:
        system_extension = f"{system_extension}\n\nOwner instructions for this agent:\n{owner_prompt}"
    max_steps = int(opts.get('max_steps') or settings.max_agent_steps)

    async def should_stop():
        # Phoenix's cancel/stop boundary is honored between agent steps.
        return bool(cancel_event and cancel_event.is_set())

    async def capture(agent):
        if not task_id:
            return
        try:
            frame = await session.take_screenshot(format='png')
            if frame:
                _latest_frames[task_id] = bytes(frame)
        except Exception:
            # No live page yet (or the session is closing); keep the previous frame.
            pass

    async def on_step_start(agent):
        await capture(agent)

    async def on_step_end(agent):
        await capture(agent)
        if progress:
            await progress(_describe_step(agent))

    agent = Agent(
        task=task,
        llm=llm,
        browser_session=session,
        extend_system_message=system_extension,
        max_failures=settings.model_action_retries + 1,
        enable_signal_handler=False,
        register_should_stop_callback=should_stop,
        source='phoenix',
    )
    try:
        history = await agent.run(max_steps=max_steps, on_step_start=on_step_start, on_step_end=on_step_end)
        summary = summarize_history(history)
        downloads = []
        try:
            downloads = _save_downloads(task_id, [str(p) for p in session.downloaded_files])
        except Exception:
            downloads = []
        if not summary['done']:
            # The agent stopped (step budget, repeated failures) without signalling done. Report that
            # honestly instead of a success with a placeholder summary.
            from app.runner import TaskIncomplete
            reason = summary['last_error'] or f'the agent stopped after {summary["steps"]} steps without finishing'
            raise TaskIncomplete(f'Browser Use did not reach a finished result: {reason}')
        return json.dumps({'summary': summary['final'] or 'Browser Use task completed.',
                           'engine': 'browser_use', 'verified': False,
                           'actions': summary['actions'], 'steps': summary['steps'],
                           'downloads': downloads}, ensure_ascii=False)
    finally:
        _latest_frames.pop(task_id, None) if task_id else None
        await session.stop()
