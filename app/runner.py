import asyncio, json, re
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse
from app.browser import browser
from app.providers import chat
from app.mailbox import mailbox
from app.config import settings

class HumanInterventionRequired(RuntimeError):
    """Raised when a page requires a human challenge or access-control decision."""


def _challenge_hint(body: str, controls: list[dict]) -> str | None:
    text = f"{body} {' '.join(str(control) for control in controls)}".lower()
    if any(term in text for term in ("captcha", "recaptcha", "hcaptcha", "cloudflare turnstile", "verify you are human", "i'm not a robot")):
        return "A CAPTCHA or human-verification challenge is present. Complete it manually, then resume the task."
    return None

SYSTEM = """You are Phoenix Autonomous Agent. Carry out the user's requested browser task using the available action tool. Be decisive and act, rather than only giving instructions. Never claim a step happened unless the tool result confirms it. Work only on the user's stated task. Use the mailbox tool when a task needs a fresh email alias or inbox messages. Keep account passwords and tokens out of summaries. Prefer semantic locators, then labels/placeholders, then CSS selectors. Each turn must return one JSON object only: {"action":..., ...}. Allowed actions: goto(url), click(target), fill(target,value), select(target,value), press(target,key), wait(milliseconds), inspect, mailbox_health, mailbox_session, mailbox_restore_session(alias_email), mailbox_accounts, mailbox_aliases, mailbox_create_alias(address_type, variation_type, variation_label, custom_name, gmail_account_id, use_primary), mailbox_messages(alias_id), mailbox_get_alias(alias_id), mailbox_update_alias(alias_id, fields), mailbox_delete_alias(alias_id), mailbox_list_messages, mailbox_get_message(message_id), mailbox_attachment(message_id, attachment_id), done(summary). Use 'target' as a visible label/text/placeholder or a CSS selector prefixed by 'css:'. Do not invent form field values. If a required value is missing, stop and ask for it. If a page requires email verification, check mailbox messages and use a code only if it is present in a message. Do not attempt to bypass CAPTCHA or access controls."""


def valid_url(url):
    p = urlparse(url)
    return p.scheme in {"http", "https"} and bool(p.netloc)

async def _snapshot(page):
    try: body = (await page.locator('body').inner_text(timeout=5000))[:7000]
    except Exception: body = ''
    try:
        controls = await page.locator('input,textarea,select,button,[role=button],[role=link]').evaluate_all("els => els.slice(0,100).map((e,i)=>({i,tag:e.tagName.toLowerCase(),type:e.type||'',text:(e.innerText||e.value||e.getAttribute('aria-label')||'').trim().slice(0,100),name:e.name||'',id:e.id||'',placeholder:e.placeholder||'',label:e.labels?.[0]?.innerText||'',required:!!e.required,href:e.href||''}))")
    except Exception: controls = []
    return {"url": page.url, "title": await page.title(), "body": body, "controls": controls, "challenge": _challenge_hint(body, controls)}

async def _choose_action(instruction, history, page_state, step, provider_choice="auto", model=None):
    prompt = {"task": instruction, "step": step, "max_steps": settings.max_agent_steps, "history": history[-12:], "page": page_state}
    raw, provider, used_model = await chat([{"role":"system","content":SYSTEM}, {"role":"user","content":json.dumps(prompt, ensure_ascii=False)}], provider=provider_choice, model=model)
    try:
        match = re.search(r'\{.*\}', raw, re.S)
        action = json.loads(match.group(0) if match else raw)
        if not isinstance(action, dict) or not isinstance(action.get('action'), str): raise ValueError('missing action')
        return action, provider, used_model
    except Exception:
        return {"action":"done", "summary":"The model did not return a valid action object. Raw response: " + raw[:800]}, provider, used_model

async def _locate(page, target):
    if not target: raise ValueError('Action requires a target')
    if target.startswith('css:'): return page.locator(target[4:]).first
    # Try accessible labels/placeholders/text before treating the target as a selector.
    for locator in (page.get_by_label(target, exact=False), page.get_by_placeholder(target, exact=False), page.get_by_role('button', name=target, exact=False), page.get_by_role('link', name=target, exact=False), page.get_by_text(target, exact=True)):
        try:
            if await locator.count() > 0: return locator.first
        except Exception: pass
    return page.locator(target).first

async def _browser_retry(operation, attempts=2):
    last_error = None
    for attempt in range(attempts):
        try:
            return await operation()
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(0.25 * (attempt + 1))
    raise last_error

