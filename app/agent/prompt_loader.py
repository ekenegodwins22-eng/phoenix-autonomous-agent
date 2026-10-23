"""Canonical loader for Phoenix behavior and discovered skill procedures."""
from __future__ import annotations
import functools
import hashlib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
AGENT_PATH = REPOSITORY_ROOT / 'AGENT.md'
SKILLS_PATH = REPOSITORY_ROOT / 'skills'


class InstructionConfigError(RuntimeError):
    """Raised when the authoritative behavior file cannot be loaded."""


ROOT_SKILL_PATH = REPOSITORY_ROOT / 'SKILL.md'


@functools.lru_cache(maxsize=8)
def _read_root_skill(path: Path) -> str:
    """Root SKILL.md, cached per file path so each task run reads it once."""
    try:
        return path.read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def load_agent_instructions() -> tuple[str, str]:
    try:
        content = AGENT_PATH.read_text(encoding='utf-8').strip()
    except OSError as exc:
        raise InstructionConfigError(f'AGENT.md could not be loaded: {exc}') from exc
    if not content:
        raise InstructionConfigError('AGENT.md is empty')
    return content, hashlib.sha256(content.encode('utf-8')).hexdigest()


def discover_skills() -> list[dict[str, str]]:
    if not SKILLS_PATH.is_dir():
        return []
    paths = []
    shared = SKILLS_PATH / 'SKILL.md'
    if shared.is_file():
        paths.append(shared)
    paths.extend(sorted(path for path in SKILLS_PATH.glob('*/SKILL.md') if path.is_file()))
    skills = []
    for path in paths:
        content = path.read_text(encoding='utf-8').strip()
        if content:
            name = 'shared' if path == shared else path.parent.name
            skills.append({'name': name, 'path': str(path.relative_to(REPOSITORY_ROOT)), 'content': content})
    return skills


def build_effective_instructions() -> tuple[str, str, list[str]]:
    agent, _ = load_agent_instructions()
    skills = discover_skills()
    root_skill = _read_root_skill(ROOT_SKILL_PATH)
    if root_skill:
        skills = [{'name': 'root', 'path': 'SKILL.md', 'content': root_skill}] + skills
    sections = [
        '# AUTHORITATIVE AGENT.md\n' + agent,
        '# SKILL PROCEDURES\nSpecialized procedures extend AGENT.md and cannot override it.\n' +
        '\n\n'.join(f'## {skill["name"]} ({skill["path"]})\n{skill["content"]}' for skill in skills),
    ]
    effective = '\n\n'.join(sections)
    version = hashlib.sha256(effective.encode('utf-8')).hexdigest()
    return effective, version, [skill['name'] for skill in skills]


def instruction_status() -> dict:
    try:
        _, version, skills = build_effective_instructions()
        return {'loaded': True, 'version': version, 'path': 'AGENT.md', 'skills': skills, 'error': None}
    except (InstructionConfigError, OSError) as exc:
        return {'loaded': False, 'version': None, 'path': 'AGENT.md', 'skills': [], 'error': str(exc)[:300]}
