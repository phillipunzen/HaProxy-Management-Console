"""File-import failures, legacy agent recovery and complete draft snapshots."""
import hashlib
from collections import defaultdict,deque
import os
from pathlib import Path

if not Path('.env').exists():
    from cryptography.fernet import Fernet
    for key,value in {'DB_PASSWORD':'test','ENCRYPTION_KEY':Fernet.generate_key().decode(),'SESSION_SECRET':'s'*48,'ADMIN_PASSWORD':'test-password'}.items():os.environ.setdefault(key,value)

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from backend import main
from backend.db import Base,Instance,User
from backend.schemas import Document
from agent import main as agent

PASSWORD='test-password-123'
PRIMARY='''defaults
 mode http
frontend incoming
 bind :8080
 use_backend %[req.hdr(host),lower,map(/etc/haproxy/maps/vhosts.map,default_pool)]
frontend mysql
 bind :3306
 mode tcp
 default_backend db
'''
EXTRA='''backend web
 server app 192.0.2.10:8080 check
backend default_pool
 http-request return status 404
backend db
 mode tcp
 server database 192.0.2.11:3306 check
'''
MAP='app.example.com web\n'
sha=lambda text:hashlib.sha256(text.encode()).hexdigest()


def bundle():
    return {'config':PRIMARY+'\n'+EXTRA,'hash':sha(PRIMARY),'complete':True,'warnings':[],
            'sources':[{'path':'/etc/haproxy/haproxy.cfg','hash':sha(PRIMARY)},
                       {'path':'/etc/haproxy/conf.d/pools.cfg','hash':sha(EXTRA)}],
            'maps':[{'path':'/etc/haproxy/maps/vhosts.map','host_path':'/srv/haproxy/maps/vhosts.map','content':MAP,'hash':sha(MAP)}]}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(main,'failures',defaultdict(deque))
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine);factory=sessionmaker(engine,expire_on_commit=False)
    with factory() as db:
        hashed=main.ph.hash(PASSWORD)
        db.add_all([User(username=role,password_hash=hashed,role=role,must_change_password=False) for role in ('admin','operator','viewer')])
        db.add_all([Instance(name=f'Edge {n}',agent_url=f'http://192.0.2.{n}:9101',profile=f'edge-{n}',token_cipher=main.cipher.encrypt(b't'*48).decode(),document=Document().model_dump()) for n in (1,2)])
        db.commit()
    def get_db():
        with factory() as db:yield db
    main.app.dependency_overrides[main.get_db]=get_db
    client=TestClient(main.app,base_url='https://testserver')
    login=client.post('/api/auth/login',json={'username':'admin','password':PASSWORD});assert login.status_code==200
    client.headers['X-CSRF-Token']=login.json()['csrf']
    # Real HTTP request/response handling, without contacting any HAProxy server.
    real_client=httpx.Client;state={'bundle':bundle(),'status':200,'detail':'','calls':[],'network':False}
    def response(request):
        state['calls'].append((request.method,str(request.url)))
        assert request.headers['Authorization']=='Bearer '+'t'*48
        if request.url.path.endswith('/config-bundle'):
            if state['network']:raise httpx.ConnectError('test connection failure',request=request)
            return httpx.Response(state['status'],json=state['bundle'] if state['status']==200 else {'detail':state['detail']})
        assert request.url.path.endswith('/config')
        return httpx.Response(200,json={'config':PRIMARY,'hash':sha(PRIMARY)})
    monkeypatch.setattr(main.httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(response),**kwargs))
    yield client,factory,state
    client.close();main.app.dependency_overrides.clear();engine.dispose()


