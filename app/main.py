import json
import secrets
import asyncio, logging
from fastapi import Depends, FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pathlib import Path
from pydantic import BaseModel, EmailStr
from app.config import settings
from app.models import TaskRequest, TaskStatus, TaskMessage, TaskMessageRequest, now_utc
from app.agent.execution_loop import HumanInterventionRequired, TaskBlocked, TaskIncomplete, execute_task
from app.auth import AuthPayload, current_user, login_user, register_user
from app.mailbox import mailbox
from app.providers import available_models
from app.browser import browser
from app.runtime import resource_status, browser_installation_status
from app.agent.task_manager import task_manager as task_store
from app import run_options as run_opts
from app import settings_store
from app.models import OwnerSettings, ConfigDocument
from app.runner import DEEP_RESEARCH_STEP_MULTIPLIER
from app import checkpoint as checkpoint_format
from app.agent.prompt_loader import InstructionConfigError, instruction_status
from app.proxy import configured_proxy, public_status

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
app = FastAPI(title=settings.app_name, version="0.4.0", description="Durable sequential browser agent with persistent task sessions.")
app.add_middleware(GZipMiddleware, minimum_size=1024)
execution_lock = asyncio.Lock()
task_resume_events = {}
task_cancel_events = {}
task_handles = {}

class TaskResponse(TaskStatus):
    pass

def snapshot(item):
    item = dict(item)
    item['session_url'] = f"/tasks/{item['slug']}/session"
    item['logs'] = task_store.get_logs(item['id'], limit=200)
    return TaskStatus(**item)


@app.on_event('startup')
async def recover_tasks_on_startup():
    for task_id in task_store.recover_interrupted():
        asyncio.create_task(start_task(task_id))
    for task_id in task_store.recover_waiting_on_restart():
        asyncio.create_task(start_task(task_id))

@app.get("/", include_in_schema=False)
async def root():
    """Root serves the console UI. The old root page is parked at static/legacy/index-make-the-next-move.html."""
    return FileResponse(Path(__file__).parent / "static" / "console.html", headers={'Cache-Control': 'no-cache'})

@app.get("/console", include_in_schema=False)
async def console():
    """Dashboard UI (static, no build step). Calls the existing REST API with a bearer token."""
    return FileResponse(Path(__file__).parent / "static" / "console.html", headers={'Cache-Control': 'no-cache'})

@app.get("/health")
async def health():
    installation, resources = await asyncio.gather(asyncio.to_thread(browser_installation_status, verify_launch=False), asyncio.to_thread(resource_status))
    return {"status":"ok","database":"postgresql" if settings.database_url else "sqlite","agent_instructions":instruction_status(),"browser_policy":"one active browser session; retained during human verification and closed afterward","max_agent_steps":settings.max_agent_steps,"auth":"email-password; no email verification","model_selection":True,"captcha":"detect-and-pause-with-same-session-resume","browser":{"available":installation.get('available'),"error":installation.get('error')},"resources":{"disk":resources.get('disk'),"memory":resources.get('memory'),"browser_processes":resources.get('browser_processes'),"timestamp":resources.get('timestamp')}}

@app.get('/runtime/status')
async def runtime_status(user=Depends(current_user)):
    installation = await asyncio.to_thread(browser_installation_status)
    resources = await asyncio.to_thread(resource_status)
    return {"agent_instructions": instruction_status(), "browser": installation, "resources": resources, "proxy": public_status()}


@app.get('/proxy/status')
async def proxy_status(user=Depends(current_user)):
    """Return only non-secret proxy configuration metadata."""
    return public_status()

@app.post('/auth/register', status_code=201)
async def register(payload: AuthPayload):
    user = register_user(str(payload.email), payload.password)
    # No email verification by design; user can sign in immediately.
    return {"created":True,"email_verification_required":False,"user":user,"message":"Account created. Sign in to receive an access token."}

@app.post('/auth/login')
async def login(payload: AuthPayload):
    return login_user(str(payload.email), payload.password)

@app.get('/auth/me')
async def me(user=Depends(current_user)):
    return user

