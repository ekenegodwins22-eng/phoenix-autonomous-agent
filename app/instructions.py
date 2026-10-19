"""Runtime loader for the repository-controlled Phoenix behavior file."""
from __future__ import annotations
import hashlib
from pathlib import Path

AGENT_PATH = Path(__file__).resolve().parent.parent / 'AGENT.md'


class InstructionConfigError(RuntimeError):
    """Raised when the authoritative agent behavior file is unavailable."""


def load_agent_instructions() -> tuple[str, str]:
    try:
        content = AGENT_PATH.read_text(encoding='utf-8').strip()
    except OSError as exc:
        raise InstructionConfigError(f'AGENT.md could not be loaded: {exc}') from exc
    if not content:
        raise InstructionConfigError('AGENT.md is empty')
    digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
    return content, digest


def instruction_status() -> dict:
    try:
        _, digest = load_agent_instructions()
        return {'loaded': True, 'version': digest, 'path': 'AGENT.md', 'error': None}
    except InstructionConfigError as exc:
        return {'loaded': False, 'version': None, 'path': 'AGENT.md', 'error': str(exc)[:300]}
