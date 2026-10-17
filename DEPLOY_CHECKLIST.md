# Deploy checklist

## Before deployment

- [ ] Create an empty GitHub repository named `phoenix-autonomous-agent` under the intended GitHub account.
- [ ] Push the contents of this project to the repository's `main` branch.
- [ ] In Render, create a Web Service from that repository and select Docker.

## Environment variables

Required:

- `PRIMARY_BASE_URL` — the Phoenix AI Router base URL, without `/v1`.
- `PRIMARY_API_KEY` — the Phoenix key used server-side for protected router routes.
- `AUTH_SECRET` — a long, random secret. Do not use the example value.

Recommended:

- `PRIMARY_MODEL` — optional startup default; the live `/available-models` catalog is authoritative.
- `GEMINI_API_KEY` — turns on Gemini fallback if the primary provider errors.
- `GEMINI_MODEL` — defaults to `gemini-2.5-flash`.
- `MAILBOX_API_BASE_URL` — defaults to `https://gmail-alias-api.senseiphoenix.name.ng`.
- `MAILBOX_API_TOKEN` or `MAILBOX_API_COOKIE` — set only if the mailbox API requires that auth method.
- `MAX_AGENT_STEPS` — defaults to `20`.
- `DATABASE_PATH` — defaults to `./data/phoenix.db`; on Render the blueprint uses `/app/data/phoenix.db`.

## Persistence

Render's free web service is for testing: it sleeps after inactivity and its local filesystem is ephemeral. For accounts to survive restarts, use a paid service with a persistent disk mounted at `/app/data`, or migrate the user store to a managed database.

## Smoke test after deploy

1. Open `/health` and confirm `status` is `ok`.
2. Open `/` and register using email + password. No email verification is sent or required.
3. Sign in, then submit a simple task such as `Open https://example.com and tell me the page title`.
4. Check `/mailbox/health` in the UI. If it fails, verify the mailbox API's current auth/session requirements and set the matching environment variable.
5. Confirm `/docs` loads and the `/tasks` endpoints require a bearer token.
6. If a task reaches `waiting_human`, verify the response includes `human_help`, complete only the authorized human step, then call `POST /tasks/{id}/resume` and confirm the task reports a new progress state.


## Model selection update

- The workspace loads `GET /models` after login.
- `PRIMARY_BASE_URL` defaults to the user-provided Koyeb AI host. The model picker discovers IDs live from authenticated `GET /available-models`; selected IDs are validated again before task steps call `POST /chat` or `POST /chat/stream` with `model` and `message`, authenticated using `X-API-Key`.
- Select `Phoenix AI (self-hosted)`, `Google Gemini`, or `Automatic fallback`, then select a model.
- Confirm `/available-models` returns model identifiers and `/chat` returns a text response. Do not replace these custom routes with OpenAI-compatible routes unless the API contract changes.
- Add `GEMINI_API_KEY` in Render if Gemini selection/fallback is needed.

## Runtime maintenance

- The Docker image installs the Playwright version declared in `requirements.txt` and its matching Chromium revision with `playwright install --with-deps chromium`.
- The image uses `/ms-playwright` as a shared browser cache so the non-root `phoenix` user can launch the installed binary.
- Container startup fails fast with a diagnostic if the matching Chromium executable is missing.
- `/health` reports sanitized browser availability, disk, memory, active browser-process count, and a timestamp. Authenticated `GET /runtime/status` returns the full runtime diagnostic.
- The agent checks disk and memory before launching a browser and removes only expired entries under `RUNTIME_TEMP_DIR` after safe task cleanup. It never removes the database, credentials, browser binaries, or files outside that designated directory.
- Set `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` and `RUNTIME_TEMP_DIR=/tmp/phoenix` in Render. A redeploy is required after changing the Dockerfile or runtime module.
