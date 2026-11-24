"""Reachability checks: agent probes, the post-apply site plan and the per-item history."""

import http.server
import socket
import threading

import pytest

from agent import probes
from backend import changes, site_checks
from backend.schemas import Document
from tests.test_import_api import api, sync_api  # noqa: F401  (fixtures)


@pytest.fixture
def http_server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            host = self.headers.get('Host')
            self.send_response(200 if host == 'app.example.com' and self.path == '/api' else 404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def test_target_probe_reports_open_and_refused_ports(http_server):
    results = probes.run(
        probes.check_target,
        [
            probes.Target(address='127.0.0.1', port=http_server),
            probes.Target(address='127.0.0.1', port=free_port()),
        ],
    )
    assert results[0]['ok'] and results[0]['ms'] >= 0
    assert not results[1]['ok'] and results[1]['error'] == 'Verbindung abgelehnt'


def test_site_probe_sends_host_header_and_path(http_server):
    ok, missing = probes.run(
        probes.check_site,
        [
            probes.Site(domain='app.example.com', path='/api', port=http_server),
            probes.Site(domain='other.example.com', path='/api', port=http_server),
        ],
    )
    assert ok['status'] == 200 and missing['status'] == 404
    down = probes.check_site(probes.Site(domain='app.example.com', port=free_port()))
    assert down['status'] is None and down['error'] == 'Verbindung abgelehnt'


def test_site_probe_rejects_wildcards_and_bad_paths():
    with pytest.raises(ValueError):
        probes.Site(domain='*.example.com', port=80)
    with pytest.raises(ValueError):
        probes.Site(domain='app.example.com', path='no-slash', port=80)


CONFIG = """
frontend public_http
    bind :80
    bind :443 ssl crt /etc/haproxy/certs/
frontend internal
    bind 10.0.0.1:8080
"""


def test_plan_uses_https_for_redirected_hosts_and_skips_unreachable_or_wildcards():
    doc = Document.model_validate(
        {
            'hosts': [
                {'id': 'a', 'domain': 'app.example.com', 'force_https': True, 'servers': [{'address': '10.0.0.5'}]},
                {'id': 'b', 'domain': 'plain.example.com', 'path': '/x', 'servers': [{'address': '10.0.0.5'}]},
                {'id': 'c', 'domain': '*.wild.example.com', 'servers': [{'address': '10.0.0.5'}]},
                {'id': 'd', 'domain': 'off.example.com', 'enabled': False, 'servers': [{'address': '10.0.0.5'}]},
            ],
            'tls_enabled': True,
        }
    )
    entries = {e['key']: e for e in site_checks.plan(doc, CONFIG)}
    assert entries['a']['request'] == {'domain': 'app.example.com', 'path': '/', 'port': 443, 'tls': True}
    assert entries['b']['request'] == {'domain': 'plain.example.com', 'path': '/x', 'port': 80, 'tls': False}
    assert entries['c']['skipped'] == 'Nur Platzhalter-Domains'
    assert 'd' not in entries
    only = site_checks.plan(doc, CONFIG, [{'kind': 'hosts', 'key': 'b'}])
    assert [e['key'] for e in only] == ['b']
    assert site_checks.listener_ports(CONFIG)['internal'] == {'http': None, 'https': None}


@pytest.mark.parametrize(
    'result,state',
    [
        ({'status': 200}, 'ok'),
        ({'status': 301, 'location': 'https://app.example.com/'}, 'ok'),
        ({'status': 401}, 'ok'),
        ({'status': 404}, 'warning'),
        ({'status': 503}, 'error'),
        ({'status': None, 'error': 'Zeitüberschreitung'}, 'error'),
    ],
)
def test_verdicts(result, state):
    assert site_checks.verdict(result)[0] == state


def test_history_entries_name_changed_fields():
    before = {'hosts': [{'id': 'a', 'domain': 'app.example.com', 'path': '/', 'servers': [{'port': 80}]}]}
    after = {
        'hosts': [
            {'id': 'a', 'domain': 'app.example.com', 'path': '/api', 'name': 'x', 'servers': [{'port': 81}]},
            {'id': 'b', 'domain': 'new.example.com'},
        ]
    }
    entries = {e['key']: e for e in changes.history_entries(before, after)}
    assert entries['a']['action'] == 'changed' and entries['a']['fields'] == ['Pfad', 'Zielserver']
    assert entries['b']['action'] == 'added' and 'fields' not in entries['b']


def test_check_routes_require_capable_agent_and_report_results(sync_api, monkeypatch):  # noqa: F811
    from backend import main

    c, _, state = sync_api
    remote = main.core.agent
    seen = {}

    def agent(i, path='', method='GET', body=None, timeout=10):
        if path == '/probe/targets':
            seen['targets'] = body
            return {'results': [t | {'ok': True, 'ms': 3} for t in body['targets']]}
        if path == '/probe/sites':
            seen['sites'] = body
            return {'results': [s | {'status': 503} for s in body['sites']]}
        return remote(i, path, method, body, timeout)

    monkeypatch.setattr(main.core, 'agent', agent)
    target = {'targets': [{'address': '[2001:db8::1]', 'port': 8080}]}
    old = c.post('/api/instances/1/check-targets', json=target)
    assert old.status_code == 422 and 'Agent aktualisieren' in old.json()['detail']
    state['cap']['probes'] = True
    ok = c.post('/api/instances/1/check-targets', json=target)
    assert ok.status_code == 200 and ok.json()['results'][0]['ok']
    assert seen['targets']['targets'][0]['address'] == '2001:db8::1'
    sites = c.post('/api/instances/1/check-sites', json={}).json()['results']
    checked = [r for r in sites if r['state'] != 'skipped']
    assert checked and all(r['state'] == 'error' and r['status'] == 503 for r in checked)
    assert all(r['url'].startswith(('http://', 'https://')) for r in checked)


def test_saving_and_applying_writes_item_history(sync_api):  # noqa: F811
    from tests.test_import_api import apply_generated

    c, _, _ = sync_api
    doc = c.get('/api/instances/1/document').json()
    route = doc['imported_routes'][0]
    doc['imported_routes'][0] = {**route, 'aliases': ['www.' + route['domain']]}
    assert c.put('/api/instances/1/document', json=doc).status_code == 200
    history = c.get(f'/api/instances/1/history/imported_routes/{route["id"]}').json()
    assert history[0]['event'] == 'saved' and history[0]['action'] == 'changed'
    assert history[0]['fields'] == ['weitere Domains'] and history[0]['actor']
    result, _, _ = apply_generated(c)
    assert result.status_code == 200, result.text
    history = c.get(f'/api/instances/1/history/imported_routes/{route["id"]}').json()
    assert [h['event'] for h in history[:2]] == ['applied', 'saved']
    assert c.get('/api/instances/1/history/users/x').status_code == 404
