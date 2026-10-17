# Architecture

- FastAPI REST API with email/password registration and sign-in. Passwords use scrypt; tokens are HMAC-signed and expire.
- Primary LLM: Phoenix AI Router custom API. Models are discovered live through `GET /available-models`; prompts are sent to `POST /chat` using `{model, message}` and `X-API-Key`.
- Fallback LLM: Google Gemini `generateContent` when primary fails.
- Agent loop: each LLM turn chooses one structured browser or mailbox action; the next turn receives the observed page state and prior action results.
- Browser resource policy: one asyncio lock around a browser session; one task execution lock; cleanup closes page, context, browser, then Playwright in `finally` before another task starts.
- Mailbox bridge: small HTTP client for the TempNest/Gmail alias API.
- Persistence: SQLite for a minimal no-extra-service setup. A persistent disk or managed database is needed for stable production accounts on hosts with ephemeral filesystems.