@pytest.mark.parametrize('status',[404,405])
def test_missing_bundle_endpoint_offers_update_without_changing_draft(api,status):
    c,factory,state=api;state.update(status=status,detail='Not Found')
    result=c.post('/api/instances/1/import-preview',json={})
    assert result.status_code==422 and result.json()['code']=='agent_update_required'
    assert isinstance(result.json()['detail'],str) and '/config-bundle fehlt' in result.json()['detail']
    assert result.headers['Cache-Control']=='no-store'
    assert len(state['calls'])==1
    update=c.post('/api/instances/1/agent-update',json={})
    assert update.status_code==200 and 'HAPROXY_AGENT_UPDATE_ONLY=1' in update.json()['command']
    with factory() as db:assert db.get(Instance,1).document==Document().model_dump()


@pytest.mark.parametrize('status,detail',[(422,'config_path gehört nicht zu den geladenen Dateien.'),(422,'Datei /srv/pools.cfg liegt außerhalb der Docker-Mounts.'),(422,'Permission denied (/srv/pools.cfg)'),(409,'Auf dieser Instanz läuft bereits eine Änderung.'),(403,'Dateizugriff verweigert.'),(502,'Agent nicht erreichbar.'),(504,'Zeitüberschreitung beim Docker-Aufruf.'),(500,'Interner Agent-Fehler.')])
@pytest.mark.parametrize('manual',[False,True])
def test_actual_bundle_failures_are_not_mislabeled_or_bypassed(api,status,detail,manual):
    c,factory,state=api;state.update(status=status,detail=detail)
    result=c.post('/api/instances/1/import-preview',json={'config':PRIMARY+EXTRA} if manual else {})
    assert result.status_code==(502 if status==500 else status)
    assert detail in result.json()['detail'] and 'Datei-Import fehlgeschlagen' in result.json()['detail']
    assert 'agent_update_required' not in result.text and len(state['calls'])==1
    with factory() as db:assert db.get(Instance,1).document_version==0


def test_remote_agent_authentication_does_not_end_management_session(api):
    c,_,state=api;state.update(status=401,detail='Agent-Zugriff verweigert.')
    result=c.post('/api/instances/1/import-preview',json={})
    assert result.status_code==502 and 'Profilname und Agent-Token' in result.json()['detail']
    assert c.get('/api/auth/me').status_code==200


def test_network_failure_is_shown_without_legacy_fallback(api):
    c,_,state=api;state['network']=True
    result=c.post('/api/instances/1/import-preview',json={'config':PRIMARY+EXTRA})
    assert result.status_code==502 and 'Agent nicht erreichbar' in result.json()['detail']
    assert len(state['calls'])==1


def test_legacy_upload_allows_preview_but_never_untracked_commit(api):
    c,factory,state=api;state.update(status=404,detail='Not Found')
    payload={'config':PRIMARY+EXTRA,'maps':[{'path':'/etc/haproxy/maps/vhosts.map','content':MAP}]}
    result=c.post('/api/instances/1/import-preview',json=payload);assert result.status_code==200,result.text
    preview=result.json();assert preview['agent_update_required'] and not preview['can_import']
    assert preview['source_files']==[] and len(preview['document']['imported_routes'])==1
    commit=c.post('/api/instances/1/import',json={**payload,**{key:preview[key] for key in ('active_hash','preview_hash','document_version')}})
    assert commit.status_code==422 and commit.json()['code']=='agent_update_required'
    with factory() as db:assert db.get(Instance,1).document_version==0
    state.update(status=200);retry=c.post('/api/instances/1/import-preview',json=payload)
    assert retry.status_code==200 and retry.json()['can_import'] and not retry.json()['agent_update_required']


