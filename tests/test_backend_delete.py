"""Draft deletion preserves unrelated configuration and never leaves static routes."""
import hashlib

import pytest
from backend.backend_delete import plan
from backend.generator import generate
from backend.haproxy_config import import_config,parse_sections,strip_document_metadata
from backend.schemas import Document,Host,ImportedSource
from backend import basic_auth,tls_bindings
from tests.test_import_api import api

CAP={'runtime_socket_config':'/run/admin.sock','cert_dir_config':'/etc/certs','tls_site_bindings':True}
CONFIG='''defaults
 mode http
 timeout connect 1s
 timeout client 5s
 timeout server 5s
frontend incoming
 bind :8088
 acl app hdr(host) -i app.example.com www.example.com
 use_backend web if app
 acl route_other hdr(host) -i other.example.com
 use_backend other if route_other
 default_backend web
frontend mysql
 bind :3306
 mode tcp
 default_backend db
backend web
 server app 127.0.0.1:8080 check
backend other
 # keep this comment
 http-request set-header X-Test keep
 server other 127.0.0.1:8081 check
backend db
 mode tcp
 server database 127.0.0.1:3306 check
'''

def imported(config=CONFIG,maps=()):
    return Document.model_validate(import_config(config,hashlib.sha256(config.encode()).hexdigest(),list(maps))['document'])

def names(config):return {s.name for s in parse_sections(config)[1] if s.kind in ('backend','listen')}

@pytest.mark.parametrize('ending',['\n','\r\n'])
def test_imported_http_pool_removes_alias_routes_default_and_preserves_other_edits(ending):
    doc=imported(CONFIG.replace('\n',ending));original=doc.model_dump()
    doc.imported_backends[1].servers[0].port=9099
    changed,summary=plan(doc,'web');output=generate(changed,CAP)
    assert summary['domains']==['app.example.com','www.example.com']
    assert 'incoming: default_backend web' in summary['references']
    assert names(output)=={'other','db'} and 'use_backend web' not in output and 'default_backend web' not in output
    assert 'other.example.com' in output and 'server other 127.0.0.1:9099 check' in output and '# keep this comment' in output
    assert changed.imported_config==original['imported_config'] and changed.imported_active_hash==original['imported_active_hash']
    assert doc.removed_backends==[] and len(doc.imported_routes)==2  # preview is pure
    again=imported(output);assert names(generate(again,CAP))=={'other','db'}
    assert again.imported_routes[0].domain=='other.example.com'


def test_tcp_imported_pool_deletes_default_but_keeps_unrelated_listener():
    changed,summary=plan(imported(),'db');output=generate(changed,CAP)
    assert 'mysql: default_backend db' in summary['references']
    assert 'frontend mysql' in output and 'default_backend db' not in output and 'db' not in names(output)
    assert 'web' in names(output)


def test_map_routes_and_fallback_can_be_removed_in_separate_operations():
    config=CONFIG.replace(' acl app hdr(host) -i app.example.com www.example.com\n use_backend web if app\n acl route_other hdr(host) -i other.example.com\n use_backend other if route_other\n default_backend web',
                          ' use_backend %[req.hdr(host),lower,map(/etc/vhosts.map,fallback)]')+'backend fallback\n http-request return status 404\n'
    maps=[{'path':'/etc/vhosts.map','content':'app.example.com web\nwww.example.com web\nother.example.com other\n'}]
    doc=imported(config,maps);doc.imported_sources=[ImportedSource(path='/etc/haproxy.cfg',hash='b'*64)]
    changed,_=plan(doc,'web');changed,summary=plan(changed,'fallback');output=generate(changed,CAP)
    assert summary['references']==['incoming: Map-Fallback fallback']
    assert names(output)=={'other','db'} and 'use_backend fallback' not in output
    assert 'other.example.com' in output and 'app.example.com' not in output
    assert changed.imported_maps==doc.imported_maps and changed.imported_sources==doc.imported_sources
    again=imported(output,maps);assert names(generate(again,CAP))=={'other','db'}


