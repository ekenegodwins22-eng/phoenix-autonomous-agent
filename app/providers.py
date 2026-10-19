"""Provider clients for Phoenix AI Router and optional Google Gemini."""
from collections.abc import AsyncIterator
import asyncio
import json
import logging
import time
import httpx
from app.config import settings

class ProviderError(RuntimeError):
    pass


log = logging.getLogger(__name__)
TRANSIENT_CHAT_STATUS_CODES = {502, 503, 504}
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
                response = await client.get(_primary_url("available-models"), headers=_primary_headers())
            _raise_upstream(response, "/available-models")
            models = _extract_model_ids(response.json())
            if not models:
                raise ProviderError("Phoenix AI /available-models returned no recognizable model IDs")
            _model_cache = (cache_key, time.monotonic(), models)
            return models
        except ProviderError as exc:
            last_error = exc
            retryable = 'HTTP 502' in str(exc) or 'HTTP 503' in str(exc) or 'HTTP 504' in str(exc)
        except (httpx.HTTPError, ValueError) as exc:
            last_error = ProviderError(f"Phoenix AI model discovery failed: {type(exc).__name__}")
            retryable = True
        if not retryable or attempt + 1 >= attempts:
            raise last_error from last_error
        delay = min(settings.primary_model_discovery_retry_base_seconds * (2 ** attempt), settings.primary_chat_retry_max_seconds)
        log.warning("Phoenix AI /available-models failed (%s); retrying in %.2fs (%d/%d)", last_error, delay, attempt + 1, attempts - 1)
        await asyncio.sleep(delay)
    raise last_error

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
            delay = min(settings.primary_chat_retry_base_seconds * (2 ** attempt), settings.primary_chat_retry_max_seconds)
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

async def gemini_chat(messages, model=None):
    if not settings.gemini_api_key: raise ProviderError("GEMINI_API_KEY is not configured")
    chosen_model = model or settings.gemini_model
    try:
        async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
            response = await client.post(f"https://generativelanguage.googleapis.com/v1beta/models/{chosen_model}:generateContent", params={"key": settings.gemini_api_key}, json={"contents": [{"role": "user", "parts": [{"text": _prompt(messages)}]}], "generationConfig": {"temperature": 0.2}})
        _raise_upstream(response, "Gemini")
        data = response.json(); return data["candidates"][0]["content"]["parts"][0]["text"]
    except ProviderError: raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"Gemini response did not contain generated text: {type(exc).__name__}") from exc

async def available_models():
    result = {"providers": [], "default_provider": "primary", "default_model": settings.primary_model}
    try:
        models = await primary_models(); result["providers"].append({"id":"primary","label":"Phoenix AI Router","available":True,"models":[{"id":m,"label":m} for m in models]})
    except Exception as exc:
        result["providers"].append({"id":"primary","label":"Phoenix AI Router","available":False,"error":str(exc)[:180],"models":[]})
    gemini = await gemini_models(); result["providers"].append({"id":"gemini","label":"Google Gemini","available":bool(settings.gemini_api_key),"error":None if settings.gemini_api_key else "GEMINI_API_KEY is not configured","models":[{"id":m,"label":m} for m in gemini]})
    result["providers"].append({"id":"auto","label":"Automatic fallback","available":True,"models":[{"id":"","label":"Use configured defaults"}]})
    return result

async def chat(messages, provider="auto", model=None):
    provider = (provider or "auto").lower()
    if provider == "primary":
        selected = await _validated_primary_model(model); return await primary_chat(messages, selected), "primary", selected
    if provider == "gemini": return await gemini_chat(messages, model), "gemini", model or settings.gemini_model
    if provider != "auto": raise ProviderError("Unsupported provider. Choose primary, gemini, or auto.")
    try:
        selected = await _validated_primary_model(model); return await primary_chat(messages, selected), "primary", selected
    except Exception as primary_error:
        if not settings.gemini_api_key: raise ProviderError(f"Phoenix AI failed; Gemini is not configured: {primary_error}") from primary_error
        try: return await gemini_chat(messages), "gemini", settings.gemini_model
        except Exception as fallback_error: raise ProviderError(f"Both providers failed. Phoenix AI={primary_error}; Gemini={fallback_error}") from fallback_error
