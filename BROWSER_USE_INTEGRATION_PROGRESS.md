# Browser Use Integration Progress

## Goal

Use `browser-use/browser-use` as the prototype for improving the live Phoenix Autonomous Agent, including its browser controls, configuration, and execution capabilities. Keep Phoenix's original purpose and user workflow intact; do not replace Phoenix with a standalone Browser Use install or overwrite its app wholesale.

Phoenix's documented product contract is a persistent, authenticated autonomous browser agent with Phoenix AI as the primary model, selectable Gemini/fallback support, mailbox integration, durable task sessions, one active browser session at a time, verified progress, human-verification pauses, and safe recovery. Preserve those capabilities while adding applicable Browser Use strengths.

## Source Of Truth

- Phoenix behavior: `AGENT.md`; shared and specialized procedures: `skills/SKILL.md` and `skills/*/SKILL.md`.
- Phoenix product purpose and deployment: `README.md`.
- Browser Use prototype: sibling checkout `../browser-use/` (upstream `main`, shallow snapshot at `aa093a8`).

## Progress

- Read Phoenix `AGENT.md`, shared skill, browser skill, recovery skill, and README.
- Retrieved Phoenix `main` at `d8edaad` into this workspace because direct authenticated `git clone` is unavailable here; GitHub API access is available for reads and commits.
- Published this integration assessment and guardrails to `main` as commit `f846e592e297cb8b4381353d2ff49ce85786192d`.
- Cloned the public Browser Use prototype. No Browser Use implementation has been copied into Phoenix yet.
- Mapped Phoenix's runner/provider/browser/task flow and the live human-verification UI. Phoenix currently owns its action protocol, model routing, serialized Playwright session, mailbox tools, task checkpoints, user messages, and browser handoff.
- Confirmed Browser Use's `Agent` accepts a custom `BaseChatModel`, custom tools, and a `BrowserSession`; its session is a CDP/event-driven lifecycle, not a drop-in Playwright page. Its full package also brings a large provider, cloud, and browser-tool dependency set.
- Added a local Pydantic `ActionRequest` schema for all current browser/mailbox actions and validation before execution. Unknown actions and missing or malformed required arguments are retried; valid legacy actions still pass through without adding a Browser Use runtime dependency.
- Focused action-validation tests pass: `PYTHONPATH=. uv run --with-requirements requirements.txt pytest -q tests/test_action_validation.py` (7 passed).
- Full Phoenix test suite passes: `PYTHONPATH=. uv run --with-requirements requirements.txt pytest -q` (66 passed, 3 deprecation warnings).
- Existing baseline remains healthy: `d8edaad` passed CI run `39618700443` and is live on the existing Render service.

## Integration Guardrails

- Preserve Phoenix's Phoenix-AI-first model routing, account/auth boundaries, mailbox bridge, task database and messages, persistent task URLs, checkpoints, bounded retries, human handoff, and Render production deployment.
- Treat Browser Use as a source of browser-agent capabilities and settings, not as permission to discard Phoenix's persistence, task lifecycle, or UI.
- Keep browser sessions serialized according to Phoenix's documented one-session limit unless the existing worker/queue design is deliberately changed and tested.
- Keep secrets, cookies, passwords, OTPs, and verification URLs out of logs, checkpoints, and summaries.
- Prefer small, tested, end-to-end patches. Push each verified patch to the Phoenix repository and record its commit and verification here.

## Next Steps

1. Push the typed-action patch with this progress update after local verification.
2. Verify the new commit in GitHub Actions and the existing Render deployment.
3. Continue assessing Browser Use capabilities for later user-facing slices; verify each production change in CI and on the existing Render service.

## Current Stop Point

- Working copies: `browser-use/` (prototype) and `phoenix-autonomous-agent/` (Phoenix source snapshot).
- Phoenix remote branch: `main`, based at `d8edaad` when this snapshot was fetched.
- Progress checkpoint is pushed as `f846e592e297cb8b4381353d2ff49ce85786192d`; the typed action-validation code and focused tests are local and not yet pushed.
- Next action: push the implementation and updated progress record, then verify CI and production.