@app.get('/mailbox/health')
async def mailbox_health(user=Depends(current_user)):
    try: return await mailbox.health()
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Mailbox API unavailable: {str(exc)[:250]}")

@app.get('/mailbox/session')
async def mailbox_session(user=Depends(current_user)):
    try: return await mailbox.session()
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Mailbox API unavailable: {str(exc)[:250]}")

class SessionRestoreRequest(BaseModel):
    alias_email: EmailStr

@app.post('/mailbox/session/restore')
async def mailbox_session_restore(payload: SessionRestoreRequest, user=Depends(current_user)):
    try: return await mailbox.restore_session(payload.alias_email)
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Mailbox API unavailable: {str(exc)[:250]}")

@app.post('/mailbox/session/recovery')
async def mailbox_session_recovery(payload: dict, user=Depends(current_user)):
    try: return await mailbox.recover_session(payload)
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Mailbox API unavailable: {str(exc)[:250]}")

@app.get('/mailbox/accounts')
async def mailbox_accounts(user=Depends(current_user)):
    try: return await mailbox.list_accounts()
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Mailbox API unavailable: {str(exc)[:250]}")

@app.get('/mailbox/aliases')
async def mailbox_aliases(user=Depends(current_user)):
    try: return await mailbox.list_aliases()
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Mailbox API unavailable: {str(exc)[:250]}")

@app.get('/mailbox/messages')
async def mailbox_messages(user=Depends(current_user)):
    try: return await mailbox.list_messages()
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Mailbox API unavailable: {str(exc)[:250]}")

@app.get('/mailbox/messages/{message_id}')
async def mailbox_message(message_id: str, user=Depends(current_user)):
    try: return await mailbox.get_message(message_id)
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Mailbox API unavailable: {str(exc)[:250]}")

@app.get('/mailbox/messages/{message_id}/attachments/{attachment_id}')
async def mailbox_attachments(message_id: str, attachment_id: str, user=Depends(current_user)):
    try: return await mailbox.get_attachment(message_id, attachment_id)
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Mailbox API unavailable: {str(exc)[:250]}")

class AliasRequest(BaseModel):
    address_type: str = 'alias'
    variation_type: str = 'dots'
    variation_label: str | None = None
    custom_name: str | None = None
    gmail_account_id: int | None = None
    use_primary: bool = False

@app.post('/mailbox/aliases')
async def create_mailbox_alias(payload: AliasRequest, user=Depends(current_user)):
    if payload.address_type not in {'alias','variation'}: raise HTTPException(422, detail='address_type must be alias or variation')
    if payload.variation_type not in {'dots','googlemail','plus','combined','googlemail_dot','googlemail_alias','googlemail_combined'}: raise HTTPException(422, detail='Unsupported variation_type')
    try:
        return await mailbox.create_alias(address_type=payload.address_type, variation_type=payload.variation_type, variation_label=payload.variation_label, custom_name=payload.custom_name, gmail_account_id=payload.gmail_account_id, use_primary=payload.use_primary)
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Mailbox API unavailable: {str(exc)[:250]}")

@app.get('/mailbox/aliases/{alias_id}')
async def get_mailbox_alias(alias_id: str, user=Depends(current_user)):
    try: return await mailbox.get_alias(alias_id)
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Mailbox API unavailable: {str(exc)[:250]}")

@app.patch('/mailbox/aliases/{alias_id}')
async def update_mailbox_alias(alias_id: str, payload: dict, user=Depends(current_user)):
    try: return await mailbox.update_alias(alias_id, payload)
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Mailbox API unavailable: {str(exc)[:250]}")

@app.delete('/mailbox/aliases/{alias_id}')
async def delete_mailbox_alias(alias_id: str, user=Depends(current_user)):
    try: return await mailbox.delete_alias(alias_id)
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Mailbox API unavailable: {str(exc)[:250]}")

@app.get('/mailbox/aliases/{alias_id}/messages')
async def alias_messages(alias_id: str, user=Depends(current_user)):
    try: return await mailbox.list_alias_messages(alias_id)
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Mailbox API unavailable: {str(exc)[:250]}")


