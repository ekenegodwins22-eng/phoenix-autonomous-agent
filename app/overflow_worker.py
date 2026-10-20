"""Phoenix auxiliary worker for an overflow workspace.

Run this process in an additional Phoenix workspace with the same protected
provider/mailbox/database environment. It does not deploy Phoenix or create
infrastructure; it consumes queued Phoenix tasks from the shared database.
"""
from __future__ import annotations
import asyncio
import logging
import time
from app.config import settings
from app.agent.task_manager import task_manager
from app.main import run_task

log = logging.getLogger("phoenix.overflow_worker")


async def serve() -> None:
    started_idle = time.monotonic()
    while True:
        task_id = task_manager.next_queued_task()
        if task_id:
            started_idle = time.monotonic()
            log.info("Claiming queued Phoenix task %s", task_id)
            await run_task(task_id)
            continue
        if settings.phoenix_worker_idle_exit_seconds and time.monotonic() - started_idle >= settings.phoenix_worker_idle_exit_seconds:
            log.info("No queued Phoenix work; exiting idle overflow worker")
            return
        await asyncio.sleep(max(0.5, settings.phoenix_worker_poll_seconds))


if __name__ == "__main__":
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    asyncio.run(serve())
