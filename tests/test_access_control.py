"""Scoped IP allowlists, ordering, validation, metadata and import/deletion safety."""
import base64
import json

import pytest
from backend import access_control as access,basic_auth
from backend.backend_delete import plan
from backend.generator import generate
from backend.haproxy_config import import_config,strip_document_metadata,parse_sections
from backend.schemas import AccessPolicy,Document,Host,ImportedRoute,ManagedFrontend
from tests.test_backend_delete import CONFIG,CAP,imported
from tests.test_import_api import api

POLICY={'networks':['192.168.10.7/24','2001:db8::1'],'paths':['/admin']}

def host(**kw):return Host(id='app',domain='app.example.com',aliases=['www.example.com'],servers=[{'address':'127.0.0.1'}],**kw)
def rules(config,name='public_http'):
    lines,sections=parse_sections(config);s=next(s for s in sections if s.name==name and s.kind in ('frontend','listen'))
    return ''.join(lines[s.start:s.end])


def test_network_validation_normalizes_ipv4_ipv6_comma_and_duplicates():
    p=AccessPolicy(networks=[' 192.168.10.7/24, 10.8.0.12 ','2001:db8::ABCD/32','10.8.0.12',''],paths=[' /admin ','','/admin'])
    assert p.networks==['192.168.10.0/24','10.8.0.12','2001:db8::/32'] and p.paths==['/admin']

@pytest.mark.parametrize('values',[[],[''],['example.com'],['192.168.1.256'],['127.0.0.1;http-request'],['127.0.0.1\nbackend injected'],['::1%eth0'],['0.0.0.0/33']])
def test_invalid_or_empty_networks_are_rejected(values):
    with pytest.raises(ValueError):AccessPolicy(networks=values)

@pytest.mark.parametrize('value',['admin','/admin?x=1','/admin#part','/admin\nhttp-request allow',"/admin'",'/admin}'])
def test_invalid_paths_cannot_become_haproxy_directives(value):
    with pytest.raises(ValueError):AccessPolicy(networks=['127.0.0.1'],paths=[value])


def test_native_aliases_scoped_before_redirect_global_rules_and_not_basic_auth_bypass():
    doc=Document(tls_enabled=True,hosts=[host(access_policy=POLICY,force_https=True,basic_auth_group=1),Host(id='other',domain='other.example.com',servers=[{'address':'127.0.0.1'}])],
                 rules=[{'id':'redirect','name':'redirect','match':'path_prefix','value':'/old','action':'redirect','target':'https://example.com/new'}])
    groups={1:{'id':1,'realm':'Members','users':[{'username':'member','hash':'$6$test$hash'}]}}
    output=generate(doc,CAP,groups);front=rules(output)
    assert front.count('deny_status 403')==2 and 'www.example.com' in front
    assert front.index('deny_status 403')<front.index('http-request redirect')
    assert ' fc_src 192.168.10.0/24 2001:db8::1' in front and ' path_beg /admin' in front and 'path,url_dec -m beg /admin' in front
    assert 'X-Forwarded-For' not in front and 'http_auth(mgmt_basic_g1)' in output
    assert len(access.read(output)['sites'])==2
    again=imported(output);assert again.hosts[0].access_policy==doc.hosts[0].access_policy
    assert generate(again,CAP,groups)==output


def test_disabled_hosts_have_no_restrictions_and_empty_policy_differs_from_none():
    output=generate(Document(hosts=[host(access_policy=POLICY,enabled=False)]),CAP)
    assert access.read(output) is None and 'deny_status 403' not in output
    assert 'deny_status 403' not in generate(Document(hosts=[host()]),CAP)
    with pytest.raises(ValueError):host(access_policy={'networks':[]})


def test_whole_entry_uses_host_base_path_and_acme_exception_is_explicit():
    doc=Document(acme_enabled=True,hosts=[host(path='/api',access_policy={'networks':['10.0.0.0/8']})])
    front=rules(generate(doc,CAP));assert ' path_beg /api' in front and '!{ path_beg /.well-known/acme-challenge/ }' in front
    doc.hosts[0].access_policy.paths=['/admin']
    with pytest.raises(ValueError,match='innerhalb'):access.validate_document(doc)
    with pytest.raises(ValueError,match='innerhalb'):generate(doc,CAP)


