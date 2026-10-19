# Phoenix Autonomous Agent

A low-footprint browser agent with your self-hosted Phoenix AI as primary, selectable Google Gemini models, email/password login (no email verification), and a TempNest/Gmail alias API bridge. It runs **one browser session at a time** and always closes the page, context, browser, and Playwright process before the next task starts.

## Deploy recommendation

Use **Render Web Service with the included Dockerfile and `render.yaml`** for the easiest first deployment. The free tier is only for testing: it sleeps after inactivity and its filesystem is ephemeral, so saved SQLite accounts can disappear. For reliable use, attach a persistent disk at `/app/data` on a paid service or move account storage to a managed database. The container entrypoint fixes the mounted data directory ownership, then runs the app as a non-root user.

## Required environment variables

- `PRIMARY_BASE_URL`: Phoenix AI Router base URL (no `/v1`). Defaults to `https://concerned-swordfish-suhailtechlnfo-01fd2de0.koyeb.app`.
- `PRIMARY_MODEL`: optional default model ID. The app fetches live model IDs from `GET /available-models`; chat requests use `POST /chat` with `{ "model": "...", "message": "..." }` and the `X-API-Key` header.
- `GEMINI_MODELS`: comma-separated Gemini model IDs shown in the model picker (defaults to `gemini-2.5-flash,gemini-2.5-pro`).
- `PRIMARY_API_KEY`: required for the protected Phoenix model and chat routes; sent only server-side as `X-API-Key`.
- `AUTH_SECRET`: long random secret for signing access tokens; change it before deployment.

Recommended:
- `GEMINI_API_KEY`: enables fallback when the primary provider fails.
- `GEMINI_MODEL`: defaults to `gemini-2.5-flash`.
- `MAILBOX_API_BASE_URL`: defaults to your TempNest service.
- `MAILBOX_API_TOKEN`: optional bearer token if the mailbox service requires one.
- `MAILBOX_API_COOKIE`: optional fixed session cookie. The live mailbox API issues a `temp_session` cookie automatically, which Phoenix bootstraps and retains in memory when needed.
- `DATABASE_PATH`: SQLite file location. Persist it with a persistent disk or replace the store with managed Postgres for production.

## API quick start

Install Python 3.12+, Docker, then run:

```bash
cp .env.example .env
# Edit .env with your provider URL/model and secrets
docker build -t phoenix-agent .
docker run --rm -p 8000:8000 --env-file .env -v "$PWD/data:/app/data" phoenix-agent
```

Open `/docs`. Register an account with `POST /auth/register` using an email and password (minimum 8 characters). There is **no email verification**; sign in immediately using `POST /auth/login`. Copy the returned access token and use `Authorization: Bearer <token>` for protected endpoints.

After sign-in, use `GET /models` to load model options; the backend retrieves Phoenix model IDs from its authenticated `/available-models` route on each request. The selected Phoenix model is checked against that live catalog before `/chat` or `/chat/stream` is called. The browser UI never receives the Phoenix API key. In the web UI, select Phoenix AI, Gemini, or automatic fallback and then choose a model. Each task stores its selected provider/model and exposes verified progress through `GET /tasks/{id}`.

Create a browser task using `POST /tasks`:

```json
{"instruction":"Open https://example.com and tell me the page title", "provider":"primary", "model":"your-model-id"}
```

Poll `GET /tasks/{id}` until status is `completed`, `failed`, or `waiting_human`; use the `progress` field for the latest verified action. If the status is `waiting_human`, use the authenticated `GET /tasks/{id}/screenshot` and `POST /tasks/{id}/browser-action` endpoints to complete the authorized human step in the retained browser session, then call `POST /tasks/{id}/resume`.

## Mailbox API bridge

Protected endpoints:
- `GET /mailbox/health`
- `GET /mailbox/session`
- `POST /mailbox/session/restore` (body: `{"alias_email":"..."}`)
- `POST /mailbox/session/recovery`
- `GET /mailbox/accounts`
- `GET /mailbox/aliases`
- `POST /mailbox/aliases` (accepts `address_type` (`alias` or `variation`), `variation_type`, `variation_label`, `custom_name`, integer `gmail_account_id`, `use_primary`)
- `GET /mailbox/aliases/{alias_id}/messages`
- `GET /mailbox/messages`
- `GET /mailbox/messages/{message_id}`
- `GET /mailbox/messages/{message_id}/attachments/{attachment_id}`

The browser agent can also call the mailbox health/accounts/aliases/create-alias/message actions while executing a task. It uses the configured TempNest API, not SMTP.
For authorized email verification workflows, the agent can call `mailbox_fill_verification_code(target, pattern, alias_id)`; it scans the associated messages and fills only the target field, never placing the OTP in task history or summaries. It can similarly open a verification link through `mailbox_open_verification_link(alias_id)` with the URL redacted from history.
When a registration needs an email address, `mailbox_prepare_email` first inspects active aliases and Gmail accounts, reuses a suitable active alias when possible, and otherwise creates a supported variation. It associates the alias ID and Gmail account ID with the task context, fills the form directly, and verifies delivery through the corresponding alias messages. Gmail dotted variations are treated as routes to the same underlying inbox, not separate accounts.

