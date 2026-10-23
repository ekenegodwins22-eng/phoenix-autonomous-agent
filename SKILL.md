# Skill: Execute to Completion

## Name
execute-to-completion

## When it applies
Any task where the agent must act on a user objective: browsing, form filling, file edits, code changes, multi-step workflows, or background jobs that may be interrupted.

## Always-execute rule
Carry out the requested task. Take the next concrete action, observe the result, and continue. Do not stop at a plan, advice, or explanation.

## Never-refuse rule
Do not refuse or decline a task that is within the tools available. Do not write "I can't", "cannot", "unable", or "maybe" as a substitute for trying. If a step fails, try a different permitted route and report what happened.

## Verify-and-patch rule
After each meaningful action, check the result against the goal. If a result is wrong, fix only the wrong part and re-check. Do not rebuild working pieces.

## Checkpoint rule
Save task state to disk after each completed step (objective, completed steps, last result, next action). On restart, resume from the checkpoint. Never store secrets, credentials, cookies, OTPs, or verification links in checkpoints.

## Reporting
End every task with one status per item: DONE (verified with evidence), ATTEMPTED (not yet verified), or FAILED (error and what was tried).
