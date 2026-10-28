"""Provider clients: Phoenix AI Router (default) and opt-in user-key providers (Gemini, OpenAI, Anthropic)."""
from collections.abc import AsyncIterator
from contextvars import ContextVar
import asyncio
import json
import logging
import time
import httpx
from app.config import settings

class ProviderError(RuntimeError):
    pass


log = logging.getLogger(__name__)
TRANSIENT_CHAT_STATUS_CODES = {429, 502, 503, 504}
_model_cache: tuple[str, float, list[str]] | None = None

def _primary_headers() -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if settings.primary_api_key:
        headers["X-API-Key"] = settings.primary_api_key
    return headers

def _primary_url(path: str) -> str:
    base = settings.primary_base_url.rstrip("/")
    if not base:
        raise ProviderError("PRIMARY_BASE_URL is not configured")
    return f"{base}/{path.lstrip('/')}"

def _raise_upstream(response: httpx.Response, operation: str, model: str | None = None) -> None:
    if response.is_error:
        suffix = f" for model {model}" if model else ""
        raise ProviderError(f"Phoenix AI {operation} failed with HTTP {response.status_code}{suffix}")

def _extract_model_ids(data) -> list[str]:
    found, seen = [], set()
    def add(value):
        if isinstance(value, str) and value.strip() and value.strip() not in seen:
            seen.add(value.strip()); found.append(value.strip())
    def walk(value, key_hint=""):
        if isinstance(value, str):
            if key_hint.lower() in {"id", "name", "model", "model_id", "model_name"}: add(value)
        elif isinstance(value, list):
            for item in value: add(item) if isinstance(item, str) else walk(item)
        elif isinstance(value, dict):
            candidate = value.get("id") or value.get("model_id") or value.get("model") or value.get("name")
            if isinstance(candidate, str): add(candidate)
            else:
                for key, child in value.items(): walk(child, key)
    walk(data)
    return found

async def primary_models() -> list[str]:
    """List Phoenix AI models via the OpenAI-compatible GET {base}/models.

    If the list call fails (404, timeout, empty list), fall back to just PRIMARY_MODEL
    so chat still runs against the configured model.
    """
    if not settings.primary_api_key:
        raise ProviderError("PRIMARY_API_KEY is not configured")
    global _model_cache
    cache_key = settings.primary_base_url.rstrip('/')
    now = time.monotonic()
    if _model_cache and _model_cache[0] == cache_key and now - _model_cache[1] < settings.primary_model_cache_seconds:
        return list(_model_cache[2])
    attempts = max(1, settings.primary_model_discovery_attempts)
    last_error = None
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=min(settings.primary_timeout_seconds, 20)) as client:
                response = await client.get(_primary_url("models"), headers=_primary_headers())
            _raise_upstream(response, "/models")
            models = _extract_model_ids(response.json())
            if not models:
                raise ProviderError("Phoenix AI /models returned no recognizable model IDs")
            _model_cache = (cache_key, time.monotonic(), models)
            return models
        except ProviderError as exc:
            last_error = exc
            retryable = 'HTTP 502' in str(exc) or 'HTTP 503' in str(exc) or 'HTTP 504' in str(exc)
        except (httpx.HTTPError, ValueError) as exc:
            last_error = ProviderError(f"Phoenix AI model discovery failed: {type(exc).__name__}")
            retryable = True
        if not retryable or attempt + 1 >= attempts:
            break
        delay = min(settings.primary_model_discovery_retry_base_seconds * (2 ** attempt), settings.primary_chat_retry_max_seconds)
        log.warning("Phoenix AI /models failed (%s); retrying in %.2fs (%d/%d)", last_error, delay, attempt + 1, attempts - 1)
        await asyncio.sleep(delay)
    fallback = (settings.primary_model or "").strip()
    log.warning("Phoenix AI model list unavailable (%s); using PRIMARY_MODEL=%s only", last_error, fallback or "none")
    return [fallback] if fallback else []

def _extract_chat_text(data) -> str:
    if isinstance(data, str): return data
    if not isinstance(data, dict): raise ProviderError("Phoenix AI chat returned an unexpected response type")
    for key in ("response", "reply", "content", "message", "text", "output", "generated_text"):
        value = data.get(key)
        if isinstance(value, str) and value.strip(): return value
        if isinstance(value, dict):
            for nested in ("content", "text", "response", "message"):
                if isinstance(value.get(nested), str) and value[nested].strip(): return value[nested]
    for key in ("result", "data"):
        if isinstance(data.get(key), (dict, str)):
            try: return _extract_chat_text(data[key])
            except ProviderError: pass
    raise ProviderError("Phoenix AI chat response did not contain a recognized text field")

async def _validated_primary_model(model: str | None) -> str:
    models = await primary_models()
    chosen = model or settings.primary_model or models[0]
    if chosen not in models: raise ProviderError(f"Selected Phoenix model is unavailable: {chosen}")
    return chosen

