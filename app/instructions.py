"""Backward-compatible imports for the canonical app.agent prompt loader."""
from app.agent.prompt_loader import (  # noqa: F401
    AGENT_PATH,
    InstructionConfigError,
    build_effective_instructions,
    discover_skills,
    instruction_status,
    load_agent_instructions,
)
