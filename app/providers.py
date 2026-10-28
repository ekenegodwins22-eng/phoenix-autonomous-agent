"""Model provider clients.

Three local engines, all OpenAI-compatible (GET /v1/models, POST /v1/chat/completions):
  ollama    -> OLLAMA_BASE_URL     (falls back to legacy PRIMARY_BASE_URL)
  llamacpp  -> LLAMACPP_BASE_URL
  smollm2   -> SMOLLM2_BASE_URL

Auto chain: ollama -> llama.cpp -> smollm2. An engine that fails discovery is skipped.
Then user-saved keys (gemini, openai, anthropic) and finally agentrouter act as optional fallbacks. Gemini is never
used unless the owner saved a key for it.
"""
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
LOCAL_ENGINES = ("ollama", "llamacpp", "smollm2")
ENGINE_LABELS = {"ollama": "Ollama", "llamacpp": "llama.cpp", "smollm2": "SmolLM2 box"}
_model_cache: dict[str, tuple[str, float, list[str]]] = {}


def _engine_config(engine: str) -> tuple[str, str, str]:
    """Return (base_url, api_key, default_model) for a local engine."""
    if engine == "ollama":
        return settings.ollama_base_url, settings.ollama_api_key, settings.ollama_model
    if engine == "llamacpp":
        return settings.llamacpp_base_url, settings.llamacpp_api_key, settings.llamacpp_model
    if engine == "smollm2":
        return settings.smollm2_base_url, settings.smollm2_api_key, settings.smollm2_model
    raise ProviderError(f"Unsupported local engine: {engine}")


def _normalize_base(base: str) -> str:
    """Return the OpenAI-compatible base ending in /v1 (no trailing slash), or '' if unset."""
    base = (base or "").strip().rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/v1") else f"{base}/v1"


def _engine_url(engine: str, path: str) -> str:
    base = _normalize_base(_engine_config(engine)[0])
    if not base:
        raise ProviderError(f"{ENGINE_LABELS[engine]} base URL is not configured")
    return f"{base}/{path.lstrip('/')}"


def _engine_headers(engine: str) -> dict[str, str]:
    key = _engine_config(engine)[1]
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    return headers


def _raise_upstream(response: httpx.Response, operation: str, model: str | None = None, label: str = "Provider") -> None:
    if response.is_error:
        suffix = f" for model {model}" if model else ""
        raise ProviderError(f"{label} {operation} failed with HTTP {response.status_code}{suffix}")


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


async def engine_models(engine: str) -> list[str]:
    """List models via GET {base}/v1/models. Raises ProviderError if the engine is not usable."""
    label = ENGINE_LABELS[engine]
    base, key, default_model = _engine_config(engine)
    if not _normalize_base(base):
        raise ProviderError(f"{label} base URL is not configured")
    cache_key = _normalize_base(base)
    cached = _model_cache.get(engine)
    now = time.monotonic()
    if cached and cached[0] == cache_key and now - cached[1] < settings.primary_model_cache_seconds:
        return list(cached[2])
    attempts = max(1, settings.primary_model_discovery_attempts)
    last_error: ProviderError | None = None
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=min(settings.primary_timeout_seconds, 20)) as client:
                response = await client.get(_engine_url(engine, "models"), headers=_engine_headers(engine))
            if response.status_code == 404 and default_model:
                # Engine without a /v1/models route: trust the configured model instead.
                _model_cache[engine] = (cache_key, time.monotonic(), [default_model])
                return [default_model]
            _raise_upstream(response, "/v1/models", label=label)
            models = _extract_model_ids(response.json())
            if not models:
                raise ProviderError(f"{label} /v1/models returned no model IDs")
            _model_cache[engine] = (cache_key, time.monotonic(), models)
            return models
        except ProviderError as exc:
            last_error = exc
            retryable = any(f"HTTP {code}" in str(exc) for code in (502, 503, 504))
        except (httpx.HTTPError, ValueError) as exc:
            last_error = ProviderError(f"{label} model discovery failed: {type(exc).__name__}")
            retryable = True
        if not retryable or attempt + 1 >= attempts:
            break
        delay = min(settings.primary_model_discovery_retry_base_seconds * (2 ** attempt), settings.primary_chat_retry_max_seconds)
        log.warning("%s /v1/models failed (%s); retrying in %.2fs (%d/%d)", label, last_error, delay, attempt + 1, attempts - 1)
        await asyncio.sleep(delay)
    raise last_error or ProviderError(f"{label} model discovery failed")


