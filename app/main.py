import asyncio, logging
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pathlib import Path
from pydantic import BaseModel, EmailStr
from app.config import settings
from app.models import TaskRequest, TaskStatus, TaskMessage, TaskMessageRequest, now_utc
from app.runner import HumanInterventionRequired, execute_task
from app.auth import AuthPayload, current_user, login_user, register_user
from app.mailbox import mailbox
from app.providers import available_models
from app.browser import browser
from app.runtime import resource_status, browser_installation_status
from app import task_store

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
app = FastAPI(title=settings.app_name, version="0.4.0", description="Durable sequential browser agent with persistent task sessions.")
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
    for task_id in task_store.recover_after_restart():
        asyncio.create_task(start_task(task_id))

@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(Path(__file__).parent / "static" / "index.html")

@app.get("/health")
async def health():
    installation, resources = await asyncio.gather(asyncio.to_thread(browser_installation_status, verify_launch=False), asyncio.to_thread(resource_status))
    return {"status":"ok","browser_policy":"one active browser session; retained during human verification and closed afterward","max_agent_steps":settings.max_agent_steps,"auth":"email-password; no email verification","model_selection":True,"captcha":"detect-and-pause-with-same-session-resume","browser":{"available":installation.get('available'),"error":installation.get('error')},"resources":{"disk":resources.get('disk'),"memory":resources.get('memory'),"browser_processes":resources.get('browser_processes'),"timestamp":resources.get('timestamp')}}

@app.get('/runtime/status')
async def runtime_status(user=Depends(current_user)):
    installation = await asyncio.to_thread(browser_installation_status)
    resources = await asyncio.to_thread(resource_status)
    return {"browser": installation, "resources": resources}

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
    item = task_store.create_task(owner_id=user['id'], instruction=request.instruction, start_url=request.start_url, timeout_seconds=request.timeout_seconds, provider=request.provider, model=request.model)
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


async def run_task(task_id: str):
    worker_token = f'worker-{id(asyncio.current_task())}'
    if not task_store.claim_task(task_id, worker_token):
        return
    item = task_store.get_task(task_id)
    if not item:
        return
    cancel_event = asyncio.Event()
    resume_event = asyncio.Event()
    task_cancel_events[task_id] = cancel_event
    task_resume_events[task_id] = resume_event
    async with execution_lock:
        try:
            task_store.set_state(task_id, 'running', progress='Starting task execution.')
            async def progress(message):
                if task_store.is_cancel_requested(task_id):
                    cancel_event.set()
                if cancel_event.is_set():
                    raise asyncio.CancelledError()
                task_store.set_state(task_id, 'running', progress=message)
            async def on_waiting_human(details):
                task_store.set_state(task_id, 'waiting_human', progress='Paused for required human verification.', human_help='Complete the challenge in the browser verification panel, then resume the task.', waiting_details=details)
            async def get_user_messages():
                return task_store.claim_unconsumed_user_messages(task_id)
            request = TaskRequest(instruction=item['instruction'], start_url=item.get('start_url'), timeout_seconds=item.get('timeout_seconds'), provider=item.get('provider') or 'auto', model=item.get('model'))
            result = await asyncio.wait_for(execute_task(request.instruction, request.start_url, request.provider, request.model, progress, on_waiting_human, resume_event, task_id, cancel_event, get_user_messages), timeout=request.timeout_seconds or settings.task_timeout_seconds)
            task_store.set_state(task_id, 'completed', progress='Task completed with verified actions.', result=result)
        except asyncio.CancelledError:
            task_store.set_state(task_id, 'cancelled', progress='Task cancelled server-side; browser resources are being released.')
        except HumanInterventionRequired as exc:
            task_store.set_state(task_id, 'waiting_human', progress='Paused for required human verification.', error=str(exc)[:400], human_help='Complete the challenge manually, then call POST /tasks/{id}/resume.', waiting_details=None)
        except Exception as exc:
            logging.exception('Task %s failed', task_id)
            task_store.set_state(task_id, 'failed', progress='Task failed; see the error for details.', error=f'{type(exc).__name__}: {str(exc)[:400]}')
        finally:
            task_store.release_worker(task_id)
            task_cancel_events.pop(task_id, None)
            task_resume_events.pop(task_id, None)


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
    message = task_store.add_message(item['id'], user['id'], 'user', content)
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
    retried = task_store.queue_retry(item['id'])
    if not retried:
        raise HTTPException(status_code=409, detail='Task is not eligible for a bounded retry')
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
    _, page = _waiting_browser(task_ref, user)
    try:
        image = await page.screenshot(type='png')
        return Response(content=image, media_type='image/png', headers={'Cache-Control':'no-store'})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'Could not capture browser screenshot: {type(exc).__name__}')


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


@app.get('/tasks', response_model=list[TaskStatus])
async def list_tasks(user=Depends(current_user)):
    return [snapshot(item) for item in task_store.list_tasks(user['id'], settings.max_task_history)]


@app.get('/tasks/{task_ref}', response_model=TaskStatus)
async def get_task(task_ref: str, user=Depends(current_user)):
    return snapshot(_task_for_user(task_ref, user))


@app.get('/tasks/{task_ref}/session', include_in_schema=False)
async def task_session(task_ref: str):
    return FileResponse(Path(__file__).parent / 'static' / 'task.html')