@app.get("/models")
async def models(user=Depends(current_user)):
    """Return selectable models for the authenticated workspace."""
    return await available_models()

@app.post("/tasks", response_model=TaskStatus, status_code=202)
async def create_task(request: TaskRequest, user=Depends(current_user)):
    proxy = configured_proxy() if request.proxy_mode == 'configured' else None
    if request.proxy_mode == 'configured' and not proxy:
        raise HTTPException(status_code=422, detail='Configured proxy mode requested but no protected proxy is configured')
    if request.proxy_mode == 'direct' and settings.browser_proxy_required:
        raise HTTPException(status_code=422, detail='A proxy is required by server configuration for this deployment')
    item = task_store.create_task(owner_id=user['id'], instruction=request.instruction, start_url=request.start_url, timeout_seconds=request.timeout_seconds, provider=request.provider, model=request.model, proxy_mode=request.proxy_mode, proxy_identifier=proxy.get('identifier') if proxy else None, effort=request.effort, fast_mode=request.fast_mode, deep_research=request.deep_research)
    asyncio.create_task(start_task(item['id']))
    return snapshot(item)


async def start_task(task_id: str):
    if task_id in task_handles and not task_handles[task_id].done():
        return
    handle = asyncio.create_task(run_task(task_id))
    task_handles[task_id] = handle
    try:
        await handle
    finally:
        task_handles.pop(task_id, None)


class _BudgetExpired(TimeoutError):
    """Raised only when the active execution budget is genuinely used up."""


class _ActiveClock:
    """Execution budget that excludes human-verification pauses.

    The budget is consumed only while the agent is executing. Waiting for a
    person does not count, so a slow human cannot time out the task.
    The global execution lock is released for the duration of a pause and
    re-acquired before execution continues.
    """

    def __init__(self, budget_seconds: float, lock: asyncio.Lock):
        self.remaining = float(budget_seconds)
        self.paused_since = None
        self.lock = lock
        self.held = False

    async def acquire(self):
        await self.lock.acquire()
        self.held = True

    def release(self):
        if self.held:
            self.held = False
            self.lock.release()

    async def pause(self):
        self.paused_since = asyncio.get_running_loop().time()
        self.release()

    async def resume(self):
        await self.acquire()
        self.paused_since = None


