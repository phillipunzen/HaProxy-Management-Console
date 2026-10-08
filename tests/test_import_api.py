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
