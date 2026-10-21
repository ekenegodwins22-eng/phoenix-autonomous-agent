import asyncio, json, re
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse
from app.browser import browser
from app.providers import chat
from app.mailbox import mailbox
from app.config import settings
from app.credentials import generate_value
from app.form_data import synthetic_profile, classify_field, constrained_value
from app.agent.action_schema import ActionRequest
from app.agent.prompt_loader import InstructionConfigError, build_effective_instructions
from app.proxy import configured_proxy

class HumanInterventionRequired(RuntimeError):
    """Raised when a page requires a human challenge or access-control decision."""


class TaskBlocked(RuntimeError):
    """Raised when execution cannot begin with the supplied task parameters."""


class TaskIncomplete(RuntimeError):
    """Raised when the bounded execution budget ends before verified completion."""


def _challenge_info(body: str, controls: list[dict]) -> dict | None:
    visible_controls = [control for control in controls if control.get("visible", True)]
    text = f"{body} {' '.join(str(control) for control in visible_controls)}".lower()
    if "hcaptcha" in text:
        challenge_type = "hcaptcha"
    elif "recaptcha" in text:
        challenge_type = "recaptcha"
    elif "turnstile" in text or "cloudflare" in text:
        challenge_type = "cloudflare_turnstile"
    elif any(term in text for term in ("captcha", "verify you are human", "i'm not a robot")):
        challenge_type = "generic_human_verification"
    else:
        return None
    methods = ["authorized_site_accessible_alternative"] if "audio" in text or "accessibility" in text else []
    methods.append("human_completion")
    return {"type": challenge_type, "resolution_methods": methods, "message": f"Detected {challenge_type}; use an authorized resolution method, then resume the task."}

def _challenge_hint(body: str, controls: list[dict]) -> str | None:
    info = _challenge_info(body, controls)
    if info:
        return info["message"]
    return None

SYSTEM = """Operational behavior is loaded from the repository AGENT.md file. Follow it as the authoritative configurable instruction source. Return exactly one JSON action object. Action protocol: goto(url), click(target), fill(target,value), fill_form, fill_generated(target,name), generate_credential(name,kind,length,prefix), select(target,value), press(target,key), wait(milliseconds), inspect, mailbox_health, mailbox_session, mailbox_restore_session(alias_email), mailbox_accounts, mailbox_aliases, mailbox_prepare_email(target,variation_type,address_type,gmail_account_id), mailbox_messages(alias_id), mailbox_get_alias(alias_id), mailbox_update_alias(alias_id,fields), mailbox_delete_alias(alias_id), mailbox_create_alias(address_type,variation_type,variation_label,custom_name,gmail_account_id,use_primary), mailbox_list_messages, mailbox_get_message(message_id), mailbox_fill_verification_code(target,pattern,alias_id), mailbox_open_verification_link(alias_id), mailbox_attachment(message_id,attachment_id), done(summary). Use target as a visible label/text/placeholder or css:selector. Never expose secrets, credentials, cookies, OTPs, or verification URLs in results."""


def valid_url(url):
    p = urlparse(url)
    return p.scheme in {"http", "https"} and bool(p.netloc)

async def _snapshot(page):
    try: body = (await page.locator('body').inner_text(timeout=5000))[:7000]
    except Exception: body = ''
    try:
        controls = await page.locator('input,textarea,select,button,[role=button],[role=link]').evaluate_all("""els => els.filter(e => {
            const style = window.getComputedStyle(e);
            const rect = e.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden' &&
                Number(style.opacity) > 0 && rect.width > 0 && rect.height > 0 &&
                !e.closest('[aria-hidden="true"]');
        }).slice(0,100).map((e,i) => {
            const metadata = [e.type, e.name, e.id, e.placeholder,
                e.getAttribute('aria-label') || '', e.labels?.[0]?.innerText || ''].join(' ');
            const sensitive = /password|passcode|secret|token|one.?time|verification|otp/i.test(metadata);
            const value = String(e.value || '');
            return {i,tag:e.tagName.toLowerCase(),type:e.type||'',visible:true,
                text:(sensitive && value ? '[redacted]' : (e.innerText||value||e.getAttribute('aria-label')||'')).trim().slice(0,100),
                name:e.name||'',id:e.id||'',placeholder:e.placeholder||'',label:e.labels?.[0]?.innerText||'',required:!!e.required,href:e.href||''};
        })""")
    except Exception: controls = []
    return {"url": page.url, "title": await page.title(), "body": body, "controls": controls, "challenge": _challenge_info(body, controls)}