async def run_task(task_id: str):
    worker_token = f'{settings.phoenix_worker_id}:{id(asyncio.current_task())}'
    before = task_store.get_task(task_id)
    if not before or not task_store.claim_task(task_id, worker_token):
        return
    item = task_store.get_task(task_id)
    if not item:
        return
    was_recovering = before.get('status') == 'recovering'
    cancel_event = asyncio.Event()
    resume_event = asyncio.Event()
    task_cancel_events[task_id] = cancel_event
    task_resume_events[task_id] = resume_event
    default_budget = settings.task_timeout_seconds * (3 if item.get('deep_research') else 1)
    budget = float(item.get('timeout_seconds') or min(3600, default_budget))
    clock = _ActiveClock(budget, execution_lock)
    try:
        resume_from = None
        if was_recovering:
            resume_from = _resume_state_or_fail(task_id, item)
            if resume_from is None:
                return
        task_store.set_state(task_id, 'running', progress='Resuming from verified checkpoint.' if resume_from else 'Starting task execution.')
        async def progress(message):
            if task_store.is_cancel_requested(task_id):
                cancel_event.set()
            if cancel_event.is_set():
                raise asyncio.CancelledError()
            task_store.set_state(task_id, 'running', progress=message)
        async def on_waiting_human(details):
            challenge_type = str((details or {}).get('type') or 'human_verification')
            methods = ', '.join(str(x) for x in ((details or {}).get('resolution_methods') or ['human_completion']))
            task_store.set_state(task_id, 'waiting_human', progress=f'Paused: detected {challenge_type}.', human_help=f'Complete the detected {challenge_type} in the browser panel, then resume. Available methods: {methods}.', waiting_details=details)
        async def get_user_messages():
            return task_store.claim_unconsumed_user_messages(task_id)
        async def save_checkpoint(checkpoint):
            task_store.save_checkpoint(task_id, checkpoint)
        request = TaskRequest(instruction=item['instruction'], start_url=item.get('start_url'), timeout_seconds=item.get('timeout_seconds'), provider=item.get('provider') or 'auto', model=item.get('model'), proxy_mode=item.get('proxy_mode') or 'direct')
        owner_cfg = settings_store.get_settings(item['owner_id'])
        run_opts.run_options.set({
            'fast_mode': bool(item.get('fast_mode')),
            'deep_research': bool(item.get('deep_research')),
            'system_prompt': owner_cfg['agent'].get('system_prompt') or '',
            'max_steps': owner_cfg['agent'].get('max_steps'),
            'browser': dict(owner_cfg['browser']),
        })
        await clock.acquire()
        try:
            if task_store.is_cancel_requested(task_id):
                raise asyncio.CancelledError()
            # The global lock is held only while actively executing. It is released during a
            # human pause (clock.pause) so other tasks are not blocked by a waiting person.
            execution = asyncio.create_task(execute_task(request.instruction, request.start_url, request.provider, request.model, progress, on_waiting_human, resume_event, task_id, cancel_event, get_user_messages, save_checkpoint, request.proxy_mode, resume_from=resume_from, on_pause_started=clock.pause, on_pause_ended=clock.resume))
            result = await _run_with_active_budget(execution, clock)
        finally:
            clock.release()
        task_store.set_state(task_id, 'completed', progress='Task completed with verified actions.', result=result)
        if item.get('deep_research'):
            try:
                _write_research_report(item, result)
            except Exception:
                logging.exception('Research report for task %s could not be written', task_id)
    except asyncio.CancelledError:
        task_store.set_state(task_id, 'cancelled', progress='Task cancelled server-side; browser resources are being released.')
    except HumanInterventionRequired as exc:
        task_store.set_state(task_id, 'waiting_human', progress='Paused for required human verification.', error=str(exc)[:400], human_help='Complete the challenge manually, then call POST /tasks/{id}/resume.', waiting_details=None)
    except _BudgetExpired:
        task_store.set_state(task_id, 'failed', progress='Task did not reach verified completion.', error=f'Execution time budget of {int(budget)}s was used; human pause time is not counted.')
    except TimeoutError as exc:
        # Inner timeouts (browser/CDP/step) are NOT budget expiry; label them as what they are.
        logging.exception('Task %s hit an inner timeout', task_id)
        task_store.set_state(task_id, 'failed', progress='Task failed on a browser or network timeout.', error=f'TimeoutError: {str(exc)[:300] or "operation timed out"}')
    except (TaskBlocked, TaskIncomplete) as exc:
        task_store.set_state(task_id, 'failed', progress='Task did not reach verified completion.', error=str(exc)[:400])
    except InstructionConfigError as exc:
        task_store.set_state(task_id, 'failed', progress='Agent instruction configuration could not be loaded.', error=str(exc)[:400])
    except Exception as exc:
        logging.exception('Task %s failed', task_id)
        task_store.set_state(task_id, 'failed', progress='Task failed; see the error for details.', error=f'{type(exc).__name__}: {str(exc)[:400]}')
    finally:
        task_store.release_worker(task_id)
        task_cancel_events.pop(task_id, None)
        task_resume_events.pop(task_id, None)


