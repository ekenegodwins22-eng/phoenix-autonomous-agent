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


def test_action_request_accepts_browser_use_navigation_controls():
    from app.agent.action_schema import ActionRequest

    back = ActionRequest.model_validate({'action': 'go_back'})
    scroll = ActionRequest.model_validate({'action': 'scroll', 'down': False, 'pages': 0.5})
    find_text = ActionRequest.model_validate({'action': 'find_text', 'text': 'Continue below'})

    assert back.action == 'go_back'
    assert scroll.model_dump(exclude_unset=True, exclude_none=True) == {
        'action': 'scroll',
        'down': False,
        'pages': 0.5,
    }
    assert find_text.model_dump(exclude_unset=True, exclude_none=True) == {
        'action': 'find_text',
        'text': 'Continue below',
    }


@pytest.mark.parametrize(
    'payload',
    [
        {'action': 'navigate', 'url': 'https://example.test'},
        {'action': 'goto'},
        {'action': 'click'},
        {'action': 'fill', 'target': 'Email'},
        {'action': 'mailbox_update_alias', 'alias_id': 'alias-1', 'fields': []},
        {'action': 'scroll', 'pages': 0},
        {'action': 'scroll', 'pages': 10.1},
        {'action': 'find_text'},
        {'action': 'find_text', 'text': '  '},
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


def test_scroll_uses_browser_use_page_based_distance():
    from app import runner

    class FakeMouse:
        def __init__(self, page):
            self.page = page
            self.delta_y = None

        async def wheel(self, delta_x, delta_y):
            self.delta_y = delta_y
            self.page.scroll_y += delta_y

    class FakePage:
        def __init__(self):
            self.scroll_y = 800
            self.mouse = FakeMouse(self)

        async def evaluate(self, expression):
            return 800 if expression == 'window.innerHeight' else self.scroll_y

        async def wait_for_timeout(self, milliseconds):
            assert milliseconds == 100

    page = FakePage()
    result = asyncio.run(runner._scroll_page(page, down=False, pages=0.5))

    assert page.mouse.delta_y == -400
    assert result == {
        'direction': 'up',
        'pages': 0.5,
        'pixels': 400,
        'scroll_y_before': 800,
        'scroll_y_after': 400,
        'moved': True,
    }


def test_go_back_reports_navigation_result():
    from app import runner

    class FakePage:
        url = 'https://example.test/current'

        async def go_back(self, wait_until):
            assert wait_until == 'domcontentloaded'
            self.url = 'https://example.test/previous'
            return object()

        async def title(self):
            return 'Previous page'

    result = asyncio.run(runner._go_back(FakePage()))

    assert result == {
        'navigated': True,
        'url': 'https://example.test/previous',
        'title': 'Previous page',
    }


def test_find_text_scrolls_to_visible_match_without_returning_page_text():
    from app import runner

    class FakeMatch:
        def __init__(self, visible):
            self.visible = visible
            self.scrolled = False

        async def is_visible(self):
            return self.visible

        async def scroll_into_view_if_needed(self):
            self.scrolled = True

    class FakeMatches:
        def __init__(self, matches):
            self.items = matches

        async def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

    class FakePage:
        url = 'https://example.test/article'

        def __init__(self):
            self.matches = [FakeMatch(False), FakeMatch(True)]

        def get_by_text(self, text, exact):
            assert text == 'Continue below'
            assert exact is False
            return FakeMatches(self.matches)

    page = FakePage()
    result = asyncio.run(runner._find_text(page, ' Continue below '))

    assert result == {'found': True, 'url': 'https://example.test/article'}
    assert page.matches[0].scrolled is False
    assert page.matches[1].scrolled is True
    assert 'Continue below' not in str(result)


def test_find_text_reports_missing_match():
    from app import runner

    class FakeMatches:
        async def count(self):
            return 0

    class FakePage:
        url = 'https://example.test/article'

        def get_by_text(self, text, exact):
            return FakeMatches()

    result = asyncio.run(runner._find_text(FakePage(), 'Missing heading'))

    assert result == {'found': False, 'url': 'https://example.test/article'}
