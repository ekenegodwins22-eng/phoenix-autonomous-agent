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
- `MAILBOX_API_COOKIE`: optional session cookie if the mailbox service uses browser-session authentication.
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
For authorized email verification workflows, the agent can call `mailbox_find_verification_code(pattern)`; it scans recent messages and returns only the matched code and minimal message metadata, never the full message body.

When the user explicitly authorizes generated registration values, the agent can call `generate_credential` for a username or password and `fill_generated` to place it in the requested form. Generated values are held only in volatile task memory, are redacted from action history and summaries, and are not presented as verified identity information. The agent does not generate identity documents or eligibility claims.

## Browser lifecycle and resource use

Tasks are queued and run sequentially. Chromium is headless, launches with background services disabled, and the browser session is closed in a `finally` block after completion, failure, or cancellation. When human verification is required, the active page remains open and can be inspected through the authenticated screenshot/browser-action endpoints. `MAX_AGENT_STEPS` caps the action loop. Navigation and common browser controls retry once after transient errors, and ordinary JavaScript dialogs are dismissed. The agent uses semantic labels/placeholders before CSS selectors and redacts form values from action history. CAPTCHA and human-verification indicators are detected; the task pauses with `waiting_human` rather than attempting a bypass. No automated CAPTCHA solver is included.

## Tests

```bash
pip install -r requirements.txt
pytest -q
```

## Deployment caveats

- Free Render services spin down after 15 minutes idle and local files are lost on spin-down/redeploy. Use a persistent storage option for the SQLite database or use managed Postgres before relying on saved accounts.
- The included `render.yaml` is a starting point; set the provider environment variables in the Render dashboard.
- Keep the app behind auth. Do not expose a public service with a default `AUTH_SECRET`.
