"""Pending changes list, single undo and the apply preview with diff."""

from backend import changes
from tests.test_import_api import api, apply_generated, sha, sync_api  # noqa: F401  (fixtures)

HOST = {
    'id': 'h1',
    'domain': 'app.example.com',
    'path': '/',
    'servers': [{'address': '10.0.0.5', 'port': 8080}],
}


def test_summary_lists_added_changed_removed_and_settings():
    applied = {'hosts': [HOST, {**HOST, 'id': 'h2', 'domain': 'old.example.com'}], 'http_port': 80}
    draft = {
        'hosts': [
            {**HOST, 'servers': [{'address': '10.0.0.6', 'port': 8080}]},
            {**HOST, 'id': 'h3', 'domain': 'new.example.com'},
        ],
        'http_port': 8080,
        'removed_backends': ['legacy'],
    }
    got = {(c['kind'], c['key'], c['action']) for c in changes.summarize(draft, applied)}
    assert got == {
        ('hosts', 'h1', 'changed'),
        ('hosts', 'h3', 'added'),
        ('hosts', 'h2', 'removed'),
        ('removed_backends', 'legacy', 'removed'),
        ('settings', 'settings', 'changed'),
    }


def test_revert_restores_single_entries_in_original_order():
    a, b, c = (dict(HOST, id=x, domain=x + '.example.com') for x in 'abc')
    applied = {'hosts': [a, b, c], 'http_port': 80}
    draft = {'hosts': [a, c], 'http_port': 81}
    restored = changes.revert(draft, applied, 'hosts', 'b')
    assert [h['id'] for h in restored['hosts']] == ['a', 'b', 'c'] and restored['http_port'] == 81
    assert changes.revert(restored, applied, 'settings', 'settings')['http_port'] == 80
    added = {'hosts': [a, b, c, dict(HOST, id='d')]}
    assert [h['id'] for h in changes.revert(added, applied, 'hosts', 'd')['hosts']] == ['a', 'b', 'c']


def test_line_diff_counts_and_numbers_lines():
    diff = changes.line_diff('a\nb\nc\n', 'a\nB\nc\nd\n')
    assert (diff['added'], diff['removed']) == (2, 1)
    kinds = [(line['type'], line['text']) for line in diff['hunks'][0]['lines']]
    assert ('removed', 'b') in kinds and ('added', 'B') in kinds and ('added', 'd') in kinds
    assert changes.line_diff('same\n', 'same\n')['hunks'] == []


def test_import_sets_baseline_and_changes_can_be_undone(sync_api):  # noqa: F811
    c, factory, state = sync_api
    first = c.get('/api/instances/1/changes').json()
    assert first['baseline'] and first['count'] == 0
    doc = c.get('/api/instances/1/document').json()
    route = doc['imported_routes'][0]
    doc['imported_routes'][0] = {**route, 'aliases': ['www.' + route['domain']]}
    saved = c.put('/api/instances/1/document', json=doc)
    assert saved.status_code == 200, saved.text
    pending = c.get('/api/instances/1/changes').json()
    assert pending['count'] == 1 and pending['changes'][0]['action'] == 'changed'
    reverted = c.post(
        '/api/instances/1/changes/revert',
        json={'kind': 'imported_routes', 'key': route['id'], 'version': pending['version']},
    )
    assert reverted.status_code == 200, reverted.text
    assert c.get('/api/instances/1/changes').json()['count'] == 0
    stale = c.post('/api/instances/1/changes/revert', json={'kind': 'all', 'key': '', 'version': 0})
    assert stale.status_code == 409


def test_apply_preview_shows_diff_validation_and_apply_resets_pending(sync_api):  # noqa: F811
    c, _, state = sync_api
    doc = c.get('/api/instances/1/document').json()
    backend = doc['imported_backends'][0]
    backend['servers'][0]['port'] = 9191
    assert c.put('/api/instances/1/document', json=doc).status_code == 200
    preview = c.post('/api/instances/1/apply-preview', json={})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body['valid'] and body['source'] == 'draft' and body['diff']['added'] >= 1
    assert any('9191' in line['text'] for h in body['diff']['hunks'] for line in h['lines'] if line['type'] == 'added')
    assert [ch['kind'] for ch in body['changes']] == ['imported_backends']
    result, _, _ = apply_generated(c)
    assert result.status_code == 200, result.text
    assert c.get('/api/instances/1/changes').json()['count'] == 0


def test_text_preview_uses_given_config_and_reports_invalid(sync_api, monkeypatch):  # noqa: F811
    from fastapi import HTTPException

    from backend import main

    c, _, state = sync_api
    remote = main.core.agent

    def failing(i, path='', method='GET', body=None, timeout=10):
        if path == '/validate':
            raise HTTPException(422, '[ALERT] parsing error')
        return remote(i, path, method, body, timeout)

    monkeypatch.setattr(main.core, 'agent', failing)
    body = c.post(
        '/api/instances/1/apply-preview',
        json={'config': state['active'].rstrip('\n') + '\nbogus\n', 'base_hash': sha(state['active'])},
    ).json()
    assert body['source'] == 'text' and not body['valid'] and 'parsing error' in body['output']
    assert body['diff']['added'] == 1
    stale = c.post('/api/instances/1/apply-preview', json={'config': 'x\n', 'base_hash': 'a' * 64})
    assert stale.status_code == 409
