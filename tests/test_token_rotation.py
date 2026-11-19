"""Admins rotate an agent token; the management keeps working with the new token."""

import httpx
import pytest

from backend import main
from backend.db import Audit, Instance
from tests.test_config_policy import _login
from tests.test_import_api import api  # noqa: F401  (fixture)


@pytest.fixture
def fake_agent(monkeypatch):
    state = {'token': 't' * 48, 'rotation': True, 'lose_response': False}
    from httpx._client import Client as real_client  # api fixture already patched httpx.Client

    def handler(request):
        if request.headers['Authorization'] != 'Bearer ' + state['token']:
            return httpx.Response(401, json={'detail': 'Agent-Zugriff verweigert.'})
        if request.url.path.endswith('/token'):
            import json

            state['token'] = json.loads(request.content)['token']
            if state['lose_response']:
                raise httpx.ReadTimeout('lost', request=request)
            return httpx.Response(200, json={'ok': True})
        return httpx.Response(200, json={'kind': 'native', 'token_rotation': state['rotation']})

    monkeypatch.setattr(main.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    return state


def stored_token(factory):
    with factory() as db:
        return main.core.cipher.decrypt(db.get(Instance, 1).token_cipher.encode()).decode()


@pytest.mark.parametrize('lose_response', [False, True])
def test_admin_rotates_token(api, fake_agent, lose_response):  # noqa: F811
    client, factory, _ = api
    fake_agent['lose_response'] = lose_response
    response = client.post('/api/instances/1/rotate-token', json={})
    assert response.status_code == 200, response.text
    assert stored_token(factory) == fake_agent['token'] != 't' * 48
    with factory() as db:
        assert db.query(Audit).filter_by(action='instance.token_rotated').count() == 1


def test_operator_cannot_rotate_and_old_agent_requires_update(api, fake_agent):  # noqa: F811
    client, factory, _ = api
    fake_agent['rotation'] = False
    response = client.post('/api/instances/1/rotate-token', json={})
    assert response.status_code == 422 and response.json()['code'] == 'agent_update_required'
    _login(client, 'operator')
    assert client.post('/api/instances/1/rotate-token', json={}).status_code == 403
    assert stored_token(factory) == 't' * 48