def test_combined_listen_deletes_listener_and_its_routes_hosts_and_cert_assignments():
    config=CONFIG.replace('frontend incoming','listen incoming').replace(' bind :8088',' bind :8088\n server local 127.0.0.1:8082 check')
    doc=imported(config);doc.frontend_certificates={'incoming':['certificate']}
    doc.hosts=[Host(id='extra',domain='extra.example.com',frontend='incoming',servers=[{'address':'127.0.0.1'}])]
    doc=Document.model_validate(doc.model_dump())
    changed,summary=plan(doc,'incoming');output=generate(changed,CAP)
    assert summary['listeners']==['incoming'] and summary['domains']==['extra.example.com','app.example.com','www.example.com','other.example.com']
    assert not changed.hosts and not changed.imported_routes and not changed.frontend_certificates
    assert 'listen incoming' not in output and 'backend_extra' not in names(output)
    assert names(output)=={'web','other','db'}


def test_managed_tcp_listener_is_explicitly_included_and_http_default_is_cleared():
    doc=Document(frontends=[{'name':'http','port':8080,'backend':'web'}, {'name':'tcp','mode':'tcp','port':8090,'backend':'stream'}],
                 backends=[{'name':'web','servers':[{'address':'127.0.0.1'}]}, {'name':'stream','mode':'tcp','servers':[{'address':'127.0.0.1'}]}])
    changed,summary=plan(doc,'stream');assert summary['listeners']==['tcp']
    assert 'frontend tcp' not in generate(changed,CAP)
    changed,summary=plan(changed,'web');assert changed.frontends[0].backend is None
    assert 'frontend http' in generate(changed,CAP) and 'default_backend web' not in generate(changed,CAP)


@pytest.mark.parametrize('import_first',[False,True])
def test_automatic_host_backend_deletion_preserves_other_hosts_identity(import_first):
    doc=Document(hosts=[{'id':id,'domain':id+'.example.com','servers':[{'address':'127.0.0.1'}]} for id in ('app','other')])
    if import_first:doc=imported(generate(doc,CAP))
    changed,summary=plan(doc,'backend_app');output=generate(changed,CAP)
    assert summary['domains']==['app.example.com'] and 'backend_app' not in names(output)
    again=imported(output);assert [h.id for h in again.hosts]==['other']


@pytest.mark.parametrize('rule',[' acl alive nbsrv(web) gt 0', ' server shadow 127.0.0.1:8083 track web/app',' use_backend %[var(txn.pool)]',' use_backend %[req.hdr(host),map(/unknown.map)]'])
def test_opaque_references_are_blocked_without_mutating_document(rule):
    doc=imported(CONFIG.replace(' # keep this comment',' # keep this comment\n'+rule));before=doc.model_dump()
    with pytest.raises(ValueError,match='Texteditor'):plan(doc,'web')
    assert doc.model_dump()==before


def test_unrelated_comments_and_substring_names_do_not_block_deletion():
    doc=imported(CONFIG.replace(' # keep this comment',' # web is no longer needed\n http-request set-header X-Test web-other'))
    changed,_=plan(doc,'web');assert 'web' not in names(generate(changed,CAP))


def test_auth_metadata_cannot_resurrect_deleted_pool():
    doc=imported();doc.imported_routes[0].basic_auth_group=1
    groups={1:{'id':1,'realm':'Members','users':[{'username':'u','hash':'$6$test$hash'}]}}
    doc=imported(strip_document_metadata(generate(doc,CAP,groups)))
    changed,_=plan(doc,'web');assert basic_auth.ids(changed)==set()
    result=generate(changed,CAP,{})
    assert basic_auth.read_metadata(result) is None and 'mgmt_basic_g1' not in result and 'web' not in names(result)


def test_tls_metadata_of_deleted_listen_is_removed_but_other_frontends_remain():
    config=CONFIG.replace('frontend incoming','listen incoming').replace(' bind :8088',' bind :8088 ssl crt /etc/certs/old.pem\n server local 127.0.0.1:8082 check')
    config+='frontend secure_other\n bind :8443 ssl crt /etc/certs/old.pem\n acl secure hdr(host) -i secure.example.com\n use_backend other if secure\n'
    doc=imported(config);doc.frontend_certificates={'incoming':['new']}
    for route in doc.imported_routes:route.certificate='new'
    doc=imported(strip_document_metadata(generate(doc,CAP)));assert len(tls_bindings.read(doc.imported_config))==2
    changed,_=plan(doc,'incoming');result=generate(changed,CAP)
    assert [p['frontend'] for p in tls_bindings.read(result)]==['secure_other'] and 'listen incoming' not in result