def _prompt(messages) -> str:
    return "\n\n".join(f'{str(item.get("role", "user")).upper()}:\n{item.get("content", "")}' for item in messages)

async def primary_chat(messages, model=None) -> str:
    chosen_model = await _validated_primary_model(model)
    payload = {"model": chosen_model, "message": _prompt(messages)}
    attempts = max(1, settings.primary_chat_retry_attempts)
    try:
        response = None
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=settings.primary_timeout_seconds) as client:
                    response = await client.post(_primary_url("chat"), headers=_primary_headers(), json=payload)
            except httpx.RequestError as exc:
                if attempt + 1 >= attempts:
                    raise ProviderError(f"Phoenix AI chat failed: {type(exc).__name__}") from exc
                delay = min(settings.primary_chat_retry_base_seconds * (2 ** attempt), settings.primary_chat_retry_max_seconds)
                log.warning("Phoenix AI /chat transient network failure (%s); retrying in %.2fs (%d/%d)", type(exc).__name__, delay, attempt + 1, attempts - 1)
                await asyncio.sleep(delay)
                continue
            if response.status_code not in TRANSIENT_CHAT_STATUS_CODES or attempt + 1 >= attempts:
                break
            retry_after = response.headers.get('retry-after')
            try: delay = min(float(retry_after), settings.primary_chat_retry_max_seconds) if retry_after else None
            except ValueError: delay = None
            if delay is None: delay = min(settings.primary_chat_retry_base_seconds * (2 ** attempt), settings.primary_chat_retry_max_seconds)
            log.warning("Phoenix AI /chat returned HTTP %s; retrying in %.2fs (%d/%d)", response.status_code, delay, attempt + 1, attempts - 1)
            await asyncio.sleep(delay)
        _raise_upstream(response, "/chat", chosen_model)
        return _extract_chat_text(response.json())
    except ProviderError: raise
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError(f"Phoenix AI chat failed: {type(exc).__name__}") from exc

def _stream_piece(data) -> str:
    try: return _extract_chat_text(data)
    except ProviderError:
        if isinstance(data, dict):
            for key in ("delta", "token", "chunk"):
                value = data.get(key)
                if isinstance(value, str): return value
                if isinstance(value, dict):
                    try: return _extract_chat_text(value)
                    except ProviderError: pass
        return ""

async def primary_chat_stream(messages, model=None) -> AsyncIterator[str]:
    """Yield text from the custom SSE/line stream at POST /chat/stream."""
    chosen_model = await _validated_primary_model(model)
    try:
        async with httpx.AsyncClient(timeout=settings.primary_timeout_seconds) as client:
            async with client.stream("POST", _primary_url("chat/stream"), headers=_primary_headers(), json={"model": chosen_model, "message": _prompt(messages)}) as response:
                _raise_upstream(response, "/chat/stream", chosen_model)
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or line.startswith(":"): continue
                    if line.startswith("data:"): line = line[5:].strip()
                    if line == "[DONE]": break
                    try: piece = _stream_piece(json.loads(line))
                    except json.JSONDecodeError: piece = line
                    if piece: yield piece
    except ProviderError: raise
    except httpx.HTTPError as exc:
        raise ProviderError(f"Phoenix AI streaming chat failed: {type(exc).__name__}") from exc

async def gemini_models(): return [m.strip() for m in settings.gemini_models.split(",") if m.strip()]

# Owner whose task is running. Set by the task runner before it starts execute_task, so the
# router can read that owner's saved provider keys without changing every call signature.
_owner_ctx: ContextVar[int | None] = ContextVar("phoenix_owner_id", default=None)

def set_owner_context(owner_id: int | None) -> None:
    _owner_ctx.set(owner_id)

def _keys_for(owner: int) -> dict:
    try:
        from app import settings_store
        return settings_store.list_provider_keys(owner)
    except Exception as exc:
        log.warning("could not read saved provider keys: %s", type(exc).__name__)
        return {}

def _user_keys() -> dict:
    owner = _owner_ctx.get()
    return _keys_for(owner) if owner is not None else {}

FALLBACK_ORDER = ("gemini", "openai", "anthropic")

async def gemini_chat(messages, model=None, api_key=None):
    key = api_key or settings.gemini_api_key
    if not key: raise ProviderError("Gemini key is not saved. Add it in Settings to use Gemini.")
    chosen_model = model or settings.gemini_model
    try:
        async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
            response = await client.post(f"https://generativelanguage.googleapis.com/v1beta/models/{chosen_model}:generateContent", params={"key": key}, json={"contents": [{"role": "user", "parts": [{"text": _prompt(messages)}]}], "generationConfig": {"temperature": 0.2}})
        _raise_upstream(response, "Gemini")
        data = response.json(); return data["candidates"][0]["content"]["parts"][0]["text"]
    except ProviderError: raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"Gemini response did not contain generated text: {type(exc).__name__}") from exc

