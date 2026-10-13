"""Listener deletion is scoped, versioned and survives generation/import."""
import hashlib

import pytest
from backend.frontend_delete import plan
from backend.generator import generate
from backend.haproxy_config import import_config,parse_sections,strip_document_metadata
from backend.schemas import Document,Host,Rule,ImportedSource,ManagedFrontend,AccessProfile
from backend import basic_auth,tls_bindings,access_control
from tests.test_backend_delete import CONFIG,CAP
from tests.test_import_api import api


def imported(config=CONFIG,maps=()):
    return Document.model_validate(import_config(config,hashlib.sha256(config.encode()).hexdigest(),list(maps))['document'])


def fronts(config):return {s.name for s in parse_sections(config)[1] if s.kind in ('frontend','listen')}
def pools(config):return {s.name for s in parse_sections(config)[1] if s.kind in ('backend','listen')}


@pytest.mark.parametrize('ending',['\n','\r\n'])
@pytest.mark.parametrize('routing',['acl','map'])
def test_imported_frontend_removes_only_its_sites_and_rules_keeps_shared_backends_and_baseline(ending,routing):
    config=CONFIG+'frontend shared\n bind :8089\n acl other hdr(host) -i shared.example.com\n use_backend web if other\n'
    maps=[]
    if routing=='map':
        config=config.replace(' acl app hdr(host) -i app.example.com www.example.com\n use_backend web if app\n acl route_other hdr(host) -i other.example.com\n use_backend other if route_other\n default_backend web',' use_backend %[req.hdr(host),lower,map(/etc/vhosts.map,web)]')
        maps=[{'path':'/etc/vhosts.map','content':'app.example.com web\nwww.example.com web\nother.example.com other\n'}]
    doc=imported(config.replace('\n',ending),maps)
    doc.imported_sources=[ImportedSource(path='/etc/haproxy.cfg',hash='a'*64)]
    doc.hosts=[Host(id='extra',frontend='incoming',domain='extra.example.com',aliases=['extra-alias.example.com'],servers=[{'address':'127.0.0.1'}]),Host(id='disabled',frontend='incoming',domain='disabled.example.com',enabled=False,servers=[{'address':'127.0.0.1'}])]
    doc.rules=[Rule(id='r',name='Old redirect',frontend='incoming',match='host',value='redirect.example.com',action='redirect',target='https://new.example.com')]
    next(b for b in doc.imported_backends if b.name=='web').servers[0].port=9099
    before=doc.model_dump();changed,summary=plan(doc,'incoming');output=generate(changed,CAP)
    assert set(summary['domains'])=={'extra.example.com','extra-alias.example.com','disabled.example.com','app.example.com','www.example.com','other.example.com'}
    assert summary['rules']==['Old redirect'] and not summary['backend_removed']
    assert changed.removed_frontends==['incoming'] and not changed.removed_backends
    assert fronts(output)=={'mysql','shared'} and pools(output)=={'web','other','db'}
    assert 'shared.example.com' in output and 'server app 127.0.0.1:9099 check' in output
    assert not changed.hosts and not changed.rules and len(changed.imported_routes)==1
    assert changed.imported_config==before['imported_config'] and changed.imported_active_hash==before['imported_active_hash'] and changed.imported_sources==doc.imported_sources and changed.imported_maps==doc.imported_maps
    assert doc.model_dump()==before
    again=imported(output,maps);assert fronts(generate(again,CAP))=={'mysql','shared'} and pools(generate(again,CAP))=={'web','other','db'}


def test_imported_tcp_frontend_deletion_leaves_tcp_backend_and_other_listener():
    changed,summary=plan(imported(),'mysql');output=generate(changed,CAP)
    assert summary['domains']==[] and fronts(output)=={'incoming'}
    assert pools(output)=={'web','other','db'} and 'server database' in output


def test_native_managed_listener_removes_disabled_hosts_and_rules_but_keeps_pools():
    doc=Document(frontends=[{'name':'site','mode':'http','port':8080},{'name':'sql','mode':'tcp','port':3306,'backend':'db'}],backends=[{'name':'db','mode':'tcp','balance':'first','servers':[{'address':'127.0.0.1'}]}],hosts=[{'id':'app','frontend':'site','domain':'app.example.com','servers':[{'address':'127.0.0.1'}]}])
    changed,_=plan(doc,'site');assert fronts(generate(changed,CAP))=={'public_http','sql'} and pools(generate(changed,CAP))=={'unknown_host','db'}
    changed,_=plan(changed,'sql');output=generate(changed,CAP)
    assert fronts(output)=={'public_http'} and 'balance first' in output and pools(output)=={'unknown_host','db'}