When the user explicitly authorizes generated registration values, the agent can call `generate_credential` for a username or password and `fill_generated` to place it in the requested form. Generated values are held only in volatile task memory, are redacted from action history and summaries, and are not presented as verified identity information. The agent does not generate identity documents or eligibility claims.

## Browser lifecycle and resource use

Tasks are queued and run sequentially. The Docker image installs the exact Playwright dependency from `requirements.txt` together with its matching Chromium revision and Linux dependencies, placing the browser in the shared `/ms-playwright` cache for the non-root runtime user. Container startup verifies that the executable exists and is runnable. Chromium is headless, launches with background services disabled, and the browser session is closed in a `finally` block after completion, failure, or cancellation. When human verification is required, the active page remains open and can be inspected through the authenticated screenshot/browser-action endpoints. `MAX_AGENT_STEPS` and `TASK_TIMEOUT_SECONDS` provide bounded execution safeguards; the Render defaults are 60 actions and 900 seconds. If the model returns prose instead of an action object, Phoenix makes bounded corrective attempts and falls back to inspection rather than falsely marking the task complete. Verified action metadata is checkpointed without form values or secrets. Before launch, Phoenix checks disk and memory and performs bounded cleanup only under `/tmp/phoenix`; after a task it removes expired designated temporary artifacts. `GET /health` reports sanitized resource and browser diagnostics, while authenticated `GET /runtime/status` exposes the detailed status. Navigation and common browser controls retry after transient errors, and ordinary JavaScript dialogs are dismissed. The agent uses semantic labels/placeholders before CSS selectors and redacts form values from action history. CAPTCHA and human-verification indicators are detected; the task pauses with `waiting_human` rather than attempting a bypass. No automated CAPTCHA solver is included.

## Tests

```bash
pip install -r requirements.txt
pytest -q
```

## Deployment caveats

- Free Render services spin down after 15 minutes idle and local files are lost on spin-down/redeploy. Use a persistent storage option for the SQLite database or use managed Postgres before relying on saved accounts.
- The included `render.yaml` is a starting point; set the provider environment variables in the Render dashboard.
- Keep the app behind auth. Do not expose a public service with a default `AUTH_SECRET`.


## Durable task sessions

Tasks are persisted in SQLite and dispatched by a single server-side worker slot, so closing or refreshing the frontend does not cancel them. Every task receives a collision-resistant slug and a `session_url`, for example `/tasks/register-service-a8f31c/session`. The dedicated session page reads the authenticated task record and displays status, actual progress, timestamps, result, errors, and durable logs.

Use `GET /tasks/{id}` or `GET /tasks/{slug}` to reconnect, `GET /tasks/{id}/logs` for task history, `POST /tasks/{id}/cancel` for server-side cancellation, and `POST /tasks/{id}/retry` for a bounded explicit retry. Startup recovery re-queues only safely queued tasks. A task already claimed by a worker is marked failed after a process restart instead of being replayed, because a browser form submission or other non-idempotent action may have an uncertain outcome. Duplicate worker claims are rejected transactionally.

The Render blueprint points `DATABASE_PATH` to `/app/data/phoenix.db`. A persistent Render disk or external database is required for task and account survival across redeploys or ephemeral filesystem loss. The single web service provides the worker; task execution does not depend on the frontend connection remaining open.

The dedicated task session page includes a persistent bottom chat composer. Messages are stored in SQLite through `GET /tasks/{id}/messages` and `POST /tasks/{id}/messages`. User corrections are injected into the agent's next decision turn; sending `stop`, `cancel`, or `abort` requests server-side cancellation, while `resume` continues a task paused for human verification. Refreshing or closing the page does not lose the task conversation.


## Phoenix chat resilience

Phoenix `/chat` calls retry only transient upstream failures (`502`, `503`, `504`) and network request errors. The default is three total attempts with exponential delays of `0.5s`, `1s`, and a configurable maximum of `8s`; authentication, validation, model-selection, and other permanent HTTP errors are not retried. Configure `PRIMARY_CHAT_RETRY_ATTEMPTS`, `PRIMARY_CHAT_RETRY_BASE_SECONDS`, and `PRIMARY_CHAT_RETRY_MAX_SECONDS` as needed.

## Smart form filling

For authorized local, staging, or test-form work, the agent supports a `fill_form` action. It inspects the live DOM instead of requiring a screenshot, using labels, placeholders, names, IDs, autocomplete hints, input types, required flags, length/range/pattern metadata, and select options. It generates one coherent synthetic profile per action, fills supported fields, re-inspects naturally when the next model turn sees the changed DOM, and returns detected, filled, skipped, and interaction-error lists. Values use reserved synthetic email domains such as `example.test`; the action never submits the form and never claims server-side validation or identity verification.
