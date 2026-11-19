"""Operators must not introduce directives that execute code on the HAProxy host."""

import pytest

from backend import config_policy, main
from tests.test_import_api import PASSWORD, api  # noqa: F401  (fixture)

ACTIVE = 'global\n    lua-load /etc/haproxy/existing.lua\ndefaults\n    mode http\n'


@pytest.mark.parametrize(
    'line',
    [
        'program evil',
        '  lua-load /tmp/x.lua',
        'lua-load-per-thread /tmp/x.lua',
        'lua-prepend-path /tmp/?.lua',
        'external-check',
        '    option   external-check',
        'insecure-fork-wanted',
        'INSECURE-SETUID-WANTED',
    ],
)
def test_privileged_lines_are_detected(line):
    assert config_policy.privileged_directives('global\n' + line + '\n')


@pytest.mark.parametrize(
    'line',
    [
        '# program evil',
        '    log stdout format raw local0',
        'http-request set-header X-Program 1',
        'server program 10.0.0.1:80',
    ],
)
def test_harmless_lines_and_comments_are_ignored(line):
    assert not config_policy.privileged_directives(line + '\n')


def test_only_newly_added_lines_count():
    assert config_policy.added_privileged_directives(ACTIVE + 'backend x\n', ACTIVE) == []
    added = config_policy.added_privileged_directives(ACTIVE + 'program run\n    command /bin/sh\n', ACTIVE)
    assert added == ['program run']


def _login(client, username):
    client.cookies.clear()
    login = client.post('/api/auth/login', json={'username': username, 'password': PASSWORD})
    assert login.status_code == 200
    client.headers['X-CSRF-Token'] = login.json()['csrf']


@pytest.fixture
def agent_calls(monkeypatch):
    calls = []

    def fake_agent(i, path='', method='GET', body=None, timeout=10):
        calls.append(path)
        if path == '/config':
            return {'config': ACTIVE, 'hash': 'h'}
        return {'valid': True}

    monkeypatch.setattr(main.core, 'agent', fake_agent)
    return calls


def test_operator_cannot_validate_new_program_section(api, agent_calls):  # noqa: F811
    client, _, _ = api
    _login(client, 'operator')
    response = client.post(
        '/api/instances/1/validate', json={'config': ACTIVE + 'program x\n  command id\n', 'base_hash': 'a' * 64}
    )
    assert response.status_code == 403 and 'program x' in response.json()['detail']
    assert '/validate' not in agent_calls


def test_operator_keeps_existing_lua_and_admin_may_add(api, agent_calls):  # noqa: F811
    client, _, _ = api
    _login(client, 'operator')
    ok = client.post('/api/instances/1/validate', json={'config': ACTIVE + 'backend b\n', 'base_hash': 'a' * 64})
    assert ok.status_code == 200
    _login(client, 'admin')
    ok = client.post('/api/instances/1/validate', json={'config': ACTIVE + 'program x\n', 'base_hash': 'a' * 64})
    assert ok.status_code == 200 and agent_calls.count('/validate') == 2