async def _choose_action(instruction, history, page_state, step, provider_choice="auto", model=None):
    agent_instructions, instruction_version, _ = build_effective_instructions()
    system_prompt = SYSTEM + "\n\nACTIVE AGENT INSTRUCTIONS (version " + instruction_version + "):\n" + agent_instructions
    prompt = {"task": instruction, "step": step, "max_steps": settings.max_agent_steps, "history": history[-12:], "page": page_state}
    last_provider, last_model = provider_choice, model
    for attempt in range(max(0, settings.model_action_retries) + 1):
        if attempt:
            prompt['correction'] = 'The previous response was not a supported action or omitted required arguments. Continue the unfinished task and return exactly one JSON action object; do not explain or claim completion.'
        raw, last_provider, last_model = await chat([{"role":"system","content":system_prompt}, {"role":"user","content":json.dumps(prompt, ensure_ascii=False)}], provider=provider_choice, model=model)
        try:
            match = re.search(r'\{.*\}', raw, re.S)
            action = json.loads(match.group(0) if match else raw)
            if not isinstance(action, dict) or not isinstance(action.get('action'), str):
                raise ValueError('missing action')
            action['action'] = action['action'].lower()
            validated_action = ActionRequest.model_validate(action)
            return validated_action.model_dump(exclude_unset=True, exclude_none=True), last_provider, last_model
        except Exception:
            continue
    return {"action":"inspect"}, last_provider, last_model

async def _locate(page, target):
    if not target: raise ValueError('Action requires a target')
    if target.startswith('css:'): return page.locator(target[4:]).first
    # Try accessible labels/placeholders/text before treating the target as a selector.
    for locator in (page.get_by_label(target, exact=False), page.get_by_placeholder(target, exact=False), page.get_by_role('button', name=target, exact=False), page.get_by_role('link', name=target, exact=False), page.get_by_text(target, exact=True)):
        try:
            if await locator.count() > 0: return locator.first
        except Exception: pass
    return page.locator(target).first

async def _browser_retry(operation, attempts=None):
    attempts = attempts or settings.browser_retry_attempts
    last_error = None
    for attempt in range(attempts):
        try:
            return await operation()
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(0.25 * (attempt + 1))
    raise last_error


async def _inspect_form_fields(page):
    return await page.locator('input,textarea,select').evaluate_all("""els => els.map((e,i) => {
        e.setAttribute('data-phoenix-form-field', String(i));
        const label = e.labels && e.labels[0] ? e.labels[0].innerText : '';
        return {index:i, tag:e.tagName.toLowerCase(), type:(e.type||'text').toLowerCase(),
          label:(label||'').trim().slice(0,160), placeholder:e.placeholder||'', name:e.name||'', id:e.id||'',
          autocomplete:e.autocomplete||'', aria_label:e.getAttribute('aria-label')||'', required:!!e.required,
          disabled:!!e.disabled, readonly:!!e.readOnly, minlength:e.minLength>0?e.minLength:null,
          maxlength:e.maxLength>0?e.maxLength:null, min:e.min||null, max:e.max||null, pattern:e.pattern||null,
          value:e.value||'', checked:!!e.checked, options:e.tagName.toLowerCase()==='select' ? Array.from(e.options).map(o=>({text:(o.textContent||'').trim(),value:o.value,disabled:!!o.disabled})) : []};
      })""")


def _registration_needs_mailbox_email(instruction: str, state: dict) -> bool:
    text = str(instruction or '').lower()
    if not any(word in text for word in ('register', 'registration', 'sign up', 'signup', 'create account', 'create an account')):
        return False
    if re.search(r'\b[^\s@]+@[^\s@]+\.[^\s@]+\b', instruction or ''):
        return False
    return any(
        str(control.get('type', '')).lower() == 'email'
        or str(control.get('autocomplete', '')).lower() == 'email'
        or any(token in f"{control.get('name', '')} {control.get('id', '')} {control.get('label', '')} {control.get('placeholder', '')}".lower() for token in ('email', 'e-mail'))
        for control in state.get('controls', [])
    )


