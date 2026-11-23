"""Readable HAProxy names for proxy hosts and recognition of legacy generated hosts."""

import hashlib

import pytest
from backend import naming
from backend.generator import generate
from backend.haproxy_config import import_config
from backend.schemas import Document, Host

CAP = {'runtime_socket_config': '/run/admin.sock', 'cert_dir_config': '/etc/certs'}


def host(id='app', domain='app.example.com', **kw):
    kw.setdefault('servers', [{'address': '192.0.2.1'}])
    return Host(id=id, domain=domain, **kw)


@pytest.mark.parametrize(
    'domain,path,expected',
    [
        ('app.example.com', '/', 'app_example_com'),
        ('pc-wiki.de', '/wiki', 'pc_wiki_de_wiki'),
        ('shop.example.com', '/api/v1/', 'shop_example_com_api_v1'),
        ('*.example.com', '/', 'wildcard_example_com'),
        ('*.example.com', '/admin', 'wildcard_example_com_admin'),
    ],
)
def test_base_is_slug_of_domain_and_path(domain, path, expected):
    assert naming.base(host(domain=domain, path=path)) == expected


def test_server_prefix_uses_first_label_and_wildcard():
    (named,) = naming.assign([host(servers=[{'address': '192.0.2.1'}, {'address': '192.0.2.2'}])])
    assert naming.servers(named) == ['app_1', 'app_2']
    wildcard = naming.assign([host(domain='*.example.com')])[0]
    assert wildcard.name == 'wildcard_example_com' and naming.servers(wildcard) == ['wildcard_1']
    # Unnamed hosts keep the legacy scheme.
    legacy = host(id='x')
    assert legacy.name is None
    assert naming.backend(legacy) == 'backend_x' and naming.acls(legacy) == ('host_x', 'path_x')
    assert naming.servers(legacy) == ['srv_1'] and naming.condition(legacy) == ['host_x', 'path_x']


def test_root_path_needs_no_path_acl():
    root, path = naming.assign([host(), host(id='api', path='/api')])
    assert naming.condition(root) == ['app_example_com']
    assert naming.condition(path) == ['app_example_com_api', 'app_example_com_api_path']


def test_assign_suffixes_collisions_with_id_prefix():
    # Different domains with the same slug, e.g. on different frontends.
    first = host(id='abcdefghijk', domain='a-b.example.com')
    second = host(id='second', domain='a.b.example.com')
    named = naming.assign([first, second])
    assert [h.name for h in named] == ['a_b_example_com_abcdefgh', 'a_b_example_com_second']
    # The originals are left untouched.
    assert first.name is None and second.name is None


def test_assign_keeps_stored_name_when_second_host_with_same_base_is_added():
    (first,) = naming.assign([host(id='one')])
    assert first.name == 'app_example_com'
    added = host(id='two', frontend='other')
    named = naming.assign([first, added])
    assert [h.name for h in named] == ['app_example_com', 'app_example_com_two']
    assert named[0] is first
    # Stable on repeated assignment, also in a different order.
    assert [h.name for h in naming.assign(named)] == ['app_example_com', 'app_example_com_two']
    assert [h.name for h in naming.assign(named[::-1])] == ['app_example_com_two', 'app_example_com']


def test_assign_replaces_stored_name_that_no_longer_fits_domain_and_path():
    (named,) = naming.assign([host()])
    moved = named.model_copy(update={'domain': 'new.example.com'})
    assert naming.assign([moved])[0].name == 'new_example_com'


def test_document_validation_assigns_names_and_keeps_them_stable():
    doc = Document(hosts=[host(id='one')])
    assert doc.hosts[0].name == 'app_example_com'
    data = doc.model_dump()
    data['hosts'].append(host(id='two', frontend='other').model_dump())
    again = Document.model_validate(data)
    assert [h.name for h in again.hosts] == ['app_example_com', 'app_example_com_two']


def test_reserved_names_are_avoided():
    reserved = naming.assign([host(id='unknown', domain='unknown.host')])[0]
    assert reserved.name == 'unknown_host_unknown'
    taken = naming.assign([host(id='app')], reserved={'app_example_com', 'app_example_com_app'})[0]
    assert taken.name == 'app_example_com_2'
    doc = Document(hosts=[host(id='acme', domain='acme.webroot')])
    assert doc.hosts[0].name not in naming.RESERVED
    assert 'backend acme_webroot_acme\n' in generate(doc, CAP)