async def execute_task(instruction, start_url=None, provider_choice="auto", model=None, progress: Callable[[str], Awaitable[None]] | None = None):
    async def report(message):
        if progress:
            await progress(message)
    url = start_url
    if not url:
        match = re.search(r"https?://[^\s)>\"\']+", instruction)
        if match: url = match.group(0).rstrip('.,')
    if url and not valid_url(url): return "Invalid start URL: use http or https."
    history = []
    used_providers = []
    used_models = []
    async with browser.session() as page:
        if url:
            await report(f"Opening {url}")
            await _browser_retry(lambda: page.goto(url, wait_until='domcontentloaded'))
            history.append({"tool":"goto", "url":page.url, "result":"navigation complete"})
        for step in range(1, settings.max_agent_steps + 1):
            await report(f"Choosing action {step}/{settings.max_agent_steps}")
            state = await _snapshot(page) if page.url else {"url":"", "title":"", "body":"No page open yet. Use goto with an explicit URL from the task, or ask user for target URL.", "controls":[]}
            if state.get("challenge"):
                raise HumanInterventionRequired(state["challenge"])
            action, provider, used_model = await _choose_action(instruction, history, state, step, provider_choice, model)
            used_providers.append(provider)
            used_models.append(used_model)
            name = action.get('action','').lower()
            try:
                if name == 'goto':
                    target_url = str(action.get('url',''))
                    if not valid_url(target_url): raise ValueError('goto requires a valid http/https URL')
                    await _browser_retry(lambda: page.goto(target_url, wait_until='domcontentloaded'))
                    result = {"url":page.url,"title":await page.title()}
                elif name == 'click':
                    loc = await _locate(page, str(action.get('target',''))); await _browser_retry(loc.click); result = {"clicked":action.get('target'),"url":page.url}
                elif name == 'fill':
                    loc = await _locate(page, str(action.get('target',''))); await _browser_retry(lambda: loc.fill(str(action.get('value','')))); result = {"filled":action.get('target'),"value":"[redacted]"}
                elif name == 'select':
                    loc = await _locate(page, str(action.get('target',''))); await _browser_retry(lambda: loc.select_option(str(action.get('value','')))); result = {"selected":action.get('target')}
                elif name == 'press':
                    loc = await _locate(page, str(action.get('target',''))); await _browser_retry(lambda: loc.press(str(action.get('key','Enter')))); result = {"pressed":action.get('key','Enter'),"target":action.get('target')}
                elif name == 'wait':
                    await page.wait_for_timeout(max(0, min(int(action.get('milliseconds',500)),5000))); result = {"waited_ms":max(0,min(int(action.get('milliseconds',500)),5000))}
                elif name == 'inspect': result = await _snapshot(page)
                elif name == 'mailbox_health': result = await mailbox.health()
                elif name == 'mailbox_session': result = await mailbox.session()
                elif name == 'mailbox_restore_session': result = await mailbox.restore_session(str(action.get('alias_email','')))
                elif name == 'mailbox_accounts': result = await mailbox.list_accounts()
                elif name == 'mailbox_aliases': result = await mailbox.list_aliases()
                elif name == 'mailbox_get_alias': result = await mailbox.get_alias(str(action.get('alias_id','')))
                elif name == 'mailbox_update_alias': result = await mailbox.update_alias(str(action.get('alias_id','')), action.get('fields',{}))
                elif name == 'mailbox_delete_alias': result = await mailbox.delete_alias(str(action.get('alias_id','')))
                elif name == 'mailbox_create_alias':
                    result = await mailbox.create_alias(address_type=action.get('address_type','alias'), variation_type=action.get('variation_type','dots'), variation_label=action.get('variation_label'), custom_name=action.get('custom_name'), gmail_account_id=action.get('gmail_account_id'), use_primary=bool(action.get('use_primary',False)))
                elif name == 'mailbox_messages': result = await mailbox.list_alias_messages(str(action.get('alias_id','')))
                elif name == 'mailbox_list_messages': result = await mailbox.list_messages()
                elif name == 'mailbox_get_message': result = await mailbox.get_message(str(action.get('message_id','')))
                elif name == 'mailbox_attachment': result = await mailbox.get_attachment(str(action.get('message_id','')), str(action.get('attachment_id','')))
                elif name == 'done':
                    summary = str(action.get('summary','Task loop finished.'))
                    history.append({"action":"done","result":summary})
                    return _format_result(summary, history, used_providers, used_models)
                else: raise ValueError(f"Unsupported action: {name}")
                # Never keep values typed into forms in task history.
                history.append({"action":name,"target":action.get('target'),"result":result})
                await report(f"Verified action {step}: {name}")
            except Exception as exc:
                history.append({"action":name,"target":action.get('target'),"error":f"{type(exc).__name__}: {str(exc)[:300]}"})
                await report(f"Action {step} failed: {type(exc).__name__}")
    return _format_result(f"Stopped after reaching the {settings.max_agent_steps}-step limit. Review the verified actions below and submit a follow-up task to continue.", history, used_providers, used_models)

def _format_result(summary, history, providers, models=None):
    clean = []
    for item in history:
        clean.append(item)
    return json.dumps({"summary":summary,"actions":clean,"providers_used":list(dict.fromkeys(providers)),"models_used":list(dict.fromkeys(models or []))}, ensure_ascii=False, indent=2)
