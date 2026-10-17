# Phoenix Autonomous Agent — Skill Reference

This document lists actions present in the current implementation. It is not a promise of unsupported capabilities.

## Task execution
The model receives the user's task, recent action history, and a snapshot of the current page. It returns one JSON action at a time. The runner executes the action, records the result, and asks the model for the next action until completion or the configured step limit.

## Browser actions

| Action | Parameters | Purpose |
| --- | --- | --- |
| `goto` | `url` | Navigate to an HTTP/HTTPS URL. |
| `inspect` | none | Read current page URL, title, visible body text, and control metadata. |
| `click` | `target` | Click a target found by label, placeholder, button/link role, visible text, or `css:` selector. |
| `fill` | `target`, `value` | Fill a form field. Action history redacts the value. |
| `select` | `target`, `value` | Select a dropdown option. |
| `press` | `target`, `key` | Press a key on a target. |
| `wait` | `milliseconds` | Wait for 0–5000 ms. |
| `done` | `summary` | End the task and return a structured result. |

## Mailbox actions

| Action | Parameters | Purpose |
| --- | --- | --- |
| `mailbox_health` | none | Check configured mailbox API health. |
| `mailbox_session` | none | Read session status. |
| `mailbox_restore_session` | `alias_email` | Request session restoration for an alias email. |
| `mailbox_accounts` | none | List mailbox/Gmail accounts. |
| `mailbox_aliases` | none | List aliases. |
| `mailbox_create_alias` | optional address/variation/account fields | Create an alias using supported API options. |
| `mailbox_get_alias` | `alias_id` | Read one alias. |
| `mailbox_update_alias` | `alias_id`, `fields` | Update an alias. |
| `mailbox_delete_alias` | `alias_id` | Delete an alias. |
| `mailbox_messages` | `alias_id` | List messages for an alias. |
| `mailbox_list_messages` | none | List mailbox messages. |
| `mailbox_get_message` | `message_id` | Retrieve one message. |
| `mailbox_find_verification_code` | optional `pattern` | Scan authorized mailbox messages and return only a matched verification code plus minimal metadata. |
| `mailbox_attachment` | `message_id`, `attachment_id` | Retrieve one documented message attachment. |

`mailbox_prepare_email` inspects active aliases and Gmail accounts before reusing or creating an address. It records the alias and Gmail-account identifiers in volatile task context and fills the requested page field without returning the address in model history. Supported strategies include `dots`, `plus`, `googlemail`, and `combined`; dotted Gmail variations are not treated as independent inboxes.

`mailbox_fill_verification_code` and `mailbox_open_verification_link` use the associated alias when available. They fill or navigate directly and redact OTPs and tokenized URLs from task history.

## Authorized random values

| Action | Parameters | Purpose |
| --- | --- | --- |
| `generate_credential` | `name`, `kind`, optional `length`, `prefix` | Generate a username or password in volatile task memory when the user authorized random values. |
| `fill_generated` | `target`, `name` | Fill a browser field from a generated value without exposing the value to task history or model output. |

Generated credentials are task-scoped and are discarded when execution ends. They are not identity verification, and the agent does not fabricate identity documents or claims.

## Recovery and human handoff

- Browser navigation and common controls retry once after a transient failure.
- JavaScript dialogs are dismissed so they do not deadlock the task loop.
- CAPTCHA and human-verification indicators are detected from visible page text and controls.
- Detected challenges are classified as reCAPTCHA, hCaptcha, Cloudflare Turnstile, or generic human verification, with site-accessible alternatives reported when visible.
- A detected challenge pauses the task with status `waiting_human`; the user must complete the challenge through an authorized human interaction and call `POST /tasks/{id}/resume`.
- The agent never solves, bypasses, or claims success for a CAPTCHA automatically. Authenticated screenshot and browser-action endpoints operate on the retained active page; `POST /tasks/{id}/resume` continues the same task coroutine and browser session.

## Provider skills

- **Phoenix AI Router:** discover models from `GET /available-models`; chat with `POST /chat`; use `X-API-Key` when configured. Streaming is available at `POST /chat/stream` where implemented by the provider client.
- **Google Gemini:** optional provider; configure `GEMINI_API_KEY` to use it. Model IDs are configured through `GEMINI_MODEL` and `GEMINI_MODELS`.
- **Automatic provider choice/fallback:** use the application's provider-selection mode and only expect fallback behavior supported by the current provider implementation and configuration.

## Safety and limits

- Ask for required values if the user has not supplied them; do not fabricate credentials, form data, or consent.
- Never claim an action succeeded unless its tool result confirms it.
- Do not attempt CAPTCHA or access-control bypass.
- If a CAPTCHA or human-verification challenge is detected, stop and request human assistance instead of guessing or bypassing it.
- Do not put passwords, tokens, cookies, or other secrets in task summaries.
- The browser session is serialized and cleaned up after a task.