def test_recovered_id_reuses_valid_names_and_hashes_others():
    assert naming.recovered_id('app_example_com') == 'app_example_com'
    dotted = naming.recovered_id('app.example.com')
    assert dotted == 'h' + hashlib.sha256(b'app.example.com').hexdigest()[:12]
    assert naming.recovered_id('x' * 41).startswith('h') and len(naming.recovered_id('x' * 41)) == 13


def test_strip_markers_removes_only_generated_comment_lines():
    config = (
        'frontend public_http\n'
        '    # [Proxy Host] app.example.com\r\n'
        '    acl app_example_com hdr(host),field(1,:) -i app.example.com\n'
        '    # [Proxy Host]\n'
        '    # user note about [Proxy Host]\n'
        '# [Proxy Host] app.example.com -> 1 Zielserver\n'
        'backend app_example_com\n'
    )
    assert naming.strip_markers(config) == (
        'frontend public_http\n'
        '    acl app_example_com hdr(host),field(1,:) -i app.example.com\n'
        '    # user note about [Proxy Host]\n'
        'backend app_example_com\n'
    )
    plain = 'backend web\n    # comment\n'
    assert naming.strip_markers(plain) is plain


def test_native_config_uses_readable_names_and_comments():
    config = generate(Document(hosts=[host(), host(id='wiki', path='/wiki')]), CAP)
    lines = config.splitlines()
    assert 'backend app_example_com' in lines and 'backend app_example_com_wiki' in lines
    assert '    use_backend app_example_com if app_example_com' in lines
    assert '    use_backend app_example_com_wiki if app_example_com_wiki app_example_com_wiki_path' in lines
    assert '    acl app_example_com_wiki_path path_beg /wiki' in lines
    assert 'app_example_com_path' not in config
    assert '    server app_1 192.0.2.1:80 weight 100 check' in lines
    assert '    # [Proxy Host] app.example.com' in lines
    assert '    # [Proxy Host] app.example.com  Pfad /wiki' in lines
    assert '# [Proxy Host] app.example.com -> 1 Zielserver' in lines
    # Defaults already set mode http; readable native host backends omit it.
    section = config.split('backend app_example_com\n')[1].split('\n\n')[0]
    assert 'mode http' not in section
    assert 'backend_app' not in config and 'host_app' not in config and 'srv_1' not in config
    # Generated hosts are recognized again, with and without document metadata.
    again = Document.model_validate(import_config(config, hashlib.sha256(config.encode()).hexdigest())['document'])
    assert [h.id for h in again.hosts] == ['app', 'wiki']
    assert generate(again, CAP).count(naming.MARKER) == config.count(naming.MARKER)


LEGACY = """defaults
    mode http
    timeout connect 5s
    timeout client 60s
    timeout server 60s
frontend public_http
    bind :80
    acl host_x hdr(host),field(1,:) -i app.example.com
    acl path_x path_beg /
    use_backend backend_x if host_x path_x
    default_backend unknown_host
backend unknown_host
    http-request deny deny_status 404
backend backend_x
    mode http
    balance roundrobin
    server srv_1 192.0.2.1:80 weight 100 check
"""


def test_legacy_hand_written_config_is_recognized_as_managed_host():
    result = import_config(LEGACY, hashlib.sha256(LEGACY.encode()).hexdigest())
    assert result['summary']['managed_hosts'] == 1
    doc = Document.model_validate(result['document'])
    assert [h.id for h in doc.hosts] == ['x'] and [h.id for h in doc.imported_managed_hosts] == ['x']
    assert doc.hosts[0].domain == 'app.example.com' and doc.hosts[0].path == '/'
    assert not any(b.name == 'backend_x' for b in doc.imported_backends)
    # Regenerating migrates the legacy pool to the readable name exactly once.
    output = generate(doc, CAP)
    assert output.splitlines().count('backend app_example_com') == 1
    assert 'backend_x' not in output and 'host_x' not in output and 'srv_1' not in output