_CHAT_ROLES = ("system", "user", "assistant")


def _prompt_messages(messages) -> list[dict]:
    """Chat payload llama.cpp/ollama accept: standard roles only, no empty turns."""
    out = []
    for m in messages or []:
        content = str(m.get("content", "") if isinstance(m, dict) else "")
        if not content.strip():
            continue
        role = str(m.get("role", "user")) if isinstance(m, dict) else "user"
        out.append({"role": role if role in _CHAT_ROLES else "user", "content": content})
    if not out:
        raise ProviderError("chat request has no message content to send")
    return out


def _is_context_overflow(response: httpx.Response) -> bool:
    if response.status_code != 400:
        return False
    text = response.text.lower()
    return "exceed" in text and "context" in text


def _shrink_messages(messages: list[dict]) -> list[dict]:
    """Halve the longest message so a context-overflow 400 can be retried once more."""
    shrunk = [dict(m) for m in messages]
    longest = max(range(len(shrunk)), key=lambda i: len(shrunk[i]["content"]))
    content = shrunk[longest]["content"]
    keep = max(len(content) // 2, 500)
    if len(content) > keep:
        shrunk[longest]["content"] = content[:keep] + "\n[truncated to fit the model context]"
    return shrunk


def _extract_chat_text(data) -> str:
    if isinstance(data, str): return data
    if not isinstance(data, dict): raise ProviderError("chat returned an unexpected response type")
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict) and isinstance(message.get("content"), str) and message["content"].strip():
            return message["content"]
        if isinstance(choices[0], dict) and isinstance(choices[0].get("text"), str) and choices[0]["text"].strip():
            return choices[0]["text"]
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
    raise ProviderError("chat response did not contain a recognized text field")


async def _validated_engine_model(engine: str, model: str | None) -> str:
    models = await engine_models(engine)
    default = _engine_config(engine)[2]
    chosen = model or (default if default in models else models[0])
    if chosen not in models:
        raise ProviderError(f"Selected model is unavailable on {ENGINE_LABELS[engine]}: {chosen}")
    return chosen


async def engine_chat(engine: str, messages, model: str | None = None) -> str:
    """POST {base}/v1/chat/completions with retries on 502/503/504 and network errors."""
    label = ENGINE_LABELS[engine]
    chosen_model = await _validated_engine_model(engine, model)
    payload = {"model": chosen_model, "messages": _prompt_messages(messages), "stream": False}
    attempts = max(1, settings.primary_chat_retry_attempts)
    try:
        response = None
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=settings.primary_timeout_seconds) as client:
                    response = await client.post(_engine_url(engine, "chat/completions"), headers=_engine_headers(engine), json=payload)
            except httpx.RequestError as exc:
                if attempt + 1 >= attempts:
                    raise ProviderError(f"{label} chat failed: {type(exc).__name__}") from exc
                delay = min(settings.primary_chat_retry_base_seconds * (2 ** attempt), settings.primary_chat_retry_max_seconds)
                log.warning("%s chat transient network failure (%s); retrying in %.2fs (%d/%d)", label, type(exc).__name__, delay, attempt + 1, attempts - 1)
                await asyncio.sleep(delay)
                continue
            if response.status_code not in TRANSIENT_CHAT_STATUS_CODES or attempt + 1 >= attempts:
                break
            retry_after = response.headers.get("retry-after")
            try: delay = min(float(retry_after), settings.primary_chat_retry_max_seconds) if retry_after else None
            except ValueError: delay = None
            if delay is None: delay = min(settings.primary_chat_retry_base_seconds * (2 ** attempt), settings.primary_chat_retry_max_seconds)
            log.warning("%s chat returned HTTP %s; retrying in %.2fs (%d/%d)", label, response.status_code, delay, attempt + 1, attempts - 1)
            await asyncio.sleep(delay)
        for _ in range(2):
            if not _is_context_overflow(response):
                break
            # Small-context local models (e.g. SmolLM2 n_ctx 2048) reject oversized snapshots with 400.
            payload["messages"] = _shrink_messages(payload["messages"])
            async with httpx.AsyncClient(timeout=settings.primary_timeout_seconds) as client:
                response = await client.post(_engine_url(engine, "chat/completions"), headers=_engine_headers(engine), json=payload)
        _raise_upstream(response, "/v1/chat/completions", chosen_model, label=label)
        return _extract_chat_text(response.json())
    except ProviderError: raise
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError(f"{label} chat failed: {type(exc).__name__}") from exc


