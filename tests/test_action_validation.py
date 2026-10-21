import asyncio

import pytest
from pydantic import ValidationError


def test_action_request_validates_supported_shape_and_keeps_empty_fill_value():
    from app.agent.action_schema import ActionRequest

    action = ActionRequest.model_validate({'action': 'fill', 'target': 'Email', 'value': ''})
    assert action.model_dump(exclude_unset=True, exclude_none=True) == {
        'action': 'fill',
        'target': 'Email',
        'value': '',
    }


@pytest.mark.parametrize(
    'payload',
    [
        {'action': 'navigate', 'url': 'https://example.test'},
        {'action': 'goto'},
        {'action': 'click'},
        {'action': 'fill', 'target': 'Email'},
        {'action': 'mailbox_update_alias', 'alias_id': 'alias-1', 'fields': []},
    ],
)
def test_action_request_rejects_unsupported_or_incomplete_shapes(payload):
    from app.agent.action_schema import ActionRequest

    with pytest.raises(ValidationError):
        ActionRequest.model_validate(payload)


def test_choose_action_retries_until_a_typed_action_is_returned(monkeypatch):
    from app import runner

    responses = iter(
        [
            '{"action":"click"}',
            '{"action":"CLICK","target":"Continue","unused":"ignored"}',
        ]
    )
    prompts = []

    async def fake_chat(messages, provider, model):
        prompts.append(messages[-1]['content'])
        return next(responses), 'primary', 'test-model'

    monkeypatch.setattr(runner, 'chat', fake_chat)
    monkeypatch.setattr(runner.settings, 'model_action_retries', 1)

    action, provider, model = asyncio.run(
        runner._choose_action('Click Continue', [], {'url': 'https://example.test'}, 1, 'primary', 'test-model')
    )

    assert action == {'action': 'click', 'target': 'Continue'}
    assert provider == 'primary'
    assert model == 'test-model'
    assert len(prompts) == 2
    assert 'previous response was not a supported action or omitted required arguments' in prompts[1]