def _write_research_report(item: dict, result: str) -> None:
    """Write a structured report (JSON + markdown) for a deep-research task into the Files store."""
    import json as _json
    from datetime import datetime as _dt, timezone as _tz
    try:
        parsed = _json.loads(result) if isinstance(result, str) else {}
    except ValueError:
        parsed = {}
    summary = (parsed.get('summary') if isinstance(parsed, dict) else None) or str(result or '')
    actions = (parsed.get('actions') if isinstance(parsed, dict) else None) or []
    visited = []
    for action in actions:
        url = action.get('url') if isinstance(action, dict) else None
        if url and url not in visited:
            visited.append(url)
    created = _dt.now(_tz.utc).isoformat()
    report = {
        'type': 'deep_research_report', 'version': 1, 'task_id': item['id'], 'slug': item['slug'],
        'question': item['instruction'], 'generated_at': created,
        'findings': summary, 'sources_visited': visited,
        'steps': [{'index': i + 1, 'action': a.get('action') or a.get('tool'), 'target': a.get('target') or a.get('url'), 'error': a.get('error')} for i, a in enumerate(actions) if isinstance(a, dict)],
        'providers_used': parsed.get('providers_used', []) if isinstance(parsed, dict) else [],
        'models_used': parsed.get('models_used', []) if isinstance(parsed, dict) else [],
    }
    md = ['# Deep research report', '', f"Question: {item['instruction']}", f"Generated: {created}", '', '## Findings', str(summary), '', '## Sources visited']
    md += [f'- {u}' for u in visited] or ['- none recorded']
    md += ['', '## Steps'] + [f"{s['index']}. {s['action']} {s['target'] or ''}".rstrip() + (f" (error: {s['error']})" if s['error'] else '') for s in report['steps']]
    base = Path(settings.database_path).resolve().parent / 'uploads' / item['id']
    base.mkdir(parents=True, exist_ok=True)
    for name, content, ctype in (
        ('deep-research-report.json', _json.dumps(report, ensure_ascii=False, indent=2), 'application/json'),
        ('deep-research-report.md', '\n'.join(md) + '\n', 'text/markdown'),
    ):
        path = base / name
        path.write_text(content, encoding='utf-8')
        task_store.add_file(item['id'], item['owner_id'], name, ctype, path.stat().st_size, str(path))


async def _run_with_active_budget(execution, clock: _ActiveClock):
    """Run execution; cancel it once ACTIVE (non-paused) time exceeds the budget.

    Only time spent executing counts. Time spent paused for a human is excluded
    from the budget, so a slow human cannot time the task out.
    """
    loop = asyncio.get_running_loop()
    active = 0.0
    last = loop.time()
    while True:
        if execution.done():
            return execution.result()
        now = loop.time()
        if clock.paused_since is None:
            active += now - last
        last = now
        if active >= clock.remaining:
            execution.cancel()
            try:
                await execution
            except (asyncio.CancelledError, Exception):
                pass
            raise _BudgetExpired()
        if clock.paused_since is not None:
            await asyncio.sleep(0.05)
        else:
            await asyncio.wait({execution}, timeout=min(0.1, max(0.0, clock.remaining - active)))


def _resume_state_or_fail(task_id: str, item: dict):
    """Return the verified resume state, or mark the task failed/recoverable with a clear reason."""
    status, state = checkpoint_format.decode(item.get('checkpoint'))
    if status != 'ok':
        task_store.set_state(task_id, 'failed', progress='Recovery blocked: no usable checkpoint.', error=f'Checkpoint {status}; execution was not replayed to avoid duplicate side effects.')
        return None
    allowed, reason = checkpoint_format.resume_decision(state)
    if not allowed:
        task_store.set_state(task_id, 'failed', progress='Recovery blocked: manual review required.', error=f'Not resumed: {reason}.')
        return None
    return state


def _task_for_user(task_ref: str, user):
    item = task_store.get_task(task_ref, owner_id=user['id'])
    if not item:
        raise HTTPException(status_code=404, detail='Task not found')
    return item


@app.get('/tasks/{task_ref}/messages', response_model=list[TaskMessage])
async def task_messages(task_ref: str, user=Depends(current_user)):
    item = _task_for_user(task_ref, user)
    return task_store.get_messages(item['id'], owner_id=user['id'], limit=200)