def _registration_observation(instruction: str, state: dict) -> dict | None:
    """Classify the observed post-action registration state; never claims success from a click alone."""
    text = f"{state.get('title', '')} {state.get('body', '')} {' '.join(str(x) for x in state.get('controls', []))}".lower()
    intent = str(instruction or '').lower()
    if not any(word in intent for word in ('register', 'registration', 'sign up', 'signup', 'create account', 'create an account')):
        return None
    if any(x in text for x in ('identity verification', 'verify your identity', 'government id')):
        outcome = 'identity_verification_required'
    elif any(x in text for x in ('phone verification', 'verify your phone', 'sms code', 'phone number verification')):
        outcome = 'phone_verification_required'
    elif state.get('challenge'):
        outcome = 'captcha_or_human_verification_required'
    elif any(x in text for x in ('verify your email', 'email verification', 'check your inbox', 'verification link', 'confirmation email')):
        outcome = 'email_verification_required'
    elif any(x in text for x in ('registration rejected', 'unable to create', 'already registered', 'invalid registration', 'sign up failed')):
        outcome = 'registration_rejected'
    elif any(x in text for x in ('account created', 'welcome', 'registration successful', 'successfully registered', 'dashboard')):
        outcome = 'registration_succeeded'
    elif any(str(c.get('type', '')).lower() in {'email', 'password'} for c in state.get('controls', [])):
        outcome = 'registration_form_displayed'
    else:
        outcome = 'registration_outcome_unknown'
    return {'state': outcome, 'url': state.get('url'), 'title': state.get('title'), 'evidence_excerpt': str(state.get('body', ''))[:500]}


async def _prepare_mailbox_email(page, state: dict, mailbox_context: dict):
    prepared = await mailbox.prepare_address(variation_type='dots', address_type='alias')
    target = next((control for control in state.get('controls', []) if str(control.get('type', '')).lower() == 'email' or str(control.get('autocomplete', '')).lower() == 'email' or 'email' in f"{control.get('name', '')} {control.get('id', '')} {control.get('label', '')} {control.get('placeholder', '')}".lower()), None)
    if not target:
        raise ValueError('No email input was available for the connected mailbox address')
    if target.get('id'):
        locator = page.locator(f'#{target["id"]}').first
    elif target.get('name'):
        locator = page.locator(f'[name="{target["name"]}"]').first
    else:
        locator = page.locator('input[type="email"], input[autocomplete="email"]').first
    await _browser_retry(lambda: locator.fill(prepared['address']))
    mailbox_context.update(prepared)
    return {'filled': target.get('label') or target.get('name') or target.get('id') or 'email', 'alias_id': prepared.get('alias_id'), 'gmail_account_id': prepared.get('gmail_account_id'), 'reused': prepared.get('reused'), 'variation_type': prepared.get('variation_type')}


def _fill_form(page):
    profile = synthetic_profile()
    fields = await _inspect_form_fields(page)
    filled, skipped, errors = [], [], []
    radio_groups = set()
    for field in fields:
        kind = classify_field(field)
        selector = f'[data-phoenix-form-field="{field["index"]}"]'
        loc = page.locator(selector).first
        try:
            if not kind:
                skipped.append({'field': field.get('label') or field.get('name') or field.get('id') or selector, 'reason': 'unsupported-or-noneditable'})
                continue
            if kind == 'checkbox':
                if field.get('required') and not field.get('checked'):
                    await _browser_retry(loc.check)
                    filled.append({'field': field.get('label') or field.get('name') or selector, 'kind': kind})
                else:
                    skipped.append({'field': field.get('label') or field.get('name') or selector, 'reason': 'optional-checkbox'})
                continue
            if kind == 'radio':
                group = field.get('name') or selector
                if group in radio_groups or field.get('checked'):
                    skipped.append({'field': field.get('label') or field.get('name') or selector, 'reason': 'radio-group-already-selected'})
                else:
                    await _browser_retry(loc.check); radio_groups.add(group)
                    filled.append({'field': field.get('label') or field.get('name') or selector, 'kind': kind})
                continue
            if kind == 'select':
                options = [o for o in field.get('options', []) if not o.get('disabled') and o.get('value')]
                if not options:
                    skipped.append({'field': field.get('label') or field.get('name') or selector, 'reason': 'no-selectable-options'}); continue
                value = field.get('value') or options[0]['value']
                await _browser_retry(lambda: loc.select_option(value))
                filled.append({'field': field.get('label') or field.get('name') or selector, 'kind': kind, 'value': '[selected-option]'})
                continue
            value = constrained_value(field, kind, profile)
            if value is None:
                skipped.append({'field': field.get('label') or field.get('name') or selector, 'reason': 'field-requirement-not-understood'}); continue
            await _browser_retry(lambda value=value: loc.fill(value))
            filled.append({'field': field.get('label') or field.get('name') or selector, 'kind': kind, 'value': '[synthetic-redacted]'})
        except Exception as exc:
            errors.append({'field': field.get('label') or field.get('name') or selector, 'error': f'{type(exc).__name__}: {str(exc)[:160]}'})
    return {'fields_detected': len(fields), 'fields_filled': filled, 'fields_skipped': skipped, 'validation_or_interaction_errors': errors, 'synthetic_data': True, 'submitted': False}

