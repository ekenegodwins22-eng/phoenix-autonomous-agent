"""Agent-layer task manager backed by the existing durable task store."""
from __future__ import annotations
from app import task_store


class TaskManager:
    """Stable integration boundary for task persistence and recovery."""

    def create(self, **kwargs):
        return task_store.create_task(**kwargs)

    def load(self, task_ref: str, owner_id: int | None = None):
        return task_store.get_task(task_ref, owner_id=owner_id)

    def checkpoint(self, task_id: str, checkpoint: dict):
        return task_store.save_checkpoint(task_id, checkpoint)

    def recover_interrupted(self) -> list[str]:
        return task_store.recover_after_restart()

    def continue_from_message(self, task_id: str):
        return task_store.requeue_for_message(task_id)

    def __getattr__(self, name):
        """Keep all existing store operations available during the migration."""
        return getattr(task_store, name)


task_manager = TaskManager()