@app.post('/tasks/{task_ref}/messages', response_model=TaskMessage, status_code=202)
async def send_task_message(task_ref: str, payload: TaskMessageRequest, user=Depends(current_user)):
    item = _task_for_user(task_ref, user)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail='Message cannot be empty')
    try:
        message = task_store.add_message(item['id'], user['id'], 'user', content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        logging.exception('Could not store follow-up message for task %s', item['id'])
        raise HTTPException(status_code=503, detail='Could not save the message right now. Please try again.')
    normalized = content.lower().strip(' .!')
    if normalized in {'stop', 'cancel', 'abort', 'terminate', 'stop task', 'cancel task'}:
        if item['status'] not in {'completed', 'failed', 'cancelled'}:
            task_store.request_cancel(item['id'])
            event = task_cancel_events.get(item['id'])
            if event: event.set()
            handle = task_handles.get(item['id'])
            if handle and not handle.done(): handle.cancel()
    elif normalized in {'resume', 'continue', 'continue task'} and item['status'] == 'waiting_human':
        event = task_resume_events.get(item['id'])
        if event: event.set()
    elif item['status'] in {'completed', 'failed', 'cancelled'}:
        # Failed/cancelled tasks are reopened by the follow-up; the message is already saved above.
        reopened = task_store.requeue_for_message(item['id'])
        if reopened:
            asyncio.create_task(start_task(item['id']))
    return message


@app.get('/tasks/{task_ref}/logs')
async def task_logs(task_ref: str, user=Depends(current_user)):
    item = _task_for_user(task_ref, user)
    return {'task_id': item['id'], 'slug': item['slug'], 'logs': task_store.get_logs(item['id'], owner_id=user['id'], limit=500)}


@app.post('/tasks/{task_ref}/cancel', response_model=TaskStatus, status_code=202)
async def cancel_task(task_ref: str, user=Depends(current_user)):
    item = _task_for_user(task_ref, user)
    if item['status'] in {'completed','failed','cancelled'}:
        raise HTTPException(status_code=409, detail='Task is already terminal')
    if not task_store.request_cancel(item['id']):
        raise HTTPException(status_code=409, detail='Task cannot be cancelled in its current state')
    event = task_cancel_events.get(item['id'])
    if event: event.set()
    handle = task_handles.get(item['id'])
    if handle and not handle.done(): handle.cancel()
    return snapshot(task_store.get_task(item['id'], owner_id=user['id']))


@app.post('/tasks/{task_ref}/retry', response_model=TaskStatus, status_code=202)
async def retry_task(task_ref: str, user=Depends(current_user)):
    item = _task_for_user(task_ref, user)
    if item.get('status') not in {'failed', 'cancelled'}:
        raise HTTPException(status_code=409, detail='Only failed or cancelled tasks can be retried. This task is still ' + str(item.get('status')) + '.')
    retried = task_store.queue_retry(item['id'])
    if not retried:
        raise HTTPException(status_code=409, detail='Retry limit reached for this task; send a follow-up message to continue it instead.')
    asyncio.create_task(start_task(item['id']))
    return snapshot(retried)


@app.post("/tasks/{task_ref}/resume", response_model=TaskStatus, status_code=202)
async def resume_task(task_ref: str, user=Depends(current_user)):
    item = _task_for_user(task_ref, user)
    if item.get('status') != 'waiting_human':
        raise HTTPException(status_code=409, detail='Task is not waiting for human verification')
    event = task_resume_events.get(item['id'])
    if event is None or browser.current_page is None or browser.current_task_id != item['id']:
        raise HTTPException(status_code=409, detail='The active browser session is no longer available')
    task_store.set_state(item['id'], 'running', progress='Resuming after human verification.')
    event.set()
    return snapshot(task_store.get_task(item['id'], owner_id=user['id']))


class BrowserActionRequest(BaseModel):
    action: str
    target: str | None = None
    value: str | None = None
    key: str | None = None
    x: int | None = None
    y: int | None = None


def _waiting_browser(task_ref, user):
    item = _task_for_user(task_ref, user)
    if item.get('status') != 'waiting_human' or browser.current_page is None or browser.current_task_id != item['id']:
        raise HTTPException(status_code=409, detail='Task is not waiting for browser verification')
    return item, browser.current_page


@app.get('/tasks/{task_ref}/screenshot')
async def task_screenshot(task_ref: str, user=Depends(current_user)):
    from app.browser_use_adapter import latest_frame
    item = _task_for_user(task_ref, user)
    frame = latest_frame(item['id'])
    if frame is not None:
        return Response(content=frame, media_type='image/png', headers={'Cache-Control':'no-store'})
    _, page = _waiting_browser(task_ref, user)
    try:
        image = await page.screenshot(type='png')
        return Response(content=image, media_type='image/png', headers={'Cache-Control':'no-store'})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'Could not capture browser screenshot: {type(exc).__name__}')


