import pytest


def test_registration_observation_distinguishes_verification_and_success():
    from app.runner import _registration_observation
    base = {'url': 'https://site.test/register', 'title': 'Create account', 'controls': []}
    pending = {**base, 'body': 'Check your inbox to verify your email'}
    success = {**base, 'url': 'https://site.test/dashboard', 'title': 'Welcome', 'body': 'Account created successfully. Welcome to your dashboard.'}
    assert _registration_observation('register a test account', pending)['state'] == 'email_verification_required'
    assert _registration_observation('register a test account', success)['state'] == 'registration_succeeded'


def test_registration_observation_does_not_claim_success_from_form_presence():
    from app.runner import _registration_observation
    state = {'url': 'https://site.test/register', 'title': 'Register', 'body': 'Create an account', 'controls': [{'type': 'email'}, {'type': 'password'}]}
    assert _registration_observation('sign up', state)['state'] == 'registration_form_displayed'


def test_proxy_validation_rejects_embedded_credentials_and_unsupported_schemes():
    from app.proxy import validate_proxy_server
    with pytest.raises(ValueError):
        validate_proxy_server('ftp://proxy.example:21')
    with pytest.raises(ValueError):
        validate_proxy_server('http://user:secret@proxy.example:8080')
    assert validate_proxy_server('socks5://proxy.example:1080') == 'socks5://proxy.example:1080'


def test_task_request_proxy_mode_is_explicit():
    from app.models import TaskRequest
    assert TaskRequest(instruction='open example.com').proxy_mode == 'direct'
    assert TaskRequest(instruction='open example.com', proxy_mode='configured').proxy_mode == 'configured'
