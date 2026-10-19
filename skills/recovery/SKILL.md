# Recovery Skill

Extend the shared procedures with task lifecycle and recovery rules.

## Checkpoints

After verified actions, persist a redacted checkpoint containing the task step, action type, safe target metadata, current URL when safe, and provider/model metadata. Never persist credentials, cookies, OTPs, verification URLs, or raw sensitive values.

## Retries

Retry transient provider, network, and browser failures within configured bounds using backoff. Do not blindly repeat uncertain side effects such as submissions or purchases; inspect state first and choose an alternative when possible.

## Resumption

Task IDs, instructions, action history, messages, logs, statuses, and checkpoints are durable records. A page reload must resolve the existing task by ID or slug rather than creating a new task. Follow-up messages requeue terminal tasks through the existing task session.

## Interrupted work

On worker restart, do not replay an uncertain side effect automatically. Record the interruption and preserve enough checkpoint evidence for an explicit retry or continuation. Human-verification pauses retain the browser session and resume the same task.

## Completion and failure

A task is complete only after verified evidence. Exhausted budgets, invalid inputs, provider outages, and missing instruction configuration must be recorded as incomplete or failed, never as success.
