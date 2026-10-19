"""Canonical execution-loop facade.

The mature browser loop remains in ``app.runner`` to preserve the existing
architecture. This module is the stable agent-layer entry point used by the
application and tests, so future loop changes have one integration boundary.
"""
from app.runner import (
    HumanInterventionRequired,
    TaskBlocked,
    TaskIncomplete,
    execute_task,
)


async def run_task(*args, **kwargs):
    """Execute one task through the existing durable browser loop."""
    return await execute_task(*args, **kwargs)


__all__ = [
    'HumanInterventionRequired',
    'TaskBlocked',
    'TaskIncomplete',
    'execute_task',
    'run_task',
]
