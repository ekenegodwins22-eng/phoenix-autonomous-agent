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
- Focused action-validation tests passed: 7 tests for typed action validation. Full Phoenix suite passed: 66 tests, 3 deprecation warnings before navigation actions were added.
- Pushed the typed-action implementation as `a99728c855ce0413e494ce3e79575726d1f68a9a`. GitHub diff review caught an omitted `async` keyword in the copied `_fill_form` handler; corrected it in `e025a9308dfcab404b1d46250e79a9eb10334ac5` before rollout.
- GitHub check run `39626645630` passed on the corrected commit; the initial `a99728c` check failed on the copied handler typo and was fixed in `e025a93`.
- Render deployment `dep-dbcdne9srm7s738fcofg` for `e025a93` is live. Browser smoke-check: `/health` returns 200 with database, instructions, and browser available; `/tasks/example/session` loads and correctly requires sign-in.
- Added local Browser Use-style `go_back` and bounded `scroll(down,pages)` actions on Phoenix's existing Playwright page. Scroll results include observed before/after offsets; limits are enforced by the Pydantic schema.
- Navigation-action tests pass: 12 focused tests. Full Phoenix suite passes: 71 tests, 3 existing deprecation warnings.

## Integration Guardrails

- Preserve Phoenix's Phoenix-AI-first model routing, account/auth boundaries, mailbox bridge, task database and messages, persistent task URLs, checkpoints, bounded retries, human handoff, and Render production deployment.
- Treat Browser Use as a source of browser-agent capabilities and settings, not as permission to discard Phoenix's persistence, task lifecycle, or UI.
- Keep browser sessions serialized according to Phoenix's documented one-session limit unless the existing worker/queue design is deliberately changed and tested.
- Keep secrets, cookies, passwords, OTPs, and verification URLs out of logs, checkpoints, and summaries.
- Prefer small, tested, end-to-end patches. Push each verified patch to the Phoenix repository and record its commit and verification here.

## Next Steps

1. Push the navigation-action patch with this updated progress record to Phoenix `main`.
2. Verify its GitHub CI run and Render deployment, then smoke-test the live service.
3. Continue assessing Browser Use capabilities for later user-facing slices; verify each production change in CI and on the existing Render service.

## Current Stop Point

- Working copies: `browser-use/` (prototype) and `phoenix-autonomous-agent/` (Phoenix source snapshot).
- Phoenix remote branch: `main`; this local source snapshot began at `d8edaad` and typed-action code is deployed through `e025a9308dfcab404b1d46250e79a9eb10334ac5`.
- Scroll/back code and its 12 focused tests are local, not yet pushed. This updated handoff is also local.
- Next action: push both local patches, then verify CI and the Render deployment.