@pytest.mark.parametrize('legacy',[False,True])
def test_imported_shared_pool_keeps_per_domain_policy_and_can_clear_after_import(legacy):
    doc=imported();doc.imported_routes[0].access_policy=AccessPolicy.model_validate(POLICY)
    config=generate(doc,CAP);front=rules(config,'incoming')
    assert 'app.example.com' in front and 'www.example.com' in front
    assert all(s['domain']!='other.example.com' for s in access.read(config)['sites'])
    if legacy:config=strip_document_metadata(config)
    again=imported(config);assert again.imported_routes[0].access_policy==doc.imported_routes[0].access_policy
    again.imported_routes[0].access_policy=None
    cleared=generate(again,CAP);assert access.read(cleared) is None and 'deny_status 403' not in cleared
    assert 'use_backend web' in cleared and 'use_backend other' in cleared


def test_legacy_tool_hosts_retain_policy_and_identity_when_graph_metadata_removed():
    doc=Document(hosts=[host(access_policy=POLICY)])
    config=strip_document_metadata(generate(doc,CAP));again=imported(config)
    assert [h.id for h in again.hosts]==['app'] and again.hosts[0].access_policy==doc.hosts[0].access_policy
    assert [h.id for h in again.imported_managed_hosts]==['app']
    again.hosts[0].access_policy=None
    assert access.read(generate(again,CAP)) is None


def test_imported_aliases_with_different_policies_stay_separate_on_conservative_reimport():
    doc=imported();old=doc.imported_routes[0];old.aliases=[];old.access_policy=AccessPolicy(networks=['192.0.2.1'])
    doc.imported_routes.append(ImportedRoute(id='www',frontend=old.frontend,backend=old.backend,domain='www.example.com',access_policy=AccessPolicy(networks=['192.0.2.2'])))
    config=strip_document_metadata(generate(doc,CAP));config=config.replace(' acl mgmt_www hdr(host) -i www.example.com\n use_backend web if mgmt_www\n','')
    # Equivalent consolidated routing ACL, without changing the managed guard.
    config=config.replace('hdr(host) -i app.example.com\n','hdr(host) -i app.example.com www.example.com\n')
    again=imported(config);routes={r.domain:r for r in again.imported_routes}
    assert routes['app.example.com'].access_policy.networks==['192.0.2.1'] and routes['www.example.com'].access_policy.networks==['192.0.2.2']
    assert not routes['app.example.com'].aliases


def test_manual_edits_outside_guard_keep_policy_but_guard_edits_are_rejected():
    config=generate(Document(hosts=[host(access_policy=POLICY)]),CAP)
    changed=config.replace('timeout client 60s','timeout client 61s')
    again=imported(changed);assert again.hosts[0].access_policy is not None
    for broken in [config.replace('deny_status 403','deny_status 404'),config.replace('    acl mgmt_access_','    acl tampered_',1),config.replace(access.META,'# missing '),config.replace(access.PREFIX+'END ','# missing END ')]:
        with pytest.raises(ValueError,match='IP-Zugriff'):imported(broken)


def test_crlf_import_preserves_and_rebuilds_verified_blocks():
    config=strip_document_metadata(generate(Document(hosts=[host(access_policy=POLICY)]),CAP)).replace('\n','\r\n')
    again=imported(config);assert again.hosts[0].access_policy is not None
    assert access.read(generate(again,CAP))['sites']==access.read(config)['sites']


def test_ip_policies_do_not_resurrect_after_backend_or_host_deletion():
    doc=imported();doc.imported_routes[0].access_policy=AccessPolicy.model_validate(POLICY)
    raw=strip_document_metadata(generate(doc,CAP));again=imported(raw)
    changed,_=plan(again,'web');output=generate(changed,CAP)
    assert access.read(output) is None and 'deny_status 403' not in output
    doc=imported(strip_document_metadata(generate(Document(hosts=[host(access_policy=POLICY)]),CAP)))
    changed,_=plan(doc,'backend_app');assert access.read(generate(changed,CAP)) is None


def test_listen_delete_removes_guard_while_unrelated_guards_survive():
    doc=imported(CONFIG.replace('frontend incoming','listen incoming').replace(' bind :8088',' bind :8088\n server local 127.0.0.1:8082 check'))
    doc.imported_routes[0].access_policy=AccessPolicy.model_validate(POLICY)
    config=strip_document_metadata(generate(doc,CAP));again=imported(config);changed,_=plan(again,'incoming')
    assert access.read(generate(changed,CAP)) is None