async def engine_chat_stream(engine: str, messages, model: str | None = None) -> AsyncIterator[str]:
    """Stream OpenAI-style SSE deltas from {base}/v1/chat/completions (stream=true)."""
    label = ENGINE_LABELS[engine]
    chosen_model = await _validated_engine_model(engine, model)
    payload = {"model": chosen_model, "messages": _prompt_messages(messages), "stream": True}
    try:
        async with httpx.AsyncClient(timeout=settings.primary_timeout_seconds) as client:
            async with client.stream("POST", _engine_url(engine, "chat/completions"), headers=_engine_headers(engine), json=payload) as response:
                _raise_upstream(response, "/v1/chat/completions", chosen_model, label=label)
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or line.startswith(":"): continue
                    if line.startswith("data:"): line = line[5:].strip()
                    if line == "[DONE]": break
                    try: data = json.loads(line)
                    except json.JSONDecodeError: continue
                    choices = data.get("choices") if isinstance(data, dict) else None
                    delta = choices[0].get("delta", {}) if choices and isinstance(choices[0], dict) else {}
                    piece = delta.get("content") if isinstance(delta, dict) else None
                    if isinstance(piece, str) and piece: yield piece
    except ProviderError: raise
    except httpx.HTTPError as exc:
        raise ProviderError(f"{label} streaming chat failed: {type(exc).__name__}") from exc


def _prompt(messages) -> str:
    return "\n\n".join(f'{str(item.get("role", "user")).upper()}:\n{item.get("content", "")}' for item in messages)


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

def _env_key(pid: str) -> str:
    """Operator-level key from env, used only for agentrouter (the other providers are owner-saved)."""
    return settings.agentrouter_api_key if pid == "agentrouter" else ""

def _user_keys() -> dict:
    owner = _owner_ctx.get()
    return _keys_for(owner) if owner is not None else {}

FALLBACK_ORDER = ("gemini", "openai", "anthropic", "agentrouter")  # agentrouter last

async def gemini_chat(messages, model=None, api_key=None):
    key = api_key or settings.gemini_api_key
    if not key: raise ProviderError("Gemini key is not saved. Add it in Settings to use Gemini.")
    chosen_model = model or settings.gemini_model
    try:
        async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
            response = await client.post(f"https://generativelanguage.googleapis.com/v1beta/models/{chosen_model}:generateContent", params={"key": key}, json={"contents": [{"role": "user", "parts": [{"text": _prompt(messages)}]}], "generationConfig": {"temperature": 0.2}})
        _raise_upstream(response, "request", label="Gemini")
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
        _raise_upstream(response, "request", label="OpenAI")
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
        _raise_upstream(response, "request", label="Anthropic")
        return "".join(b.get("text", "") for b in response.json()["content"] if b.get("type") == "text")
    except ProviderError: raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"Anthropic response did not contain generated text: {type(exc).__name__}") from exc

AGENTROUTER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) CherryStudio/1.0.0 Chrome/124.0.0.0 Safari/537.36"

async def agentrouter_chat(messages, model=None, api_key=None):
    """OpenAI-compatible AgentRouter. Its edge gates unknown clients, so send a browser-like UA."""
    key = api_key or settings.agentrouter_api_key
    if not key: raise ProviderError("AgentRouter key is not saved. Add it in Settings to use AgentRouter.")
    base = _normalize_base(settings.agentrouter_base_url) or "https://agentrouter.org/v1"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": AGENTROUTER_USER_AGENT}
    payload = {"model": model or settings.agentrouter_model, "messages": _prompt_messages(messages), "stream": False}
    try:
        async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
            response = await client.post(f"{base}/chat/completions", headers=headers, json=payload)
        _raise_upstream(response, "request", label="AgentRouter")
        return _extract_chat_text(response.json())
    except ProviderError: raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"AgentRouter response did not contain generated text: {type(exc).__name__}") from exc

