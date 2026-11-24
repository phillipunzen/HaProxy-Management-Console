"""HTTPS assistant: plan, challenge preparation and certificate assignment."""

import pytest

from backend import https_assistant
from backend.schemas import Document
from tests.test_import_api import api, apply_generated, sync_api  # noqa: F401  (fixtures)

CAP = {
    'http_challenge': True,
    'dns_credentials_ui': True,
    'acme_engines': ['certbot', 'lego'],
    'dns_credentials': [],
    'tls_site_bindings': True,
    'probes': True,
}
CERTS = [
    {'name': 'app_example_com', 'staging': False, 'days_remaining': 50, 'domains': ['app.example.com']},
    {'name': 'wild', 'staging': False, 'days_remaining': 0, 'domains': ['*.example.com']},
    {'name': 'old', 'staging': False, 'days_remaining': -1, 'domains': ['shop.example.com']},
    {'name': 'test', 'staging': True, 'days_remaining': 80, 'domains': ['shop.example.com']},
]


def native(**values):
    return Document.model_validate(
        {
            'hosts': [
                {'id': 'a', 'domain': 'app.example.com', 'servers': [{'address': '10.0.0.5'}]},
                {
                    'id': 'b',
                    'domain': 'shop.example.com',
                    'aliases': ['www.shop.example.com'],
                    'servers': [{'address': '10.0.0.6'}],
                },
                {'id': 'c', 'domain': '*.dev.example.com', 'servers': [{'address': '10.0.0.7'}]},
            ]
        }
        | values
    )


def test_plan_for_new_native_site_enables_challenge_and_tls():
    doc = native()
    result = https_assistant.plan(doc, doc.model_dump(), 'hosts', 'b', CAP, CERTS, '')
    assert result['domains'] == ['shop.example.com', 'www.shop.example.com']
    assert result['existing'] == []  # expired and staging certificates do not count
    assert result['challenges']['http']['available'] and not result['challenges']['dns']['available']
    assert 'DNS-Zugang' in result['challenges']['dns']['reason']
    assert result['enable_challenge'] and result['enable_tls'] and result['redirect']
    assert result['suggested_name'] == 'shop_example_com'
    assert result['blockers'] == [] and result['pending'] == []


def test_plan_reuses_covering_certificates_and_avoids_name_clashes():
    doc = native()
    result = https_assistant.plan(doc, doc.model_dump(), 'hosts', 'a', CAP, CERTS, '')
    assert [c['name'] for c in result['existing']] == ['app_example_com', 'wild']
    assert result['suggested_name'] == 'app_example_com-2'


def test_plan_wildcards_need_dns_and_list_other_pending_changes():
    doc = native()
    applied = native(hosts=[]).model_dump()
    result = https_assistant.plan(doc, applied, 'hosts', 'c', CAP, CERTS, '')
    assert result['wildcard'] and 'Platzhalter' in result['challenges']['http']['reason']
    assert any(b.startswith('Kein Weg zum Zertifikat') for b in result['blockers'])
    assert {p['key'] for p in result['pending']} == {'a', 'b'}
    dns = CAP | {'dns_credentials': [{'id': 'f' * 32, 'provider': 'cloudflare', 'label': 'Zone'}]}
    result = https_assistant.plan(doc, applied, 'hosts', 'c', dns, CERTS, '')
    assert result['challenges']['dns']['available'] and result['blockers'] == []


def test_plan_blocks_frontends_without_tls_and_unknown_items():
    doc = Document.model_validate(
        {
            'frontends': [{'name': 'plain', 'port': 8080}],
            'hosts': [
                {'id': 'a', 'domain': 'app.example.com', 'frontend': 'plain', 'servers': [{'address': '10.0.0.5'}]}
            ],
        }
    )
    result = https_assistant.plan(doc, None, 'hosts', 'a', CAP, CERTS, '')
    assert any('keinen HTTPS-Listener' in b for b in result['blockers'])
    assert not result['applied_known']
    assert https_assistant.plan(doc, None, 'hosts', 'zzz', CAP, CERTS, '') is None
    with pytest.raises(ValueError):
        https_assistant.assign(doc, 'hosts', 'a', 'app_example_com')


def test_assign_sets_certificate_redirect_and_listener():
    doc = https_assistant.assign(native(), 'hosts', 'a', 'app_example_com')
    host = doc.hosts[0]
    assert host.certificate == 'app_example_com' and host.force_https and doc.tls_enabled
    assert https_assistant.prepare_challenge(native()).acme_enabled


def test_routes_assign_imported_domain_and_apply(sync_api):  # noqa: F811
    c, _, state = sync_api
    doc = c.get('/api/instances/1/document').json()
    route = doc['imported_routes'][0]
    plan = c.get(f'/api/instances/1/https/imported_routes/{route["id"]}')
    assert plan.status_code == 200, plan.text
    plan = plan.json()
    assert [e['name'] for e in plan['existing']] == ['site']
    assert not plan['redirect'] and not plan['enable_tls'] and plan['blockers'] == []
    # Imported configs without a challenge route only offer DNS.
    assert not plan['challenges']['http']['available']
    assert c.post('/api/instances/1/https/prepare-challenge', json={'version': plan['version']}).status_code == 422
    missing = c.post(
        f'/api/instances/1/https/imported_routes/{route["id"]}/assign',
        json={'certificate': 'nope', 'version': plan['version']},
    )
    assert missing.status_code == 422
    stale = c.post(
        f'/api/instances/1/https/imported_routes/{route["id"]}/assign',
        json={'certificate': 'site', 'version': plan['version'] + 5},
    )
    assert stale.status_code == 409
    saved = c.post(
        f'/api/instances/1/https/imported_routes/{route["id"]}/assign',
        json={'certificate': 'site', 'version': plan['version']},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()['imported_routes'][0]['certificate'] == 'site'
    history = c.get(f'/api/instances/1/history/imported_routes/{route["id"]}').json()
    assert history[0]['fields'] == ['Zertifikat']
    result, _, _ = apply_generated(c)
    assert result.status_code == 200, result.text
    assert 'crt-list' in state['active']
    assert c.get('/api/instances/1/https/users/x').status_code == 404
    assert c.get('/api/instances/1/https/hosts/unknown').status_code == 404


def test_native_config_after_assistant_routes_challenge_redirects_and_binds_tls():
    from backend.generator import generate

    cap = {'runtime_socket_config': '/run/haproxy/admin.sock', 'cert_dir_config': '/etc/haproxy/certs'}
    doc = https_assistant.prepare_challenge(native())
    doc = https_assistant.assign(Document.model_validate(doc.model_dump()), 'hosts', 'b', 'shop_example_com')
    config = generate(Document.model_validate(doc.model_dump()), cap)
    assert 'use_backend acme_webroot if acme_challenge' in config
    assert 'bind :443 ssl' in config
    redirect = next(line for line in config.splitlines() if 'redirect scheme https' in line)
    assert redirect.endswith('!{ ssl_fc } !acme_challenge')
