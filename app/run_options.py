"""Per-run options (owner settings + task flags) carried through the async call chain.

The worker sets this ContextVar before it spawns the execution task, so the runner
and the browser layer read the same owner-saved settings without changing existing
function signatures.
"""
from __future__ import annotations

from contextvars import ContextVar

DEFAULT_RUN_OPTIONS: dict = {
    'fast_mode': False,
    'deep_research': False,
    'max_steps': None,
    'system_prompt': '',
    'browser': {},
}

run_options: ContextVar[dict] = ContextVar('phoenix_run_options', default={})


def current() -> dict:
    merged = dict(DEFAULT_RUN_OPTIONS)
    merged.update(run_options.get() or {})
    return merged
