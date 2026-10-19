# Phoenix AI Router Findings

Source: `https://concerned-swordfish-suhailtechlnfo-01fd2de0.koyeb.app`

Verified 2026-10-19:

- `GET /health` returned HTTP 200 with `{"status":"ok","db":"connected","ai":"ready"}`.
- `GET /openapi.json` returned HTTP 200.
- `POST /chat` requires JSON `{ "model": string, "message": string }` and the `X-API-Key` header.
- `GET /available-models` returned HTTP 200 and advertised: `claude-opus-5-5`, `deepseek-v4-flash`, `gpt-6-astra`, and `gpt-6.1-sol`.
- A minimal `POST /chat` request using `deepseek-v4-flash` returned HTTP 200 with `{"model":"deepseek-v4-flash","response":"OK"}`.
- Minimal requests using `claude-opus-5-5`, `gpt-6-astra`, and `gpt-6.1-sol` returned HTTP 502 with the sanitized body `error code: 502`.

Conclusion: the previous Render E2E failure was model-specific upstream behavior, not a missing API key, invalid request schema, database error, or browser error. Phoenix should default to the verified working `deepseek-v4-flash`, while preserving explicit model selection and reporting the selected model in sanitized failures.
