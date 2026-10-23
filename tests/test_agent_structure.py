import asyncio


def test_prompt_loader_discovers_shared_and_specialized_skills():
    from app.agent.prompt_loader import build_effective_instructions, discover_skills
    skills = discover_skills()
    names = [skill['name'] for skill in skills]
    assert names == ['root', 'shared', 'browser', 'recovery']
    effective, version, discovered = build_effective_instructions()
    assert discovered == names
    assert len(version) == 64
    assert '# AUTHORITATIVE AGENT.md' in effective
    assert '## root (SKILL.md)' in effective
    assert 'Browser Operations Skill' in effective
    assert 'Recovery Skill' in effective


def test_agent_instructions_precede_skill_procedures():
    from app.agent.prompt_loader import build_effective_instructions
    effective, _, _ = build_effective_instructions()
    assert effective.index('# AUTHORITATIVE AGENT.md') < effective.index('# SKILL PROCEDURES')
    assert '`AGENT.md` remains the authoritative behavior source' in effective


def test_execution_loop_facade_uses_existing_runner(monkeypatch):
    from app.agent import execution_loop
    seen = {}
    async def fake_execute(*args, **kwargs):
        seen['called'] = True
        return 'verified'
    monkeypatch.setattr(execution_loop, 'execute_task', fake_execute)
    assert asyncio.run(execution_loop.run_task('Inspect')) == 'verified'
    assert seen['called'] is True


def test_task_manager_preserves_checkpoint_and_recovery(monkeypatch, tmp_path):
    from app import config
    from app.agent.task_manager import TaskManager
    from app import task_store
    monkeypatch.setattr(config.settings, 'database_path', str(tmp_path / 'agent-manager.db'))
    manager = TaskManager()
    task = manager.create(owner_id=9, instruction='Inspect', start_url=None, timeout_seconds=30, provider='auto', model=None)
    manager.checkpoint(task['id'], {'step': 1, 'action': 'inspect', 'url': 'https://example.test'})
    assert manager.load(task['id'])['checkpoint']
    assert task['id'] in manager.recover_interrupted()
    assert task_store.get_task(task['id'])['status'] == 'queued'
