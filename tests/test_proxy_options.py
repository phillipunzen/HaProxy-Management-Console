"""Proxy directives retain scope, order, raw expressions and import round trips."""
import hashlib

import pytest
from pydantic import ValidationError
from backend.generator import generate
from backend.haproxy_config import import_config,generate_imported,strip_document_metadata,parse_sections
from backend.schemas import Document,Host,ImportedBackend,ManagedBackend
from backend import basic_auth

CAP={'runtime_socket_config':'/run/admin.sock','cert_dir_config':'/etc/certs'}
OPTIONS=['http-request set-path /reset-password%[path]',
         'http-request set-header Host %[req.hdr(host)]',
         'http-request set-header X-Forwarded-Host %[req.hdr(host)]',
         'http-request set-header X-Forwarded-Proto https',
         'http-request set-header X-Forwarded-Port 443',
         'option forwardfor header X-Forwarded-For']

def host(**kw):return Host(id='site',domain='app.example.com',servers=[{'address':'127.0.0.1','port':8080}],**kw)
def imported(config):return Document.model_validate(import_config(config,hashlib.sha256(config.encode()).hexdigest())['document'])
def backend_text(text,name='web'):
    lines,sections=parse_sections(text);section=next(s for s in sections if s.kind=='backend' and s.name==name)
    return ''.join(lines[section.start:section.end])

CONFIG='''defaults
 mode http
 timeout connect 5s
 timeout client 10s
 timeout server 10s
frontend incoming
 bind :8088
 acl mgmt_app hdr(host) -i app.example.com
 use_backend web if mgmt_app
 acl mgmt_other hdr(host) -i other.example.com
 use_backend web if mgmt_other
backend web
 mode http
 balance roundrobin
 # preserve the original placement
 http-request set-path /old%[path] # old-path
 acl private src 192.0.2.1
 http-request auth realm legacy unless { http_auth(users) }
 http-request set-header X-Test "value with spaces" # keep-comment
 http-response set-header X-Response %[status]
 option forwardfor header X-Forwarded-For
 server srv 127.0.0.1:8080 check inter 3s rise 2
userlist users
 user old insecure-password old
'''


@pytest.mark.parametrize('new_front',[False,True])
def test_options_only_apply_to_own_host_backend_and_survive_reimport(new_front):
    first=host(proxy_options=['mode http']+OPTIONS)
    other=host();other.id='other';other.domain='other.example.com'
    doc=Document(hosts=[first,other])
    if new_front:
        from backend.schemas import ManagedFrontend
        doc.frontends=[ManagedFrontend(name='incoming',mode='http',port=8088)];first.frontend='incoming'
    text=generate(doc,CAP)
    assert text.count('http-request set-path /reset-password%[path]')==1
    section=backend_text(text,'backend_site')
    assert all('    '+line+'\n' in section for line in OPTIONS)
    assert 'mode http' in section and first.proxy_options==OPTIONS
    again=imported(text);assert again.hosts[0].proxy_options==OPTIONS
    again.hosts[0].proxy_options=[]
    clean=generate(again,CAP);assert not any(line in clean for line in OPTIONS)
    assert clean.count('backend backend_site\n')==1


def test_legacy_generated_host_with_supported_custom_options_stays_editable():
    text=strip_document_metadata(generate(Document(hosts=[host(proxy_options=OPTIONS)]),CAP))
    doc=imported(text);assert doc.hosts[0].proxy_options==OPTIONS
    doc.hosts[0].proxy_options.pop(0)
    fresh=generate(doc,CAP);assert '/reset-password%[path]' not in fresh
    assert fresh.count('http-request set-header Host')==1


def test_imported_add_change_remove_preserves_other_rules_and_shared_backend():
    doc=imported(CONFIG);pool=doc.imported_backends[0]
    assert pool.proxy_options==['http-request set-path /old%[path] # old-path',
                              'http-request set-header X-Test "value with spaces" # keep-comment',
                              'http-response set-header X-Response %[status]',
                              'option forwardfor header X-Forwarded-For']
    assert backend_text(generate_imported(doc))==backend_text(CONFIG)
    pool.proxy_options=['http-request set-path /reset-password%[path]',pool.proxy_options[1],
                        'http-request set-header X-Forwarded-Proto https']
    output=generate_imported(doc)
    assert '/old%[path]' not in output and 'http-response set-header X-Response' not in output
    assert 'option forwardfor' not in output and output.count('backend web\n')==1
    assert output.count('use_backend web if')==2
    assert 'server srv 127.0.0.1:8080 check inter 3s rise 2' in output
    assert '# preserve the original placement' in output and 'http-request auth realm legacy' in output
    assert output.index('set-path')<output.index('acl private')<output.index('http-request auth')<output.index('http-request set-header X-Test')
    again=imported(generate(doc,CAP));assert again.imported_backends[0].proxy_options==pool.proxy_options
    again.imported_backends[0].proxy_options=[]
    clean=generate(again,CAP);assert '/reset-password%[path]' not in clean and 'set-header X-Test' not in clean
    assert 'http-request auth realm legacy' in clean