def test_multifile_import_tracks_files_maps_and_only_changes_target_draft(api):
    c,factory,state=api
    response=c.post('/api/instances/1/import-preview',json={});assert response.status_code==200,response.text
    preview=response.json();assert preview['can_import'] and not preview['agent_update_required']
    assert len(preview['source_files'])==2 and preview['summary']['tcp']==2
    assert preview['document']['imported_sources']==state['bundle']['sources']
    assert preview['document']['imported_map_hashes']==[{'path':'/srv/haproxy/maps/vhosts.map','hash':sha(MAP)}]
    assert preview['document']['imported_maps']==[{'path':'/etc/haproxy/maps/vhosts.map','content':MAP}]
    assert preview['document']['imported_routes'][0]['domain']=='app.example.com'
    result=c.post('/api/instances/1/import',json={key:preview[key] for key in ('active_hash','preview_hash','document_version')})
    assert result.status_code==200,result.text
    with factory() as db:
        assert db.get(Instance,1).document_version==1 and db.get(Instance,1).document==preview['document']
        assert db.get(Instance,2).document_version==0 and db.get(Instance,2).document==Document().model_dump()
    assert all(method=='GET' and url.endswith('/profiles/edge-1/config-bundle') for method,url in state['calls'])


@pytest.mark.parametrize('change',['primary','source','map','draft'])
def test_import_rejects_changes_after_preview(api,change):
    c,factory,state=api;preview=c.post('/api/instances/1/import-preview',json={}).json()
    if change=='primary':state['bundle']['hash']=sha('changed')
    elif change=='source':state['bundle']['sources'][1]['hash']=sha('changed')
    elif change=='map':state['bundle']['maps'][0]['hash']=sha('changed')
    else:
        with factory() as db:db.get(Instance,1).document_version=1;db.commit()
    result=c.post('/api/instances/1/import',json={key:preview[key] for key in ('active_hash','preview_hash','document_version')})
    assert result.status_code==409,result.text
    with factory() as db:assert db.get(Instance,1).document==Document().model_dump()


@pytest.mark.parametrize('manual',[False,True])
def test_incomplete_sources_are_never_committed(api,manual):
    c,_,state=api;state['bundle']['complete']=False
    result=c.post('/api/instances/1/import-preview',json={'config':PRIMARY+EXTRA} if manual else {})
    assert result.status_code==422 and 'config_sources' in result.json()['detail']
    assert 'agent_update_required' not in result.text


@pytest.mark.parametrize('role',['operator','viewer'])
def test_import_and_updater_roles_and_csrf(api,role):
    c,_,state=api
    login=c.post('/api/auth/login',json={'username':role,'password':PASSWORD});assert login.status_code==200
    c.headers['X-CSRF-Token']=login.json()['csrf']
    response=c.post('/api/instances/1/import-preview',json={});assert response.status_code==(200 if role=='operator' else 403)
    assert c.post('/api/instances/1/agent-update',json={}).status_code==403
    count=len(state['calls']);c.headers.pop('X-CSRF-Token')
    assert c.post('/api/instances/1/import-preview',json={}).status_code==403
    assert len(state['calls'])==count
    c.cookies.clear();assert c.post('/api/instances/1/import-preview',json={}).status_code==401


@pytest.mark.parametrize('error,expected',[(PermissionError(13,'Permission denied','/srv/private/pools.cfg'),'Leserechte'),(UnicodeDecodeError('utf-8',b'\xff',0,1,'bad byte'),'UTF-8')])
def test_agent_reports_unreadable_bundle_as_file_error(monkeypatch,tmp_path,error,expected):
    def fail(*args):raise error
    monkeypatch.setattr(agent,'read_bundle',fail)
    monkeypatch.setattr(agent,'STATE_DIR',tmp_path)
    profile={'config_path':str(tmp_path/'haproxy.cfg')}
    with pytest.raises(HTTPException) as raised:agent.config_bundle('edge',profile)
    assert raised.value.status_code==422 and expected in raised.value.detail


def test_html_revalidates_while_api_and_assets_keep_their_cache_policy():
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse,JSONResponse,Response
    app=FastAPI()
    app.middleware('http')(main.security_headers)
    @app.get('/{path:path}')
    def response(path):
        if path.startswith('api/'):
            return JSONResponse({'detail':'Denied'},status_code=401)
        if path.startswith('assets/'):
            return Response('export {}',media_type='text/javascript',headers={'Cache-Control':'public, max-age=31536000, immutable'})
        return HTMLResponse('<html>New frontend</html>')
    with TestClient(app) as client:
        for path in ('/','/index.html','/config'):
            result=client.get(path);assert result.status_code==200 and result.headers['Cache-Control']=='no-cache'
        assert client.get('/api/auth/me').headers['Cache-Control']=='no-store'
        assert client.get('/assets/versioned.js').headers['Cache-Control']=='public, max-age=31536000, immutable'