_USER_CHAT = {"gemini": gemini_chat, "openai": openai_chat, "anthropic": anthropic_chat, "agentrouter": agentrouter_chat}
_USER_LABELS = {"gemini": "Google Gemini", "openai": "OpenAI", "anthropic": "Anthropic", "agentrouter": "AgentRouter"}


async def available_models(owner_id: int | None = None):
    default_provider = "ollama"
    result = {"providers": [], "default_provider": default_provider, "default_model": settings.ollama_model}
    for engine in LOCAL_ENGINES:
        label = ENGINE_LABELS[engine]
        try:
            models = await engine_models(engine)
            result["providers"].append({"id": engine, "label": label, "available": True, "models": [{"id": m, "label": m} for m in models]})
        except Exception as exc:
            result["providers"].append({"id": engine, "label": label, "available": False, "error": str(exc)[:180], "models": []})
    saved = _user_keys() if owner_id is None else _keys_for(owner_id)
    for pid in FALLBACK_ORDER:
        has = pid in saved
        result["providers"].append({"id": pid, "label": _USER_LABELS[pid], "available": has, "error": None if has else "Add your own key in Settings to enable", "models": []})
    result["providers"].append({"id": "auto", "label": "Automatic fallback", "available": True, "models": [{"id": "", "label": "Use configured defaults"}]})
    return result


async def _local_chain(messages, model):
    errors: list[str] = []
    order = list(LOCAL_ENGINES)
    pinned = None
    if model:
        # A picked model stays on the engine that serves it; other engines are only fallbacks.
        for engine in LOCAL_ENGINES:
            try:
                if model in await engine_models(engine):
                    pinned = engine
                    break
            except Exception:
                continue
        if pinned:
            order = [pinned] + [e for e in order if e != pinned]
    for engine in order:
        try:
            models = await engine_models(engine)
        except Exception as exc:
            errors.append(f"{ENGINE_LABELS[engine]}: {exc}")
            continue
        chosen = model if model in models else None
        # The pinned engine gets two attempts in a row before the chain falls through.
        for _attempt in range(2 if engine == pinned else 1):
            try:
                text = await engine_chat(engine, messages, chosen)
                return text, engine, chosen or _engine_config(engine)[2]
            except Exception as exc:
                errors.append(f"{ENGINE_LABELS[engine]}: {exc}")
    return None, errors, None


async def chat(messages, provider="auto", model=None):
    provider = (provider or "auto").lower()
    if provider == "primary":
        # Legacy value from stored tasks/clients: the old Phoenix router is no longer a provider.
        provider = "auto"
    if provider in LOCAL_ENGINES:
        return await engine_chat(provider, messages, model), provider, model or _engine_config(provider)[2]
    if provider in _USER_CHAT:
        key = _user_keys().get(provider) or _env_key(provider)
        if not key: raise ProviderError(f"{provider} key is not saved. Add it in Settings to use {provider}.")
        return await _USER_CHAT[provider](messages, model, key), provider, model or ""
    if provider != "auto":
        raise ProviderError("Unsupported provider. Choose ollama, llamacpp, smollm2, gemini, openai, anthropic, agentrouter, or auto.")
    text, engine_or_errors, used_model = await _local_chain(messages, model)
    if text is not None:
        return text, engine_or_errors, used_model
    local_errors = engine_or_errors
    saved = _user_keys()
    fallback_errors: list[str] = []
    for pid in FALLBACK_ORDER:
        key = saved.get(pid) or _env_key(pid)
        if not key: continue
        try:
            return await _USER_CHAT[pid](messages, None, key), pid, ""
        except Exception as exc:
            fallback_errors.append(f"{pid}: {exc}")
    local_text = "; ".join(local_errors) if local_errors else "no local engine is configured"
    if not fallback_errors:
        raise ProviderError(f"No local model engine answered ({local_text}). Set OLLAMA_BASE_URL, LLAMACPP_BASE_URL or SMOLLM2_BASE_URL, or save your own provider key in Settings.")
    raise ProviderError(f"No local model engine answered ({local_text}); saved keys failed: {'; '.join(fallback_errors)}")