@pytest.mark.parametrize('import_first',[False,True])
def test_shared_public_http_deletion_survives_roundtrip_and_other_hosts_stay_managed(import_first):
    doc=Document(tls_enabled=True,acme_enabled=True,frontends=[{'name':'other','mode':'http','port':8081}],hosts=[{'id':'a','domain':'a.example.com','servers':[{'address':'127.0.0.1'}]},{'id':'b','domain':'b.example.com','frontend':'other','servers':[{'address':'127.0.0.1'}]}])
    if import_first:doc=imported(generate(doc,CAP))
    changed,summary=plan(doc,'public_http');output=generate(changed,CAP)
    assert summary['domains']==['a.example.com'] and fronts(output)=={'other'} and 'backend_a' not in pools(output)
    assert 'backend_b' in pools(output) and [h.id for h in changed.hosts]==['b']
    again=imported(output);assert fronts(generate(again,CAP))=={'other'} and [h.id for h in again.hosts]==['b']
    if not import_first:assert not changed.acme_enabled and not changed.tls_enabled


def test_listen_deletion_reports_combined_pool_and_additional_tcp_listener():
    config=CONFIG.replace('frontend incoming','listen incoming').replace(' bind :8088',' bind :8088\n server local 127.0.0.1:8082 check')+'frontend external\n mode http\n bind :8089\n default_backend incoming\n'
    doc=imported(config)
    changed,summary=plan(doc,'incoming');output=generate(changed,CAP)
    assert summary['backend_removed'] and 'external: default_backend incoming' in summary['references']
    assert changed.removed_frontends==['incoming'] and changed.removed_backends==['incoming']
    assert fronts(output)=={'mysql','external'} and pools(output)=={'web','other','db'} and 'default_backend incoming' not in output


@pytest.mark.parametrize('rule',[' acl alive fe_conn(incoming) gt 0',' http-request set-var(txn.listener) str(incoming)'])
def test_opaque_references_block_deletion_without_mutation(rule):
    doc=imported(CONFIG.replace('backend other\n','backend other\n'+rule+'\n'));before=doc.model_dump()
    with pytest.raises(ValueError,match='Texteditor'):plan(doc,'incoming')
    assert doc.model_dump()==before


def test_comments_and_substring_listener_names_do_not_block_deletion():
    doc=imported(CONFIG.replace('backend other\n','backend other\n # incoming is old\n http-request set-header X-Name incoming-other\n'))
    changed,_=plan(doc,'incoming');assert 'incoming' not in fronts(generate(changed,CAP))


def test_deleting_plain_listener_preserves_another_listener_tls_auth_access_and_redirects():
    config=CONFIG.replace(' bind :8088',' bind :8088 ssl crt /etc/certs/old.pem')+'frontend shared\n bind :8443 ssl crt /etc/certs/old.pem\n acl shared hdr(host) -i shared.example.com\n use_backend web if shared\n'
    doc=imported(config);doc.access_profiles=[AccessProfile(id='lan',name='LAN',networks=['192.168.10.0/24'])]
    doc=Document.model_validate(doc.model_dump())
    for r in doc.imported_routes:r.certificate='selected';r.basic_auth_group=1;r.access_profile='lan'
    doc.rules=[Rule(id='one',name='Removed rule',frontend='incoming',match='host',value='a.example.com',action='redirect',target='https://b.example.com'),Rule(id='two',name='Kept rule',frontend='shared',match='host',value='c.example.com',action='redirect',target='https://d.example.com')]
    groups={1:{'id':1,'realm':'Members','users':[{'username':'u','hash':'$6$test$hash'}]}}
    # Discard graphical snapshot to exercise conservative metadata recovery too.
    doc=imported(strip_document_metadata(generate(doc,CAP,groups)))
    changed,_=plan(doc,'incoming');output=generate(changed,CAP,groups)
    assert fronts(output)=={'mysql','shared'}
    assert [p['frontend'] for p in tls_bindings.read(output)]==['shared']
    assert {s['frontend'] for s in basic_auth.bindings(changed)}=={'shared'}
    assert {s['frontend'] for s in access_control.bindings(changed)}=={'shared'}
    assert 'Removed rule' not in [r.name for r in changed.rules] and [r.name for r in changed.rules]==['Kept rule']
    assert len(basic_auth.read_metadata(output)['sites'])==1
    assert len(access_control.read(output)['sites'])==1
    assert all(s['frontend']=='shared' for s in basic_auth.read_metadata(output)['sites'])
    assert all(s['frontend']=='shared' for s in access_control.read(output)['sites'])
    again=imported(strip_document_metadata(output));assert fronts(generate(again,CAP,groups))=={'mysql','shared'}


def test_delete_after_prior_backend_deletion_and_backend_delete_after_frontend_deletion():
    from backend.backend_delete import plan as remove_backend
    doc,_=remove_backend(imported(),'other');doc,_=plan(doc,'incoming');assert fronts(generate(doc,CAP))=={'mysql'}
    doc,_=plan(imported(),'incoming');doc,_=remove_backend(doc,'web');output=generate(doc,CAP)
    assert fronts(output)=={'mysql'} and pools(output)=={'other','db'}


@pytest.mark.parametrize('target',['does-not-exist','web'])
def test_unknown_frontend_or_backend_name_cannot_delete_a_pool(target):
    doc=imported();before=doc.model_dump()
    with pytest.raises(ValueError,match='nicht gefunden'):plan(doc,target)
    assert doc.model_dump()==before