@app.get('/tasks/{task_ref}/browser-stream')
async def task_browser_stream(task_ref: str, user=Depends(current_user)):
    item, page = _waiting_browser(task_ref, user)
    boundary = b'phoenixframe'

    async def frames():
        while browser.current_page is page and browser.current_task_id == item['id']:
            current = task_store.get_task(item['id'])
            if not current or current.get('status') != 'waiting_human':
                break
            try:
                image = await page.screenshot(type='jpeg', quality=settings.browser_stream_jpeg_quality)
            except Exception:
                break
            yield b'--' + boundary + b'\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(image)).encode() + b'\r\n\r\n' + image + b'\r\n'
            await asyncio.sleep(max(0.25, settings.browser_stream_interval_seconds))
        yield b'--' + boundary + b'--\r\n'

    return StreamingResponse(frames(), media_type='multipart/x-mixed-replace; boundary=phoenixframe', headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@app.post('/tasks/{task_ref}/browser-action')
async def task_browser_action(task_ref: str, payload: BrowserActionRequest, user=Depends(current_user)):
    item, page = _waiting_browser(task_ref, user)
    try:
        action = payload.action.lower()
        if action == 'click':
            if payload.x is not None and payload.y is not None: await page.mouse.click(payload.x, payload.y)
            elif payload.target:
                from app.runner import _locate
                await (await _locate(page, payload.target)).click()
            else: raise HTTPException(status_code=422, detail='click requires target or x and y')
        elif action == 'fill':
            if not payload.target: raise HTTPException(status_code=422, detail='fill requires target')
            from app.runner import _locate
            await (await _locate(page, payload.target)).fill(payload.value or '')
        elif action == 'press':
            if not payload.target: raise HTTPException(status_code=422, detail='press requires target')
            from app.runner import _locate
            await (await _locate(page, payload.target)).press(payload.key or 'Enter')
        elif action == 'scroll': await page.mouse.wheel(payload.x or 0, payload.y or 500)
        elif action == 'inspect':
            from app.runner import _snapshot
            return await _snapshot(page)
        else: raise HTTPException(status_code=422, detail='Supported actions: click, fill, press, scroll, inspect')
        task_store.add_log(item['id'], f'Authorized browser action completed: {action}')
        return {'ok':True,'action':action,'url':page.url}
    except HTTPException: raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f'Browser action failed: {type(exc).__name__}: {str(exc)[:180]}')


UPLOAD_LIMIT_BYTES = 10 * 1024 * 1024
UPLOAD_ROOT = Path(settings.database_path).resolve().parent / 'uploads'


@app.post('/tasks/{task_ref}/uploads', status_code=201)
async def upload_task_file(task_ref: str, file: UploadFile = File(...), user=Depends(current_user)):
    item = _task_for_user(task_ref, user)
    data = await file.read(UPLOAD_LIMIT_BYTES + 1)
    if len(data) > UPLOAD_LIMIT_BYTES:
        raise HTTPException(status_code=413, detail='File is larger than 10 MB')
    if not data:
        raise HTTPException(status_code=422, detail='File is empty')
    safe_name = Path(file.filename or 'upload.bin').name[:200] or 'upload.bin'
    target_dir = UPLOAD_ROOT / item['id']
    target_dir.mkdir(parents=True, exist_ok=True)
    stored = target_dir / f"{secrets.token_hex(6)}-{safe_name}"
    stored.write_bytes(data)
    record = task_store.add_file(item['id'], user['id'], safe_name, file.content_type, len(data), str(stored))
    return {k: v for k, v in record.items() if k != 'path'}