@pytest.mark.parametrize('action',['allow','return status 200','set-src hdr(X-Forwarded-For)','set-path /admin'])
def test_inherited_http_rules_cannot_bypass_guard(action):
    config=CONFIG.replace('defaults\n','defaults shared\n http-request '+action+'\n').replace('frontend incoming\n','frontend incoming from shared\n')
    doc=imported(config);doc.imported_routes[0].access_policy=AccessPolicy.model_validate(POLICY)
    with pytest.raises(ValueError,match='geerbten defaults'):generate(doc,CAP)
    with pytest.raises(ValueError,match='geerbten defaults'):access.validate_document(doc)


def test_tcp_set_src_before_http_is_rejected_and_frontend_http_allow_is_after_guard():
    doc=imported(CONFIG.replace(' bind :8088',' bind :8088\n tcp-request connection set-src src'))
    doc.imported_routes[0].access_policy=AccessPolicy.model_validate(POLICY)
    with pytest.raises(ValueError,match='set-src'):generate(doc,CAP)
    doc=imported(CONFIG.replace(' bind :8088',' bind :8088\n http-request allow'))
    doc.imported_routes[0].access_policy=AccessPolicy.model_validate(POLICY)
    front=rules(generate(doc,CAP),'incoming');assert front.index('deny_status 403')<front.index('http-request allow')


def test_metadata_is_validated_even_when_document_snapshot_is_valid():
    config=generate(Document(hosts=[host(access_policy=POLICY)]),CAP)
    data=access.read(config);data['sites'][0]['policy']['networks']=['127.0.0.1\n10.0.0.0/8'];data['digest']=access.digest(data['sites'])
    forged='\n'.join(line for line in config.splitlines() if not line.startswith(access.META))+'\n'+access.META+base64.b64encode(json.dumps(data).encode()).decode()+'\n'
    with pytest.raises(ValueError,match='metadaten'):imported(forged)


def test_api_save_validation_no_agent_calls_version_and_clear(api):
    from backend.db import Instance
    c,factory,state=api
    doc=imported().model_dump()
    result=c.put('/api/instances/1/document',json=doc);assert result.status_code==200,result.text
    doc=result.json();doc['imported_routes'][0]['access_policy']=POLICY
    result=c.put('/api/instances/1/document',json=doc);assert result.status_code==200,result.text
    assert result.json()['version']==2 and result.json()['imported_routes'][0]['access_policy']['networks']==['192.168.10.0/24','2001:db8::1']
    assert c.put('/api/instances/1/document',json=doc).status_code==409
    doc=result.json();doc['imported_routes'][0]['access_policy']['networks']=['bad']
    assert c.put('/api/instances/1/document',json=doc).status_code==422
    doc=result.json();doc['imported_routes'][0]['access_policy']=None
    result=c.put('/api/instances/1/document',json=doc);assert result.status_code==200,result.text
    with factory() as db:assert db.get(Instance,2).document_version==0
    assert state['calls']==[]


def test_original_manual_limits_remain_when_central_policy_is_cleared():
    original=CONFIG.replace(' bind :8088',' bind :8088\n http-request deny if { src 198.51.100.9 }')
    doc=imported(original);doc.imported_routes[0].access_policy=AccessPolicy.model_validate(POLICY)
    raw=strip_document_metadata(generate(doc,CAP));again=imported(raw);again.imported_routes[0].access_policy=None
    output=generate(again,CAP)
    assert access.read(output) is None and 'http-request deny if { src 198.51.100.9 }' in output


def test_wildcard_hosts_use_suffix_matching_and_policies_cannot_attach_to_tcp():
    doc=Document(hosts=[Host(id='wild',domain='*.example.com',servers=[{'address':'127.0.0.1'}],access_policy=POLICY)])
    front=rules(generate(doc,CAP));assert 'hdr(host),field(1,:) -m end -i .example.com' in front
    doc.hosts[0].frontend='stream';doc.frontends=[ManagedFrontend(name='stream',mode='tcp',port=9000)]
    doc=Document.model_validate(doc.model_dump())
    with pytest.raises(ValueError,match='HTTP-Frontend'):access.validate_document(doc)



def test_frontend_and_backend_http_source_rewrites_do_not_change_connection_identity_acl():
    config=CONFIG.replace(' bind :8088',' bind :8088\n http-request set-src hdr(X-Forwarded-For)').replace('backend web\n','backend web\n http-request set-src hdr(X-Forwarded-For)\n')
    doc=imported(config);doc.imported_routes[0].access_policy=AccessPolicy.model_validate(POLICY)
    access.validate_document(doc)
    output=generate(doc,CAP)
    assert '_src fc_src ' in output and 'http-request set-src hdr(X-Forwarded-For)' in output
