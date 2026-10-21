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
- Cloned the public Browser Use prototype. No Browser Use implementation has been copied into Phoenix yet.
- Mapped Phoenix's runner/provider/browser/task flow and the live human-verification UI. Phoenix currently owns its action protocol, model routing, serialized Playwright session, mailbox tools, task checkpoints, user messages, and browser handoff.
- Confirmed Browser Use's `Agent` accepts a custom `BaseChatModel`, custom tools, and a `BrowserSession`; its session is a CDP/event-driven lifecycle, not a drop-in Playwright page. Its full package also brings a large provider, cloud, and browser-tool dependency set.
- No product-code changes have been made in this integration effort. The integration will proceed in small Phoenix-native slices, starting with typed validation for Phoenix's existing action protocol rather than a wholesale runtime swap or a large initial dependency increase.
- Existing baseline remains healthy: `d8edaad` passed CI run `39618700443` and is live on the existing Render service.

## Integration Guardrails

- Preserve Phoenix's Phoenix-AI-first model routing, account/auth boundaries, mailbox bridge, task database and messages, persistent task URLs, checkpoints, bounded retries, human handoff, and Render production deployment.
- Treat Browser Use as a source of browser-agent capabilities and settings, not as permission to discard Phoenix's persistence, task lifecycle, or UI.
- Keep browser sessions serialized according to Phoenix's documented one-session limit unless the existing worker/queue design is deliberately changed and tested.
- Keep secrets, cookies, passwords, OTPs, and verification URLs out of logs, checkpoints, and summaries.
- Prefer small, tested, end-to-end patches. Push each verified patch to the Phoenix repository and record its commit and verification here.

## Next Steps

1. Publish this assessment checkpoint to Phoenix `main`.
2. Add Pydantic validation for the current action names and their required fields, following Browser Use's typed action-schema approach while keeping Phoenix's existing model, mailbox, browser, and task boundaries.
3. Add focused tests for valid, malformed, and unsupported model actions; then push this code patch and record its commit and verification here.
4. Continue assessing Browser Use capabilities for later user-facing slices; verify each production change in CI and on the existing Render service.

## Current Stop Point

- Working copies: `browser-use/` (prototype) and `phoenix-autonomous-agent/` (Phoenix source snapshot).
- Phoenix remote branch: `main`, based at `d8edaad` when this snapshot was fetched.
- This progress file is not yet pushed. No integration code has been pushed.
- Next action: commit this checkpoint, then implement the typed action-validation slice.