async def execute_task(instruction, start_url=None, provider_choice="auto", model=None, progress: Callable[[str], Awaitable[None]] | None = None, on_waiting_human=None, resume_event=None, task_id=None, cancel_event=None, get_user_messages: Callable[[], Awaitable[list[dict]]] | None = None, save_checkpoint: Callable[[dict], Awaitable[None]] | None = None, proxy_mode: str = 'direct'):
    async def report(message):
        if progress:
            await progress(message)
    url = start_url
    if not url:
        match = re.search(r"https?://[^\s)>\"\']+", instruction)
        if match: url = match.group(0).rstrip('.,')
    if url and not valid_url(url):
        raise TaskBlocked('Invalid start URL: use http or https.')
    history = []
    used_providers = []
    used_models = []
    generated_credentials = {}
    mailbox_context = {}
    mailbox_email_attempted = False
    proxy = configured_proxy() if proxy_mode == 'configured' else None
    if proxy_mode == 'configured' and not proxy:
        raise TaskBlocked('Configured proxy requested but no protected proxy is available.')
    if proxy_mode == 'direct' and settings.browser_proxy_required:
        raise TaskBlocked('Direct connection is disabled because this deployment requires a proxy.')
    async with browser.session(proxy=proxy) as page:
        browser.current_task_id = task_id
        if url:
            await report(f"Opening {url}")
            await _browser_retry(lambda: page.goto(url, wait_until='domcontentloaded'))
            history.append({"tool":"goto", "url":page.url, "result":"navigation complete"})
        for step in range(1, settings.max_agent_steps + 1):
            if cancel_event and cancel_event.is_set():
                raise asyncio.CancelledError()
            if get_user_messages:
                for message in await get_user_messages():
                    history.append({"tool": "user_message", "message": message.get("content", "")})
                    await report("Received a new user instruction from the task chat")
            await report(f"Choosing action {step}/{settings.max_agent_steps}")
            state = await _snapshot(page) if page.url else {"url":"", "title":"", "body":"No page open yet. Use goto with an explicit URL from the task, or ask user for target URL.", "controls":[]}
            while state.get("challenge"):
                if cancel_event and cancel_event.is_set():
                    raise asyncio.CancelledError()
                details = state["challenge"]
                if on_waiting_human:
                    await on_waiting_human(details)
                if resume_event is None:
                    raise HumanInterventionRequired(json.dumps(details, ensure_ascii=False))
                await resume_event.wait()
                resume_event.clear()
                await report("Resuming after human verification")
                state = await _snapshot(page)
            if not mailbox_email_attempted and _registration_needs_mailbox_email(instruction, state):
                mailbox_email_attempted = True
                try:
                    result = await _prepare_mailbox_email(page, state, mailbox_context)
                    history.append({'action': 'mailbox_prepare_email', 'target': result.get('filled'), 'result': result})
                    await report('Verified action: connected mailbox email prepared and filled')
                    continue
                except Exception as exc:
                    history.append({'action': 'mailbox_prepare_email', 'error': f'{type(exc).__name__}: {str(exc)[:300]}'})
                    await report(f'Connected mailbox email was not available: {type(exc).__name__}')
            action, provider, used_model = await _choose_action(instruction, history, state, step, provider_choice, model)
            used_providers.append(provider)
            used_models.append(used_model)
            name = action.get('action','').lower()
            try:
                if cancel_event and cancel_event.is_set():
                    raise asyncio.CancelledError()
                if name == 'goto':
                    target_url = str(action.get('url',''))
                    if not valid_url(target_url): raise ValueError('goto requires a valid http/https URL')
                    await _browser_retry(lambda: page.goto(target_url, wait_until='domcontentloaded'))
                    result = {"url":page.url,"title":await page.title()}
                elif name == 'click':
                    loc = await _locate(page, str(action.get('target',''))); await _browser_retry(loc.click); result = {"clicked":action.get('target'),"url":page.url}
                elif name == 'fill':
                    loc = await _locate(page, str(action.get('target',''))); await _browser_retry(lambda: loc.fill(str(action.get('value','')))); result = {"filled":action.get('target'),"value":"[redacted]"}
                elif name == 'fill_form':
                    result = await _fill_form(page)
                elif name == 'generate_credential':
                    credential_name = str(action.get('name','')).strip()
                    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', credential_name): raise ValueError('generate_credential requires a safe name')
                    generated_credentials[credential_name] = generate_value(action.get('kind',''), int(action.get('length',20)), str(action.get('prefix','phoenix')))
                    result = {"stored_as":credential_name,"kind":str(action.get('kind','')).lower()}
                elif name == 'fill_generated':
                    credential_name = str(action.get('name','')).strip()
                    if credential_name not in generated_credentials: raise ValueError('No generated credential exists with that name')
                    loc = await _locate(page, str(action.get('target',''))); await _browser_retry(lambda: loc.fill(generated_credentials[credential_name])); result = {"filled":action.get('target'),"value":"[redacted]","credential":"[redacted]"}
                elif name == 'mailbox_prepare_email':
                    prepared = await mailbox.prepare_address(variation_type=str(action.get('variation_type','dots')), address_type=str(action.get('address_type','alias')), gmail_account_id=action.get('gmail_account_id'))
                    mailbox_context.update(prepared)
                    loc = await _locate(page, str(action.get('target',''))); await _browser_retry(lambda: loc.fill(prepared['address']))
                    result = {"filled":action.get('target'),"alias_id":prepared.get('alias_id'),"gmail_account_id":prepared.get('gmail_account_id'),"reused":prepared.get('reused'),"variation_type":prepared.get('variation_type')}
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
                elif name == 'mailbox_fill_verification_code':
                    verification = await mailbox.find_verification_code(str(action.get('pattern', r'\b\d{4,8}\b')), str(action.get('alias_id') or mailbox_context.get('alias_id')) if (action.get('alias_id') or mailbox_context.get('alias_id')) else None)
                    if not verification: raise ValueError('No verification code was found in the authorized mailbox')
                    loc = await _locate(page, str(action.get('target',''))); await _browser_retry(lambda: loc.fill(verification['code']))
                    result = {"filled":action.get('target'),"message_id":verification.get('message_id'),"value":"[redacted]"}
                elif name == 'mailbox_open_verification_link':
                    verification = await mailbox.find_verification_link(str(action.get('alias_id') or mailbox_context.get('alias_id')) if (action.get('alias_id') or mailbox_context.get('alias_id')) else None)
                    if not verification: raise ValueError('No verification link was found in the authorized mailbox')
                    await _browser_retry(lambda: page.goto(verification['url'], wait_until='domcontentloaded'))
                    result = {"opened":True,"message_id":verification.get('message_id'),"url":"[redacted]"}
                elif name == 'mailbox_attachment': result = await mailbox.get_attachment(str(action.get('message_id','')), str(action.get('attachment_id','')))
                elif name == 'done':
                    summary = str(action.get('summary','Task loop finished.'))
                    evidence = await _snapshot(page) if page.url else {'url': '', 'title': '', 'body': ''}
                    history.append({"action":"done","result":summary,"evidence":{"url":evidence.get('url'),"title":evidence.get('title'),"body_excerpt":evidence.get('body','')[:500]}})
                    if save_checkpoint:
                        await save_checkpoint({'step': step, 'action': 'done', 'url': evidence.get('url'), 'title': evidence.get('title'), 'verified': True, 'proxy_mode': proxy_mode, 'proxy_identifier': proxy.get('identifier') if proxy else None})
                    return _format_result(summary, history, used_providers, used_models)
                else: raise ValueError(f"Unsupported action: {name}")
                registration = None
                if name in {'click', 'press', 'fill_form', 'select', 'mailbox_prepare_email', 'mailbox_fill_verification_code', 'mailbox_open_verification_link'}:
                    registration = _registration_observation(instruction, await _snapshot(page))
                    if registration:
                        result = {**(result if isinstance(result, dict) else {'result': result}), 'registration': registration}
                # Never keep values typed into forms in task history.
                history.append({"action":name,"target":action.get('target'),"result":result})
                if save_checkpoint:
                    await save_checkpoint({'step': step, 'action': name, 'target': action.get('target'), 'url': page.url, 'proxy_mode': proxy_mode, 'proxy_identifier': proxy.get('identifier') if proxy else None, 'registration': registration})
                await report(f"Verified action {step}: {name}")
            except Exception as exc:
                history.append({"action":name,"target":action.get('target'),"error":f"{type(exc).__name__}: {str(exc)[:300]}"})
                if save_checkpoint:
                    await save_checkpoint({'step': step, 'action': name, 'target': action.get('target'), 'url': page.url, 'error_type': type(exc).__name__})
                await report(f"Action {step} failed: {type(exc).__name__}")
    raise TaskIncomplete(f"Execution budget exhausted after {settings.max_agent_steps} actions before Phoenix could verify completion.")

def _format_result(summary, history, providers, models=None):
    clean = []
    for item in history:
        clean.append(item)
    return json.dumps({"summary":summary,"actions":clean,"providers_used":list(dict.fromkeys(providers)),"models_used":list(dict.fromkeys(models or []))}, ensure_ascii=False, indent=2)
