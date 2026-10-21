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
- Cloned the public Browser Use prototype. Phoenix is adapting selected capabilities rather than vendoring the full package or replacing its runtime.
- Mapped Phoenix's runner/provider/browser/task flow and the live human-verification UI. Phoenix currently owns its action protocol, model routing, serialized Playwright session, mailbox tools, task checkpoints, user messages, and browser handoff.
- Confirmed Browser Use's `Agent` accepts a custom `BaseChatModel`, custom tools, and a `BrowserSession`; its session is a CDP/event-driven lifecycle, not a drop-in Playwright page. Its full package also brings a large provider, cloud, and browser-tool dependency set.
- Added a Pydantic `ActionRequest` schema for Phoenix's browser/mailbox actions and validation before execution. Unknown actions and missing or malformed required arguments are retried; valid legacy actions still pass through without adding a Browser Use runtime dependency.
- Focused action-validation tests pass: `PYTHONPATH=. uv run --with-requirements requirements.txt pytest -q tests/test_action_validation.py` (7 passed).
- Full Phoenix test suite passes: `PYTHONPATH=. uv run --with-requirements requirements.txt pytest -q` (66 passed, 3 deprecation warnings).
- Pushed the typed-action implementation as `a99728c855ce0413e494ce3e79575726d1f68a9a`. GitHub diff review caught an omitted `async` keyword in the copied `_fill_form` handler; corrected it in `e025a9308dfcab404b1d46250e79a9eb10334ac5` before rollout.
- GitHub check run `39626645630` passed on the corrected commit; the initial `a99728c` check failed on the copied handler typo and was fixed in `e025a93`.
- Render deployment `dep-dbcdne9srm7s738fcofg` for `e025a93` completed successfully and was later superseded by the navigation rollout. Browser smoke-check at that stage: `/health` returned 200 with database, instructions, and browser available; `/tasks/example/session` loaded and correctly required sign-in.
- Added local Browser Use-style `go_back` and bounded `scroll(down,pages)` actions on Phoenix's existing Playwright page. Scroll results include observed before/after offsets; limits are enforced by the Pydantic schema.
- Navigation-action tests pass: 12 focused tests. Full Phoenix suite passes: 71 tests, 3 existing deprecation warnings.
- Pushed the navigation actions as `e464c364faf97be8736d445f1666ec270fbb2a77` (`feat: add browser navigation actions`). The reviewed diff only adds the schema fields, navigation helpers/dispatch, tests, and this progress handoff; the previously fixed async form handler remains unchanged.
- GitHub Actions run `39628669417` and check run `119320637236` passed for `e464c364`.
- Render deployment `dep-dbcdv4ei0phs738592q0` for `e464c364` completed successfully and was later superseded by the `find_text` rollout. `/health` returned 200 with status `ok`, PostgreSQL, loaded agent instructions, and browser availability; `/tasks/example/session` returned 200 and displayed the expected sign-in gate.
- Browser-based visual smoke testing was blocked by this run's proxy returning `ERR_SOCKS_CONNECTION_FAILED` for both Phoenix and `example.com`, even after reload and a fresh tab. Direct HTTP checks and Render health logs succeeded, so this was isolated to the browser proxy rather than the app.
- Added a Browser Use-style `find_text(text)` action that scrolls the first visible match into view without adding the matched page content to task history.
- Updated `docs/SKILLS.md` with the supported `go_back`, bounded `scroll`, and `find_text` browser actions.
- Local tests pass for the new action: 16 focused action-validation tests and 75 tests in the full Phoenix suite, with 3 existing deprecation warnings.
- GitHub Actions run `39632401161` and check run `119333838794` passed for `710e27efb948c8e4cc95808d6f3cc3404ba790e3`.
- Render deployment `dep-dbce8a0n74is7388v7r0` for `710e27e` is live. Direct checks after rollout: `/health` returned 200 with status `ok`, PostgreSQL, loaded instructions, and browser availability; `/tasks/example/session` returned 200.

## Integration Guardrails

- Preserve Phoenix's Phoenix-AI-first model routing, account/auth boundaries, mailbox bridge, task database and messages, persistent task URLs, checkpoints, bounded retries, human handoff, and Render production deployment.
- Treat Browser Use as a source of browser-agent capabilities and settings, not as permission to discard Phoenix's persistence, task lifecycle, or UI.
- Keep browser sessions serialized according to Phoenix's documented one-session limit unless the existing worker/queue design is deliberately changed and tested.
- Keep secrets, cookies, passwords, OTPs, and verification URLs out of logs, checkpoints, and summaries.
- Prefer small, tested, end-to-end patches. Push each verified patch to the Phoenix repository and record its commit and verification here.

## Next Steps

1. Assess tab control as a larger next slice: design stable tab identifiers and keep Phoenix's active page pointer synchronized with screenshot, stream, and human-resume routes while retaining the one-session lock.
2. Verify any later patch in GitHub Actions and on the existing Render service; preserve Phoenix's model routing, persistence, mailbox, auth, and human handoff.

## Current Stop Point

- Working copies: `browser-use/` (prototype) and `phoenix-autonomous-agent/` (Phoenix source snapshot).
- Phoenix `main` includes navigation commit `e464c364faf97be8736d445f1666ec270fbb2a77` and find-text commit `710e27efb948c8e4cc95808d6f3cc3404ba790e3`; the local source snapshot began at `d8edaad`.
- Typed action validation, `go_back`, bounded `scroll`, and `find_text(text)` are deployed. Check runs `119320637236` and `119333838794` passed; Render deploy `dep-dbce8a0n74is7388v7r0` for `710e27e` is live.
- The `find_text` patch also updates `docs/SKILLS.md`. Its helper returns only the found flag and current URL, not matched page text.
- Direct route checks succeeded. Browser-based visual confirmation remains unverified because the Browser Use Cloud browser proxy failed across multiple sites; retry only if that visual check becomes necessary.
- Next action: design safe tab identifiers and synchronization with Phoenix's human-handoff page reference before implementing multi-tab actions.