@pytest.fixture
def sync_api(api,monkeypatch):
    from backend.db import BasicAuthGroup
    c,factory,_=api
    cap={'kind':'native','runtime_socket_config':'/run/edge/admin.sock','cert_dir_config':'/srv/edge/certs','tls_site_bindings':True,'config_bundle':True}
    original=PRIMARY.replace(' bind :8080',' bind :8080\n bind :443 ssl crt /srv/edge/certs/old.pem')
    state={'active':original,'extra':EXTRA,'map':MAP,'calls':[],'fail':False,'read_error':False,'after_apply':None,'applied':0,'cap':cap}
    def current_bundle():
        return {'config':state['active']+'\n'+state['extra'],'hash':sha(state['active']),'complete':True,'warnings':[],
                'sources':[{'path':'/etc/haproxy/haproxy.cfg','hash':sha(state['active'])},{'path':'/etc/haproxy/conf.d/pools.cfg','hash':sha(state['extra'])}],
                'maps':[{'path':'/etc/haproxy/maps/vhosts.map','host_path':'/srv/haproxy/maps/vhosts.map','content':state['map'],'hash':sha(state['map'])}]}
    def remote(i,path='',method='GET',body=None,timeout=10):
        state['calls'].append((i.id,path,method))
        if state['applied'] and state['read_error'] and path in ('/config','/config-bundle'):raise HTTPException(502,'Agent nicht erreichbar.')
        if not path:return state['cap']
        if path=='/config':return {'config':state['active'],'hash':sha(state['active'])}
        if path=='/config-bundle':return current_bundle()
        if path=='/certificates':return [{'name':'site','staging':False,'days_remaining':60,'domains':['app.example.com','new.example.com','www.example.com','www.new.example.com']}]
        if path=='/validate':return {'valid':True}
        if path=='/apply':
            if state['fail']:raise HTTPException(422,'Konfiguration ungültig.')
            assert body['expected_hash']==sha(state['active'])
            state['active']=body['config'];state['extra']='# Consolidated into /etc/haproxy/haproxy.cfg by HAProxy Control\n';state['applied']+=1
            bundle=current_bundle();result={'applied':True,'hash':sha(state['active']),'sources':bundle['sources'],'map_hashes':[{'path':m['host_path'],'hash':m['hash']} for m in bundle['maps']]}
            if state['after_apply']:state['after_apply']()
            return result
        pytest.fail(f'Unexpected agent call {path}')
    monkeypatch.setattr(main,'agent',remote)
    with factory() as db:db.add(BasicAuthGroup(name='Paywall',realm='Restricted'));db.commit()
    preview=c.post('/api/instances/1/import-preview',json={}).json()
    response=c.post('/api/instances/1/import',json={key:preview[key] for key in ('active_hash','preview_hash','document_version')});assert response.status_code==200,response.text
    return c,factory,state


def apply_generated(c):
    result=c.post('/api/instances/1/generate',json={});assert result.status_code==200,result.text
    generated=result.json()
    revision=c.post('/api/instances/1/revisions',json={'config':generated['config'],'base_hash':generated['base_hash'],'message':'Test apply'}).json()['id']
    result=c.post(f'/api/instances/1/revisions/{revision}/apply',json={'document_version':generated['document_version']})
    return result,generated,revision