OPENAI_MODEL_DEFAULT = "gpt-4o-mini"
ANTHROPIC_MODEL_DEFAULT = "claude-3-5-haiku-latest"

async def openai_chat(messages, model=None, api_key=None):
    if not api_key: raise ProviderError("OpenAI key is not saved. Add it in Settings to use OpenAI.")
    try:
        async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
            response = await client.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": f"Bearer {api_key}"}, json={"model": model or OPENAI_MODEL_DEFAULT, "messages": [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in messages]})
        _raise_upstream(response, "OpenAI")
        return response.json()["choices"][0]["message"]["content"]
    except ProviderError: raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"OpenAI response did not contain generated text: {type(exc).__name__}") from exc

async def anthropic_chat(messages, model=None, api_key=None):
    if not api_key: raise ProviderError("Anthropic key is not saved. Add it in Settings to use Claude.")
    system = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
    convo = [{"role": "assistant" if m.get("role") == "assistant" else "user", "content": str(m.get("content", ""))} for m in messages if m.get("role") != "system"] or [{"role": "user", "content": _prompt(messages)}]
    payload = {"model": model or ANTHROPIC_MODEL_DEFAULT, "max_tokens": 4096, "messages": convo}
    if system: payload["system"] = system
    try:
        async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
            response = await client.post("https://api.anthropic.com/v1/messages", headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"}, json=payload)
        _raise_upstream(response, "Anthropic")
        return "".join(b.get("text", "") for b in response.json()["content"] if b.get("type") == "text")
    except ProviderError: raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"Anthropic response did not contain generated text: {type(exc).__name__}") from exc

_USER_CHAT = {"gemini": gemini_chat, "openai": openai_chat, "anthropic": anthropic_chat}

async def available_models(owner_id: int | None = None):
    result = {"providers": [], "default_provider": "primary", "default_model": settings.primary_model}
    try:
        models = await primary_models(); result["providers"].append({"id":"primary","label":"Phoenix AI Router","available":True,"models":[{"id":m,"label":m} for m in models]})
    except Exception as exc:
        result["providers"].append({"id":"primary","label":"Phoenix AI Router","available":False,"error":str(exc)[:180],"models":[]})
    saved = _user_keys() if owner_id is None else _keys_for(owner_id)
    labels = {"gemini": "Google Gemini", "openai": "OpenAI", "anthropic": "Anthropic"}
    for pid in FALLBACK_ORDER:
        has = pid in saved
        result["providers"].append({"id": pid, "label": labels[pid], "available": has, "error": None if has else "Add your own key in Settings to enable", "models": []})
    result["providers"].append({"id":"auto","label":"Automatic fallback","available":True,"models":[{"id":"","label":"Use configured defaults"}]})
    return result

async def _primary_chain(messages, model):
    chain: list[str] = []
    preferred = model or settings.primary_model
    try:
        available = await primary_models()
    except Exception:
        available = []
    if preferred and (not available or preferred in available): chain.append(preferred)
    chain += [m for m in available if m not in chain][:4]
    chain_errors: list[str] = []
    for candidate in chain:
        try:
            return await primary_chat(messages, candidate), "primary", candidate
        except Exception as exc:
            chain_errors.append(f"{candidate}: {exc}")
            text = str(exc)
            if 'HTTP 429' not in text and 'HTTP 50' not in text: break
    detail = f" ({'; '.join(chain_errors)})" if chain_errors else ""
    raise ProviderError(f"Phoenix AI failed{detail}")

async def chat(messages, provider="auto", model=None):
    provider = (provider or "auto").lower()
    if provider == "primary":
        selected = await _validated_primary_model(model); return await primary_chat(messages, selected), "primary", selected
    if provider in _USER_CHAT:
        key = _user_keys().get(provider)
        if not key: raise ProviderError(f"{provider} key is not saved. Add it in Settings to use {provider}.")
        return await _USER_CHAT[provider](messages, model, key), provider, model or ""
    if provider != "auto": raise ProviderError("Unsupported provider. Choose primary, gemini, openai, anthropic, or auto.")
    # Auto: Phoenix AI only (model chain, then retries), then user-saved providers in order.
    try:
        return await _primary_chain(messages, model)
    except Exception as primary_error:
        saved = _user_keys()
        fallback_errors: list[str] = []
        for pid in FALLBACK_ORDER:
            if pid not in saved: continue
            try:
                return await _USER_CHAT[pid](messages, None, saved[pid]), pid, ""
            except Exception as exc:
                fallback_errors.append(f"{pid}: {exc}")
        if not fallback_errors:
            raise ProviderError(f"{primary_error}") from primary_error
        raise ProviderError(f"Phoenix AI failed: {primary_error}; saved providers failed: {'; '.join(fallback_errors)}") from primary_error