def test_api_preview_versioned_delete_and_isolation_never_contacts_agent(api):
    c,factory,state=api
    from backend.db import Instance
    with factory() as db:
        db.get(Instance,1).document=imported().model_dump();db.commit()
        baseline=db.get(Instance,1).document
    response=c.post('/api/instances/1/backend-delete-preview',json={'name':'web','version':0})
    assert response.status_code==200,response.text
    assert response.json()['domains']==['app.example.com','www.example.com']
    with factory() as db:assert db.get(Instance,1).document==baseline and db.get(Instance,1).document_version==0
    result=c.post('/api/instances/1/backend-delete',json={'name':'web','version':0});assert result.status_code==200,result.text
    assert result.json()['version']==1 and result.json()['removed_backends']==['web']
    with factory() as db:
        assert db.get(Instance,2).document_version==0
        assert db.get(Instance,1).document['imported_active_hash']==baseline['imported_active_hash']
    assert state['calls']==[]
    stale=c.post('/api/instances/1/backend-delete',json={'name':'db','version':0});assert stale.status_code==409
    with factory() as db:assert db.get(Instance,1).document_version==1
    assert c.post('/api/instances/1/backend-delete',json={'name':'does-not-exist','version':1}).status_code==422


def test_api_viewer_cannot_preview_or_delete(api):
    from tests.test_import_api import PASSWORD
    c,_,state=api
    login=c.post('/api/auth/login',json={'username':'viewer','password':PASSWORD});c.headers['X-CSRF-Token']=login.json()['csrf']
    for action in ('backend-delete-preview','backend-delete'):
        assert c.post('/api/instances/1/'+action,json={'name':'web','version':0}).status_code==403
    assert state['calls']==[]


def test_layout_fallback_does_not_restore_deleted_pools(api,monkeypatch):
    from backend.db import Instance
    from backend import main
    from backend.schemas import ManagedFrontend
    c,factory,state=api
    changed,_=plan(imported(),'web')
    changed.frontends=[ManagedFrontend(name='unfinished',mode='tcp',port=9999)]
    with factory() as db:
        db.get(Instance,1).document=changed.model_dump();db.commit()
    monkeypatch.setattr(main,'agent',lambda *args,**kwargs:CAP)
    result=c.get('/api/instances/1/proxy-layout');assert result.status_code==200,result.text
    value=result.json();assert value['error'] and 'web' not in {b['name'] for b in value['backends']}
    assert not any(r['backend']=='web' for f in value['frontends'] for r in f['routes'])
    with factory() as db:assert db.get(Instance,1).document_version==0
    assert state['calls']==[]


@pytest.mark.parametrize('names',[['web','web'],['web\nbackend injected'],['%[var(txn.pool)]']])
def test_tombstones_reject_invalid_names(names):
    with pytest.raises(ValueError):Document(removed_backends=names)


def test_unacknowledged_typed_backend_removal_still_requires_delete_workflow():
    doc=imported();doc.imported_backends=doc.imported_backends[1:]
    with pytest.raises(ValueError,match='Löschfunktion'):generate(doc,CAP)


def test_listen_deletion_rebuilds_managed_auth_for_other_backends():
    config=CONFIG.replace('frontend incoming','listen incoming').replace(' bind :8088',' bind :8088\n server local 127.0.0.1:8082 check')
    doc=imported(config)
    for route in doc.imported_routes:route.basic_auth_group=1
    groups={1:{'id':1,'realm':'Members','users':[{'username':'u','hash':'$6$test$hash'}]}}
    doc=imported(strip_document_metadata(generate(doc,CAP,groups)))
    changed,summary=plan(doc,'incoming');output=generate(changed,CAP,{})
    assert summary['listeners']==['incoming'] and basic_auth.ids(changed)==set()
    assert 'fe_name -m str incoming' not in output and 'mgmt_basic_g1' not in output
    assert 'other' in names(output) and 'incoming' not in names(output)