@pytest.mark.parametrize('kind',['native','docker'])
def test_repeated_apply_keeps_graphical_hosts_auth_tls_and_updates_all_hashes(sync_api,kind):
    c,factory,state=sync_api;state['cap']['kind']=kind
    doc=c.get('/api/instances/1/document').json()
    doc['imported_routes'][0].update(certificate='site',basic_auth_group=1,aliases=['www.example.com'])
    doc['hosts']=[{'aliases':['www.new.example.com'],'id':'newsite','frontend':'incoming','domain':'new.example.com','certificate':'site','basic_auth_group':1,'servers':[{'address':'192.0.2.30','port':8080}]}]
    doc['backends']=[{'name':'new_db','mode':'tcp','servers':[{'address':'192.0.2.31','port':3306}]}]
    doc['frontends']=[{'name':'new_tcp','mode':'tcp','port':3307,'backend':'new_db'}]
    saved=c.put('/api/instances/1/document',json=doc);assert saved.status_code==200,saved.text
    for port in (8081,8082,8083):
        doc=c.get('/api/instances/1/document').json();doc['imported_backends'][0]['servers'][0]['port']=port
        saved=c.put('/api/instances/1/document',json=doc);assert saved.status_code==200,saved.text
        result,generated,_=apply_generated(c);assert result.status_code==200,result.text
        assert result.json()['document_synced'] and not result.json()['sync_warning']
        refreshed=c.get('/api/instances/1/document').json()
        assert refreshed['imported_config']==saved.json()['imported_config']
        assert refreshed['hosts']==saved.json()['hosts'] and refreshed['frontends']==saved.json()['frontends'] and refreshed['backends']==saved.json()['backends']
        assert refreshed['imported_routes'][0]['certificate']=='site' and refreshed['imported_routes'][0]['basic_auth_group']==1
        assert refreshed['imported_active_hash']==sha(state['active'])
        assert refreshed['imported_sources'][1]['hash']==sha(state['extra']) and refreshed['version']==saved.json()['version']+1
        next_config=c.post('/api/instances/1/generate',json={});assert next_config.status_code==200,next_config.text
        assert next_config.json()['config'].splitlines().count('backend backend_newsite')==1
        assert next_config.json()['config'].splitlines().count('frontend new_tcp')==1
    with factory() as db:assert db.get(Instance,2).document_version==0


def test_manual_text_apply_reimports_backend_targets_and_can_generate_again(sync_api):
    c,_,state=sync_api
    config=c.post('/api/instances/1/generate',json={}).json()['config'].replace('192.0.2.10:8080','192.0.2.50:9090')
    revision=c.post('/api/instances/1/revisions',json={'config':config,'base_hash':sha(state['active'])}).json()['id']
    result=c.post(f'/api/instances/1/revisions/{revision}/apply',json={});assert result.status_code==200,result.text
    assert result.json()['document_synced']
    doc=c.get('/api/instances/1/document').json()
    assert doc['imported_backends'][0]['servers'][0]['address']=='192.0.2.50' and doc['imported_backends'][0]['servers'][0]['port']==9090
    assert any(b['name']=='db' and b['mode']=='tcp' for b in doc['imported_backends'])
    assert c.post('/api/instances/1/generate',json={}).status_code==200


def test_failed_apply_does_not_advance_graphical_base(sync_api):
    c,factory,state=sync_api;before=c.get('/api/instances/1/document').json();state['fail']=True
    result,_,revision=apply_generated(c);assert result.status_code==422
    assert c.get('/api/instances/1/document').json()==before
    from backend.db import Revision
    with factory() as db:assert db.get(Revision,revision).status=='failed'


def test_apply_rejects_stale_graphical_version_before_mutation(sync_api):
    c,_,state=sync_api;generated=c.post('/api/instances/1/generate',json={}).json()
    revision=c.post('/api/instances/1/revisions',json={'config':generated['config'],'base_hash':generated['base_hash']}).json()['id']
    doc=c.get('/api/instances/1/document').json();doc['imported_backends'][0]['servers'][0]['port']=9080
    assert c.put('/api/instances/1/document',json=doc).status_code==200
    result=c.post(f'/api/instances/1/revisions/{revision}/apply',json={'document_version':generated['document_version']})
    assert result.status_code==409 and state['applied']==0