def test_repeated_deletion_and_invalid_tombstones():
    doc,_=plan(imported(),'incoming')
    with pytest.raises(ValueError,match='bereits gelöscht'):plan(doc,'incoming')
    for names in [['one','one'],['injected\nfrontend new'],['%[var(name)]']]:
        with pytest.raises(ValueError):Document(removed_frontends=names)
    with pytest.raises(ValueError,match='gelöschtes Frontend'):Document(removed_frontends=['public_http'],hosts=[{'id':'a','domain':'a.example.com','servers':[{'address':'127.0.0.1'}]}])


def test_api_preview_versioned_delete_is_pure_and_never_contacts_agent(api):
    from backend.db import Instance
    c,factory,state=api
    with factory() as db:db.get(Instance,1).document=imported().model_dump();db.commit();before=db.get(Instance,1).document
    preview=c.post('/api/instances/1/frontend-delete-preview',json={'name':'incoming','version':0});assert preview.status_code==200,preview.text
    assert preview.json()['domains']==['app.example.com','www.example.com','other.example.com']
    with factory() as db:assert db.get(Instance,1).document==before and db.get(Instance,1).document_version==0
    deleted=c.post('/api/instances/1/frontend-delete',json={'name':'incoming','version':0});assert deleted.status_code==200,deleted.text
    assert deleted.json()['version']==1 and deleted.json()['removed_frontends']==['incoming']
    with factory() as db:assert db.get(Instance,2).document_version==0 and db.get(Instance,1).document['imported_active_hash']==before['imported_active_hash']
    assert c.post('/api/instances/1/frontend-delete',json={'name':'mysql','version':0}).status_code==409
    assert c.post('/api/instances/1/frontend-delete',json={'name':'web','version':1}).status_code==422
    assert state['calls']==[]


def test_api_viewer_and_csrf_are_required(api):
    from tests.test_import_api import PASSWORD
    c,_,state=api
    c.headers.pop('X-CSRF-Token');assert c.post('/api/instances/1/frontend-delete',json={'name':'incoming','version':0}).status_code==403
    login=c.post('/api/auth/login',json={'username':'viewer','password':PASSWORD});c.headers['X-CSRF-Token']=login.json()['csrf']
    for action in ('frontend-delete-preview','frontend-delete'):assert c.post('/api/instances/1/'+action,json={'name':'incoming','version':0}).status_code==403
    assert state['calls']==[]


def test_layout_fallback_respects_deleted_frontends(api,monkeypatch):
    from backend.db import Instance
    from backend import main
    c,factory,state=api
    changed,_=plan(imported(),'incoming');changed.frontends=[ManagedFrontend(name='unfinished',mode='tcp',port=9999)]
    with factory() as db:db.get(Instance,1).document=changed.model_dump();db.commit()
    monkeypatch.setattr(main,'agent',lambda *args,**kwargs:CAP)
    response=c.get('/api/instances/1/proxy-layout');assert response.status_code==200,response.text
    value=response.json();assert value['error'] and {f['name'] for f in value['frontends']}=={'mysql','unfinished'} and {b['name'] for b in value['backends']}=={'web','other','db'}
    assert state['calls']==[]


@pytest.mark.parametrize('import_first',[False,True])
def test_last_listener_can_be_deleted_in_draft_but_cannot_be_generated_for_apply(import_first):
    doc=Document()
    if import_first:doc=imported(generate(doc,CAP))
    changed,summary=plan(doc,'public_http');assert summary['remaining_listeners']==[]
    with pytest.raises(ValueError,match='Kein Frontend'):generate(changed,CAP)
    changed.frontends=[ManagedFrontend(name='replacement',mode='http',port=8081)]
    assert fronts(generate(changed,CAP))=={'replacement'}


def test_last_managed_listener_on_backend_only_import_cannot_be_applied_after_deletion():
    doc=imported('defaults\n mode http\nbackend web\n server origin 127.0.0.1:8080\n')
    doc.frontends=[ManagedFrontend(name='managed',mode='http',port=8081,backend='web')]
    changed,summary=plan(doc,'managed');assert summary['remaining_listeners']==[]
    from backend.frontend_delete import require_listener
    with pytest.raises(ValueError,match='Kein Frontend'):require_listener(generate(changed,CAP))


def test_generate_api_rejects_listenerless_managed_draft_before_revision_or_apply(api,monkeypatch):
    from backend.db import Instance
    from backend import main
    c,factory,state=api
    doc=imported('defaults\n mode http\nbackend web\n server origin 127.0.0.1:8080\n')
    doc.frontends=[ManagedFrontend(name='managed',mode='http',port=8081,backend='web')]
    changed,_=plan(doc,'managed')
    with factory() as db:db.get(Instance,1).document=changed.model_dump();db.commit()
    calls=[]
    def fake_agent(instance,path=None,*args,**kwargs):
        calls.append(path)
        if path is None:return CAP
        assert path=='/config'
        return {'hash':changed.imported_active_hash,'config':changed.imported_config}
    monkeypatch.setattr(main,'agent',fake_agent)
    result=c.post('/api/instances/1/generate',json={});assert result.status_code==422 and 'Kein Frontend' in result.text
    assert calls==[None,'/config'] and state['calls']==[]
