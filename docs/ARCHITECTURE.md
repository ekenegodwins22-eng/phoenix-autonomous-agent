# Architecture

- FastAPI REST API with email/password registration and sign-in. Passwords use scrypt; tokens are HMAC-signed and expire.
- Primary LLM: Phoenix AI Router custom API. Models are discovered live through `GET /available-models`; prompts are sent to `POST /chat` using `{model, message}` and `X-API-Key`.
- Fallback LLM: Google Gemini `generateContent` when primary fails.
- Agent loop: each LLM turn chooses one structured browser or mailbox action; the next turn receives the observed page state and prior action results.
- Recovery: navigation and common controls use one bounded retry, while ordinary JavaScript dialogs are dismissed to prevent deadlocks.
- Human handoff: visible CAPTCHA/human-verification indicators raise a controlled handoff, set task status to `waiting_human`, and expose `POST /tasks/{id}/resume`. No automated CAPTCHA solving or access-control bypass is implemented.
- Browser resource policy: one asyncio lock around a browser session; one task execution lock; cleanup closes page, context, browser, then Playwright in `finally` after completion or after a human-verification resume cycle. While waiting, the authenticated task owner can inspect a screenshot and issue limited browser actions against the retained page.
- Mailbox bridge: small HTTP client for the TempNest/Gmail alias API.
- Persistence: SQLite for a minimal no-extra-service setup. A persistent disk or managed database is needed for stable production accounts on hosts with ephemeral filesystems.


## Durable task worker

- `app/task_store.py` persists task records and append-only task logs in SQLite. Each task has an ID, collision-resistant slug, owner, status, progress, result/error, recovery count, cancellation flag, and worker claim token.
- The web service owns a single bounded worker slot through `execution_lock`; transactional task claims prevent duplicate execution in the process.
- The frontend receives a task immediately and may disconnect. The dedicated `/tasks/{slug}/session` shell reconnects through authenticated task APIs and does not own task lifetime.
- On startup, safely queued tasks are dispatched. Tasks found in `running`, `recovering`, or `waiting_human` state are marked failed with a recovery log instead of replayed, preventing duplicate non-idempotent browser actions after an uncertain restart.
- Cancellation is persisted and also signals/cancels the active worker so browser cleanup runs through the existing `finally` lifecycle. Retry is explicit and bounded by `MAX_TASK_RECOVERY_ATTEMPTS`.
- SQLite persistence survives normal process restarts only when `/app/data` is persistent. Render's ephemeral filesystem still requires a persistent disk or external database for durable production history.
- Each task also has a durable message stream. The session page's bottom composer sends user corrections through `POST /tasks/{id}/messages`; the worker claims unread messages before its next model decision and appends them to the task context. Stop-like messages trigger server-side cancellation, and `resume` can release a human-verification pause. Message content is displayed in the authenticated task session and is not written into generic provider logs.