@pytest.mark.parametrize('change',['draft','active','map','source','network'])
def test_apply_preserves_concurrent_changes_and_reports_sync_warning(sync_api,change):
    from backend.db import Revision
    c,factory,state=sync_api;original=c.get('/api/instances/1/document').json()
    def changed():
        if change=='draft':
            with factory() as db:
                i=db.get(Instance,1);doc=dict(i.document);doc['maxconn']=4097;i.document=doc;i.document_version+=1;db.commit()
        elif change=='active':state['active']+='\n# external edit\n'
        elif change=='map':state['map']+='second.example.com web\n'
        elif change=='source':state['extra']+='\n# external edit\n'
        else:state['read_error']=True
    state['after_apply']=changed
    result,_,revision=apply_generated(c);assert result.status_code==200,result.text
    assert result.json()['applied'] and not result.json()['document_synced'] and result.json()['sync_warning']
    with factory() as db:
        assert db.get(Revision,revision).status=='applied'
        assert db.get(Instance,1).document['imported_active_hash']==original['imported_active_hash']
        if change=='draft':assert db.get(Instance,1).document['maxconn']==4097


def test_old_management_stale_base_is_repaired_on_open_without_reimport(sync_api):
    c,factory,state=sync_api
    doc=c.get('/api/instances/1/document').json();doc['imported_routes'][0]['certificate']='site'
    saved=c.put('/api/instances/1/document',json=doc);assert saved.status_code==200
    before=saved.json();result,_,_=apply_generated(c);assert result.status_code==200
    with factory() as db:
        i=db.get(Instance,1);i.document={key:value for key,value in before.items() if key not in ('version','basic_auth_existing')};i.document_version=before['version'];db.commit()
    repaired=c.get('/api/instances/1/document').json()
    assert repaired['version']==before['version']+1 and repaired['imported_active_hash']==sha(state['active'])
    assert repaired['imported_routes'][0]['certificate']=='site'
    assert c.post('/api/instances/1/generate',json={}).status_code==200


def test_text_edit_keeps_central_auth_and_sni_assignments_on_regeneration(sync_api):
    from backend.basic_auth import read_metadata
    from backend.tls_bindings import read as tls_plans
    c,_,state=sync_api;doc=c.get('/api/instances/1/document').json()
    doc['imported_routes'][0].update(certificate='site',basic_auth_group=1)
    doc['hosts']=[{'id':'private','frontend':'incoming','domain':'new.example.com','path':'/private','certificate':'site','basic_auth_group':1,'servers':[{'address':'192.0.2.30','port':8080}]}]
    assert c.put('/api/instances/1/document',json=doc).status_code==200
    result,_,_=apply_generated(c);assert result.status_code==200 and result.json()['document_synced']
    generated=c.post('/api/instances/1/generate',json={}).json()
    config=generated['config'].replace('192.0.2.10:8080','192.0.2.55:8085')
    revision=c.post('/api/instances/1/revisions',json={'config':config,'base_hash':generated['base_hash']}).json()['id']
    result=c.post(f'/api/instances/1/revisions/{revision}/apply',json={});assert result.status_code==200,result.text
    assert result.json()['document_synced']
    regenerated=c.post('/api/instances/1/generate',json={});assert regenerated.status_code==200,regenerated.text
    assert read_metadata(regenerated.json()['config'])['sites']==read_metadata(config)['sites']
    assert tls_plans(regenerated.json()['config'])[0]['sites']==tls_plans(config)[0]['sites']
    assert '192.0.2.55:8085' in regenerated.json()['config']


def test_old_base_recovery_does_not_adopt_external_secondary_edits(sync_api):
    c,factory,state=sync_api;before=c.get('/api/instances/1/document').json()
    result,_,_=apply_generated(c);assert result.status_code==200
    with factory() as db:
        i=db.get(Instance,1);i.document={key:value for key,value in before.items() if key not in ('version','basic_auth_existing')};i.document_version=before['version'];db.commit()
    state['extra']+='\n# external change\n'
    after=c.get('/api/instances/1/document').json();assert after==before
    assert c.post('/api/instances/1/generate',json={}).status_code==409