def test_legacy_draft_missing_options_retains_live_baseline_and_empty_list_removes():
    old=imported(CONFIG).model_dump();old['imported_backends'][0].pop('proxy_options')
    doc=Document.model_validate(old);assert len(doc.imported_backends[0].proxy_options)==4
    assert backend_text(generate_imported(doc))==backend_text(CONFIG)
    old['imported_backends'][0]['proxy_options']=[]
    doc=Document.model_validate(old);assert not doc.imported_backends[0].proxy_options
    assert 'set-header X-Test' not in generate_imported(doc)


def test_unchanged_crlf_and_no_final_newline_are_preserved():
    for original in [CONFIG.replace('\n','\r\n'),CONFIG.rstrip('\n')]:
        doc=imported(original);assert backend_text(generate_imported(doc))==backend_text(original)
        doc.imported_backends[0].proxy_options.append('timeout server 45s')
        output=generate_imported(doc);assert 'timeout server 45s' in output
        if '\r\n' in original:assert '\n' not in output.replace('\r\n','')


def test_options_and_target_or_balance_changes_merge_without_overwriting_each_other():
    doc=imported(CONFIG.replace(' balance roundrobin\n','').replace(' http-request set-path /old%[path] # old-path\n','').replace(' http-request set-header X-Test "value with spaces" # keep-comment\n','').replace(' http-response set-header X-Response %[status]\n','').replace(' option forwardfor header X-Forwarded-For\n',''))
    pool=doc.imported_backends[0];pool.proxy_options=OPTIONS;pool.balance='leastconn';pool.servers[0].port=8081
    output=generate_imported(doc)
    assert 'balance leastconn' in output and '127.0.0.1:8081 check inter 3s rise 2' in output
    assert all(line in output for line in OPTIONS)


def test_central_authentication_runs_before_custom_header_and_path_changes():
    doc=Document(hosts=[host(proxy_options=OPTIONS,basic_auth_group=1)])
    config=generate(doc,CAP,{1:{'id':1,'realm':'Members','users':[{'username':'member','hash':'$6$unused'}]}})
    backend=backend_text(config,'backend_site')
    assert backend.index('http-request auth')<backend.index('http-request set-path')
    assert not any('Authorization' in line for line in imported(config).hosts[0].proxy_options)
    assert basic_auth.bindings(doc)[0]['backend']=='backend_site'


@pytest.mark.parametrize('line',['backend injected','frontend x','server bad 192.0.2.1:80','bind :9000','mode tcp',
                                'http-request auth realm bypass','http-request allow','http-request return status 200',
                                'http-request set-path /ok\nbackend injected','option forwardfor\rserver bad','option forwardfor\x00',
                                'http-request set-header X-Test "unterminated','http-request set-var(txn.mgmt_skip_own) bool(true)'])
def test_invalid_or_managed_directives_are_rejected(line):
    with pytest.raises(ValidationError):host(proxy_options=[line])
    with pytest.raises(ValidationError):ImportedBackend(name='web',mode='http',servers=[],proxy_options=[line])


def test_known_sample_expressions_tabs_quotes_conditionals_and_inline_comments_are_retained():
    options=['http-request\tset-header X-Example "a b # c" if { path_beg /api } # note',
             'http-response del-header Server','http-after-response set-header X-Trace %[unique-id]',
             'no option forwardfor','timeout server 45s']
    assert host(proxy_options=options).proxy_options==options


def test_tcp_pool_options_cannot_be_assigned():
    with pytest.raises(ValidationError):Document(backends=[ManagedBackend(name='db',mode='tcp',servers=[{'address':'127.0.0.1'}],proxy_options=OPTIONS)])


def test_new_options_follow_legacy_auth_when_pool_has_no_editable_options():
    original="""defaults
 mode http
frontend incoming
 bind :8088
 acl app hdr(host) -i app.example.com
 use_backend web if app
backend web
 http-request auth realm Legacy if { hdr(host) -i app.example.com } !{ http_auth(users) }
 server srv 127.0.0.1:8080 check
userlist users
 user member insecure-password test
"""
    doc=imported(original);doc.imported_backends[0].proxy_options=['http-request set-header Host backend.internal']
    value=backend_text(generate_imported(doc))
    assert value.index('http-request auth')<value.index('http-request set-header Host')
