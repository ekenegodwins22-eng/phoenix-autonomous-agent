import asyncio, logging, uuid
from collections import OrderedDict
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pathlib import Path
from pydantic import BaseModel, EmailStr
from app.config import settings
from app.models import TaskRequest, TaskStatus, now_utc
from app.runner import HumanInterventionRequired, execute_task
from app.auth import AuthPayload, current_user, login_user, register_user
from app.mailbox import mailbox
from app.providers import available_models
from app.browser import browser
from app.runtime import resource_status, browser_installation_status

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
app = FastAPI(title=settings.app_name, version="0.3.0", description="Low-memory sequential browser agent with selectable self-hosted and Gemini models.")
tasks = OrderedDict()
task_lock = asyncio.Lock()
execution_lock = asyncio.Lock()
task_resume_events = {}

class TaskResponse(TaskStatus):
    pass

def snapshot(item): return TaskStatus(**item)

@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(Path(__file__).parent / "static" / "index.html")

@app.get("/health")
async def health():
    installation, resources = await asyncio.gather(asyncio.to_thread(browser_installation_status), asyncio.to_thread(resource_status))
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
    task_id = uuid.uuid4().hex[:12]
    item = {"id":task_id,"owner_id":user['id'],"instruction":request.instruction,"start_url":request.start_url,"timeout_seconds":request.timeout_seconds,"status":"queued","progress":"Queued; waiting for the single execution slot.","human_help":None,"waiting_details":None,"created_at":now_utc(),"updated_at":now_utc(),"result":None,"error":None,"provider":request.provider,"model":request.model}
    task_resume_events[task_id] = asyncio.Event()
    async with task_lock:
        tasks[task_id] = item
        while len(tasks) > settings.max_task_history: tasks.popitem(last=False)
    asyncio.create_task(run_task(task_id, request))
    return snapshot(item)

async def run_task(task_id, request):
    async with execution_lock:
        item = tasks.get(task_id)
        if not item: return
        item['status']='running'; item['progress']='Starting task execution.'; item['updated_at']=now_utc()
        async def progress(message):
            item['progress'] = message
            item['updated_at'] = now_utc()
        async def on_waiting_human(details):
            item['status'] = 'waiting_human'
            item['human_help'] = 'Complete the challenge in the browser verification panel, then resume the task.'
            item['waiting_details'] = details
            item['updated_at'] = now_utc()
        try:
            resume_event = task_resume_events.get(task_id)
            item['result'] = await asyncio.wait_for(execute_task(request.instruction, request.start_url, request.provider, request.model, progress, on_waiting_human, resume_event, task_id), timeout=request.timeout_seconds or settings.task_timeout_seconds)
            item['progress']='Task completed with verified actions.'; item['status']='completed'; item['human_help']=None; item['waiting_details']=None
        except HumanInterventionRequired as exc:
            item['error']=str(exc)[:400]; item['human_help']='Complete the challenge manually, then call POST /tasks/{id}/resume.'; item['progress']='Paused for required human verification.'; item['status']='waiting_human'
        except Exception as exc:
            logging.exception('Task %s failed', task_id)
            item['error']=f"{type(exc).__name__}: {str(exc)[:400]}"; item['human_help']=None; item['waiting_details']=None; item['progress']='Task failed; see the error for details.'; item['status']='failed'
        finally:
            item['updated_at']=now_utc()
            task_resume_events.pop(task_id, None)

@app.post("/tasks/{task_id}/resume", response_model=TaskStatus, status_code=202)
async def resume_task(task_id: str, user=Depends(current_user)):
    item = tasks.get(task_id)
    if not item or item.get('owner_id') != user['id']:
        raise HTTPException(status_code=404, detail="Task not found or history expired")
    if item.get('status') != 'waiting_human':
        raise HTTPException(status_code=409, detail="Task is not waiting for human verification")
    event = task_resume_events.get(task_id)
    if event is None or browser.current_page is None or browser.current_task_id != task_id:
        raise HTTPException(status_code=409, detail="The active browser session is no longer available")
    item['status']='running'; item['progress']='Resuming after human verification.'; item['error']=None; item['human_help']=None; item['waiting_details']=None; item['updated_at']=now_utc()
    event.set()
    return snapshot(item)

class BrowserActionRequest(BaseModel):
    action: str
    target: str | None = None
    value: str | None = None
    key: str | None = None
    x: int | None = None
    y: int | None = None

def _waiting_browser(task_id, user):
    item = tasks.get(task_id)
    if not item or item.get('owner_id') != user['id']:
        raise HTTPException(status_code=404, detail="Task not found or history expired")
    if item.get('status') != 'waiting_human' or browser.current_page is None or browser.current_task_id != task_id:
        raise HTTPException(status_code=409, detail="Task is not waiting for browser verification")
    return item, browser.current_page

@app.get("/tasks/{task_id}/screenshot")
async def task_screenshot(task_id: str, user=Depends(current_user)):
    _, page = _waiting_browser(task_id, user)
    try:
        image = await page.screenshot(type='png')
        return Response(content=image, media_type='image/png', headers={"Cache-Control":"no-store"})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not capture browser screenshot: {type(exc).__name__}")

@app.post("/tasks/{task_id}/browser-action")
async def task_browser_action(task_id: str, payload: BrowserActionRequest, user=Depends(current_user)):
    item, page = _waiting_browser(task_id, user)
    try:
        action = payload.action.lower()
        if action == 'click':
            if payload.x is not None and payload.y is not None:
                await page.mouse.click(payload.x, payload.y)
            elif payload.target:
                from app.runner import _locate
                await (await _locate(page, payload.target)).click()
            else:
                raise HTTPException(status_code=422, detail="click requires target or x and y")
        elif action == 'fill':
            if not payload.target:
                raise HTTPException(status_code=422, detail="fill requires target")
            from app.runner import _locate
            await (await _locate(page, payload.target)).fill(payload.value or '')
        elif action == 'press':
            if not payload.target:
                raise HTTPException(status_code=422, detail="press requires target")
            from app.runner import _locate
            await (await _locate(page, payload.target)).press(payload.key or 'Enter')
        elif action == 'scroll':
            await page.mouse.wheel(payload.x or 0, payload.y or 500)
        elif action == 'inspect':
            from app.runner import _snapshot
            return await _snapshot(page)
        else:
            raise HTTPException(status_code=422, detail="Supported actions: click, fill, press, scroll, inspect")
        item['updated_at'] = now_utc()
        return {"ok":True,"action":action,"url":page.url}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Browser action failed: {type(exc).__name__}: {str(exc)[:180]}")

@app.get("/tasks", response_model=list[TaskStatus])
async def list_tasks(user=Depends(current_user)):
    return [snapshot(x) for x in reversed(list(tasks.values())) if x.get('owner_id') == user['id']]

@app.get("/tasks/{task_id}", response_model=TaskStatus)
async def get_task(task_id: str, user=Depends(current_user)):
    item = tasks.get(task_id)
    if not item or item.get('owner_id') != user['id']: raise HTTPException(status_code=404, detail="Task not found or history expired")
    return snapshot(item)