def test_management_rejects_selected_certificate_missing_alias(sync_api):
    c,_,_=sync_api;doc=c.get('/api/instances/1/document').json()
    doc['imported_routes'][0].update(certificate='site',aliases=['not-covered.example.net'])
    result=c.put('/api/instances/1/document',json=doc)
    assert result.status_code==200,result.text
    result=c.post('/api/instances/1/generate',json={})
    assert result.status_code==422 and 'not-covered.example.net' in result.text


def test_reading_old_draft_exposes_original_backend_verification_without_mutating_it(api):
    c,factory,_=api
    config='defaults\n mode http\nbackend web\n server origin 192.0.2.1:443 ssl verify none\n'
    draft=main.import_config(config,sha(config))['document']
    draft['imported_backends'][0]['servers'][0].pop('tls_verify')
    with factory() as db:
        db.get(Instance,1).document=draft;db.commit()
    result=c.get('/api/instances/1/document');assert result.status_code==200,result.text
    assert result.json()['imported_backends'][0]['servers'][0]['tls_verify'] is False
    with factory() as db:
        assert db.get(Instance,1).document==draft and db.get(Instance,1).document_version==0


@pytest.mark.parametrize('legacy',[False,True])
def test_repeated_import_and_apply_keeps_created_hosts_and_live_provenance(sync_api,legacy):
    from backend.haproxy_config import strip_document_metadata
    c,factory,state=sync_api;doc=c.get('/api/instances/1/document').json()
    doc['hosts']=[{'id':'wiki','frontend':'incoming','domain':'new.example.com','aliases':['www.new.example.com'],'path':'/wiki','certificate':'site','basic_auth_group':1,'servers':[{'address':'192.0.2.70','port':443,'tls':True,'tls_verify':False}]}]
    assert c.put('/api/instances/1/document',json=doc).status_code==200
    result,_,_=apply_generated(c);assert result.status_code==200 and result.json()['document_synced']
    if legacy:state['active']=strip_document_metadata(state['active'])
    for port in (8443,9443):
        before=c.get('/api/instances/1/document').json()
        preview=c.post('/api/instances/1/import-preview',json={});assert preview.status_code==200,preview.text
        assert c.get('/api/instances/1/document').json()==before
        preview=preview.json();assert preview['summary']['managed_hosts']==1
        result=c.post('/api/instances/1/import',json={k:preview[k] for k in ('active_hash','preview_hash','document_version')})
        assert result.status_code==200,result.text
        imported=result.json();assert imported['hosts'][0]['id']=='wiki'
        assert imported['hosts'][0]['certificate']=='site' and imported['hosts'][0]['basic_auth_group']==1
        assert imported['hosts'][0]['aliases']==['www.new.example.com'] and imported['hosts'][0]['servers'][0]['tls_verify'] is False
        imported['hosts'][0]['servers'][0]['port']=port
        assert c.put('/api/instances/1/document',json=imported).status_code==200
        result,generated,_=apply_generated(c);assert result.status_code==200,result.text
        assert result.json()['document_synced'] and '192.0.2.70:'+str(port) in generated['config']
        assert generated['config'].splitlines().count('backend backend_wiki')==1
    with factory() as db:assert db.get(Instance,2).document_version==0


def test_import_preview_warns_about_unapplied_edits_without_replacing_them(sync_api):
    c,_,_=sync_api;doc=c.get('/api/instances/1/document').json()
    doc['hosts']=[{'id':'pending','frontend':'incoming','domain':'new.example.com','servers':[{'address':'192.0.2.70'}]}]
    saved=c.put('/api/instances/1/document',json=doc).json()
    preview=c.post('/api/instances/1/import-preview',json={}).json()
    assert any('Noch nicht angewendete' in w for w in preview['warnings'])
    assert c.get('/api/instances/1/document').json()==saved