@app.get('/tasks/{task_ref}/artifacts')
async def task_artifacts(task_ref: str, user=Depends(current_user)):
    item = _task_for_user(task_ref, user)
    files = task_store.list_files(item['id'], owner_id=user['id'])
    uploads = [{'id': f['id'], 'kind': 'upload', 'name': f['name'], 'content_type': f['content_type'], 'size': f['size'], 'created_at': f['created_at'], 'url': f"/tasks/{item['slug']}/artifacts/{f['id']}"} for f in files]
    shot = None
    if item.get('status') == 'waiting_human' and browser.current_page is not None and browser.current_task_id == item['id']:
        shot = {'kind': 'live_screenshot', 'name': 'current browser view', 'url': f"/tasks/{item['slug']}/screenshot"}
    return {'task_id': item['id'], 'slug': item['slug'], 'artifacts': uploads + ([shot] if shot else [])}


@app.get('/tasks/{task_ref}/artifacts/{file_id}')
async def task_artifact_download(task_ref: str, file_id: int, user=Depends(current_user)):
    item = _task_for_user(task_ref, user)
    record = task_store.get_file(item['id'], file_id, user['id'])
    if not record or not Path(record['path']).is_file():
        raise HTTPException(status_code=404, detail='File not found')
    return FileResponse(record['path'], media_type=record['content_type'] or 'application/octet-stream', filename=record['name'])


@app.get('/tasks/{task_ref}/summary')
async def task_summary(task_ref: str, user=Depends(current_user)):
    item = _task_for_user(task_ref, user)
    lines = [f"Phoenix task: {item['instruction']}", f"Status: {item.get('status')}"]
    if item.get('model'): lines.append(f"Model: {item['model']}")
    if item.get('effort'): lines.append(f"Effort: {item['effort']}")
    if item.get('result'): lines.append('Result:\n' + str(item['result'])[:4000])
    if item.get('error'): lines.append('Error: ' + str(item['error'])[:1000])
    return {'task_id': item['id'], 'slug': item['slug'], 'summary': '\n'.join(lines), 'share_path': f"/tasks/{item['slug']}"}


@app.get('/usage')
async def usage(user=Depends(current_user)):
    """Phoenix-native usage stats (no monetary balance exists in this server)."""
    stats = task_store.usage_stats(user['id'])
    return {'unit': 'tasks', 'stats': stats, 'balance': None, 'note': 'Phoenix tracks task and runtime usage; no monetary balance is kept by this server.'}


@app.get('/settings')
async def get_owner_settings(user=Depends(current_user)):
    return settings_store.get_settings(user['id'])


@app.put('/settings')
async def put_owner_settings(body: OwnerSettings, user=Depends(current_user)):
    return settings_store.save_settings(user['id'], body.model_dump())


@app.get('/config/export')
async def export_config(user=Depends(current_user)):
    """Export the owner's settings as a portable Phoenix config document (JSON)."""
    cfg = settings_store.get_settings(user['id'])
    doc = {'format': 'phoenix-config', 'version': 1, 'settings': {'agent': cfg['agent'], 'browser': cfg['browser']}}
    return Response(content=json.dumps(doc, indent=2, ensure_ascii=False), media_type='application/json', headers={'Content-Disposition': 'attachment; filename="phoenix-config.json"'})


@app.post('/config/import')
async def import_config(body: ConfigDocument, user=Depends(current_user)):
    """Apply a Phoenix config document. Settings are saved; an included task draft is returned for the console to load."""
    saved = settings_store.save_settings(user['id'], body.settings.model_dump())
    return {'saved': True, 'settings': saved, 'task': body.task.model_dump() if body.task else None}


@app.get('/tasks', response_model=list[TaskStatus])
async def list_tasks(user=Depends(current_user)):
    return [snapshot(item) for item in task_store.list_tasks(user['id'], settings.max_task_history)]


@app.get('/tasks/{task_ref}', response_model=TaskStatus)
async def get_task(task_ref: str, user=Depends(current_user)):
    return snapshot(_task_for_user(task_ref, user))


@app.get('/tasks/{task_ref}/session', include_in_schema=False)
async def task_session(task_ref: str):
    return FileResponse(Path(__file__).parent / 'static' / 'task.html', headers={'Cache-Control': 'private, no-store, max-age=0, must-revalidate', 'Pragma': 'no-cache'})
