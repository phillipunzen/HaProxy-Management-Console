"""Server classifications, offline editing and compatibility with older clients."""
import os
from pathlib import Path
if not Path('.env').exists():
    from cryptography.fernet import Fernet
    for key,value in {'DB_PASSWORD':'test','ENCRYPTION_KEY':Fernet.generate_key().decode(),'SESSION_SECRET':'s'*48,'ADMIN_PASSWORD':'test-password'}.items():os.environ.setdefault(key,value)
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,event,select,func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from backend import main
from backend.db import Base,User,Instance,InstanceMetadata,Metric,MetricBucket
from backend.schemas import InstanceMetadataIn
from backend.agent_setup import AgentSetupIn,build_plan

PASSWORD='test-password-123'

@pytest.fixture
def api(monkeypatch):
    from collections import defaultdict,deque
    monkeypatch.setattr(main,'failures',defaultdict(deque))
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    @event.listens_for(engine,'connect')
    def foreign_keys(connection,record):connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine);factory=sessionmaker(engine,expire_on_commit=False)
    with factory() as db:
        db.add_all([User(username=role,password_hash=main.ph.hash(PASSWORD),role=role,must_change_password=False) for role in ('admin','operator','viewer')])
        db.add(Instance(name='Existing edge',agent_url='http://192.0.2.1:9101',profile='native',token_cipher=main.cipher.encrypt(b'x'*48).decode(),notes='Keep notes',document={'hosts':[]}));db.commit()
    def get_db():
        with factory() as db:yield db
    main.app.dependency_overrides[main.get_db]=get_db
    monkeypatch.setattr(main,'agent',lambda *a,**kw:pytest.fail('Metadata operation must not contact an agent'))
    client=TestClient(main.app,base_url='https://testserver');login=client.post('/api/auth/login',json={'username':'admin','password':PASSWORD});assert login.status_code==200
    client.headers['X-CSRF-Token']=login.json()['csrf']
    yield client,factory
    client.close();main.app.dependency_overrides.clear();engine.dispose()

def test_tag_normalization():
    value=InstanceMetadataIn(tags=[' Prod ','prod','Kunde   A','Dev','De\u0301v'],location='  Frankfurt   ·  RZ 1  ')
    assert value.tags==['Prod','Kunde A','Dev','Dév'] and value.location=='Frankfurt · RZ 1'

@pytest.mark.parametrize('values',[{'tags':'Prod'},{'tags':['']},{'tags':['a'*41]},{'tags':['Prod,Dev']},{'tags':['x\nProd']},{'tags':['x\u200b']},{'tags':[str(i) for i in range(21)]},{'location':'Berlin\x00'},{'location':'x'*121}])
def test_invalid_classifications(values):
    with pytest.raises(ValidationError):InstanceMetadataIn(**values)

def test_existing_instance_and_offline_edit(api):
    client,factory=api;old=client.get('/api/instances').json()[0]
    assert old['tags']==[] and old['location']=='' and old['metadata_version']==0
    response=client.put('/api/instances/1/metadata',json={'tags':[' Prod ','prod','Edge'],'location':'Frankfurt','metadata_version':0});assert response.status_code==200,response.text
    changed=response.json();assert changed['tags']==['Prod','Edge'] and changed['location']=='Frankfurt' and changed['metadata_version']==1
    assert changed['agent_url']==old['agent_url'] and changed['notes']=='Keep notes' and 'token' not in changed
    assert client.get('/api/instances').json()[0]['tags']==changed['tags']
    assert client.put('/api/instances/1/metadata',json={'tags':['Dev'],'metadata_version':0}).status_code==409
    assert client.put('/api/instances/1/metadata',json={'location':'Berlin','metadata_version':1}).json()['tags']==['Prod','Edge']
    cleared=client.put('/api/instances/1/metadata',json={'tags':[],'location':'','metadata_version':2});assert cleared.status_code==200 and cleared.json()['tags']==[]
    with factory() as db:
        assert db.get(Instance,1).document=={'hosts':[]}
        assert db.scalar(select(func.count()).select_from(Metric))==0 and db.scalar(select(func.count()).select_from(MetricBucket))==0

def test_metadata_validation_and_missing_instance(api):
    client,factory=api
    assert client.put('/api/instances/1/metadata',json={'tags':['x'*41]}).status_code==422
    assert client.put('/api/instances/999/metadata',json={'tags':['Prod']}).status_code==404
    with factory() as db:assert db.get(InstanceMetadata,1) is None

def test_metadata_permissions_and_csrf(api):
    client,_=api;csrf=client.headers.pop('X-CSRF-Token')
    assert client.put('/api/instances/1/metadata',json={'tags':['Prod']}).status_code==403
    client.headers['X-CSRF-Token']=csrf
    assert client.put('/api/instances/1/metadata',json={},headers={'Origin':'https://evil.example'}).status_code==403
    outsider=TestClient(main.app,base_url='https://testserver')
    try:
        assert outsider.get('/api/instances').status_code==401
        assert outsider.put('/api/instances/1/metadata',json={}).status_code==401
        for role in ('operator','viewer'):
            login=outsider.post('/api/auth/login',json={'username':role,'password':PASSWORD});assert login.status_code==200
            outsider.headers['X-CSRF-Token']=login.json()['csrf']
            assert outsider.get('/api/instances').status_code==200
            assert outsider.put('/api/instances/1/metadata',json={'tags':['Prod']}).status_code==403
    finally:outsider.close()

def test_metadata_deleted_with_instance(api):
    client,factory=api
    assert client.put('/api/instances/1/metadata',json={'tags':['Prod']}).status_code==200
    assert client.delete('/api/instances/1').status_code==200
    with factory() as db:assert db.get(InstanceMetadata,1) is None

def test_connection_create_edit_and_older_clients(api,monkeypatch):
    client,_=api;monkeypatch.setattr(main,'agent',lambda *a,**kw:{'kind':'docker'})
    payload={'name':'New edge','agent_url':'http://192.0.2.2:9101','profile':'docker-edge','token':'t'*48,'allow_http':True,'tags':['Dev'],'location':'Berlin'}
    response=client.post('/api/instances',json=payload);assert response.status_code==200,response.text
    id=response.json()['id'];assert response.json()['tags']==['Dev'] and response.json()['metadata_version']==0
    legacy={k:v for k,v in payload.items() if k not in ('tags','location')};legacy['name']='Legacy update'
    response=client.put(f'/api/instances/{id}',json=legacy);assert response.status_code==200 and response.json()['tags']==['Dev'] and response.json()['location']=='Berlin'
    response=client.put(f'/api/instances/{id}',json={**payload,'tags':['Prod'],'metadata_version':0});assert response.status_code==200 and response.json()['metadata_version']==1
    assert client.put(f'/api/instances/{id}',json={**payload,'metadata_version':0}).status_code==409

def test_setup_keeps_metadata_out_of_installation_command():
    body=AgentSetupIn(name='Edge',kind='native',host='192.168.10.71',profile='native',config_path='/etc/haproxy/haproxy.cfg',runtime_socket='/run/haproxy/admin.sock',cert_dir='/etc/haproxy/certs',allow_http=True,tags=['Prod','Customer-Label'],location='Frankfurt-RZ-1')
    plan=build_plan(body,'http://192.168.10.70:8100',Path(__file__).resolve().parents[1]);assert plan['instance']['tags']==body.tags and plan['instance']['location']==body.location
    assert 'Customer-Label' not in plan['command'] and 'Frankfurt-RZ-1' not in plan['command']

@pytest.mark.parametrize('token',[None,'','omitted'])
def test_full_server_edit_retains_token_and_works_offline(api,token):
    client,factory=api
    with factory() as db:
        i=db.get(Instance,1);i.allow_http=True;db.commit();stored=i.token_cipher
    payload={'name':'Renamed edge','agent_url':'http://192.0.2.1:9101','profile':'native','allow_http':True,'notes':'Changed offline','tags':['Prod'],'location':'Berlin'}
    if token!='omitted':payload['token']=token
    response=client.put('/api/instances/1',json=payload)
    assert response.status_code==200,response.text
    assert response.json()['name']=='Renamed edge' and response.json()['location']=='Berlin'
    assert 'token' not in response.json() and 'token_cipher' not in response.json()
    with factory() as db:assert db.get(Instance,1).token_cipher==stored


def test_connection_change_reuses_or_rotates_token(api,monkeypatch):
    client,factory=api;calls=[]
    def agent(candidate,*args,**kw):
        calls.append((candidate.agent_url,candidate.profile,main.cipher.decrypt(candidate.token_cipher.encode()).decode()))
        return {'kind':'native'}
    monkeypatch.setattr(main,'agent',agent)
    payload={'name':'Edge','agent_url':'http://192.0.2.2:9101','profile':'native','allow_http':True}
    assert client.put('/api/instances/1',json=payload).status_code==200
    assert calls[-1][2]=='x'*48
    assert client.put('/api/instances/1',json=payload|{'token':'new-token-'+'t'*48}).status_code==200
    assert calls[-1][2]=='new-token-'+'t'*48
    with factory() as db:
        assert main.cipher.decrypt(db.get(Instance,1).token_cipher.encode()).decode()==calls[-1][2]
    count=len(calls)
    assert client.put('/api/instances/1',json=payload|{'token':'short'}).status_code==422
    assert len(calls)==count
    assert client.post('/api/instances',json=payload).status_code==422


def test_failed_connection_change_keeps_credentials(api,monkeypatch):
    from fastapi import HTTPException
    client,factory=api
    with factory() as db:stored=db.get(Instance,1).token_cipher
    def offline(*args,**kw):raise HTTPException(502,'offline')
    monkeypatch.setattr(main,'agent',offline)
    payload={'name':'Edge','agent_url':'http://192.0.2.2:9101','profile':'native','allow_http':True,'token':'t'*48}
    assert client.put('/api/instances/1',json=payload).status_code==502
    with factory() as db:
        i=db.get(Instance,1);assert i.token_cipher==stored and i.agent_url=='http://192.0.2.1:9101' and i.name=='Existing edge'


def test_certificate_management_api_uses_selected_profile_and_validates_requests(api,monkeypatch):
    client,_=api;calls=[]
    def agent(i,path='',method='GET',body=None,timeout=10):
        calls.append((i.id,i.profile,path,method,body))
        return {'checked':0,'renewed':[],'errors':[]} if path.endswith('/renew') else {'ok':True}
    monkeypatch.setattr(main,'agent',agent)
    assert client.get('/api/instances/1/certificates/renewal-settings').status_code==200
    assert client.put('/api/instances/1/certificates/renewal-settings',json={'schedule':'daily','daily_time':'03:15'}).status_code==200
    assert calls[-1][:4]==(1,'native','/certificates/renewal-settings','PUT')
    assert client.post('/api/instances/1/certificates/renew',json={'name':'cert','force':True}).status_code==200
    assert calls[-1][-1]=={'name':'cert','force':True}
    assert client.put('/api/instances/1/certificates/cert/policy',json={'automatic':False}).status_code==200
    count=len(calls)
    assert client.post('/api/instances/1/certificates/renew',json={'force':True}).status_code==422
    assert client.put('/api/instances/1/certificates/renewal-settings',json={'daily_time':'30:00'}).status_code==422
    assert client.post('/api/instances/1/certificates/adopt-lego',json={'name':'cert','domains':['example.com'],'email':'admin@example.com','challenge':'dns','provider':'cloudflare','source_name':'../../secret'}).status_code==422
    assert len(calls)==count
    assert client.post('/api/instances/1/certificates/adopt-lego',json={'name':'cert','domains':['example.com','*.example.com'],'email':'admin@example.com','challenge':'dns','provider':'cloudflare','source_name':'example.com','env_file':'/arbitrary/secret'}).status_code==200
    assert calls[-1][2]=='/certificates/adopt-lego' and 'env_file' not in calls[-1][-1]
    client.headers.pop('X-CSRF-Token')
    assert client.post('/api/instances/1/certificates/renew',json={}).status_code==403


@pytest.mark.parametrize('assignment',['host','imported-route','frontend'])
def test_certificate_delete_blocks_draft_assignments_but_allows_staging(api,monkeypatch,assignment):
    client,factory=api;calls=[]
    doc={'hosts':[{'domain':'app.example.com','certificate':'site','enabled':False}]} if assignment=='host' else {'imported_routes':[{'domain':'app.example.com','certificate':'site'}]} if assignment=='imported-route' else {'frontend_certificates':{'edge':['site']}}
    with factory() as db:
        db.get(Instance,1).document=doc;db.commit()
    def agent(i,path='',method='GET',body=None,timeout=10):
        calls.append((i.id,i.profile,path,method));return {'certificate_delete':True,'deleted':True}
    monkeypatch.setattr(main,'agent',agent)
    response=client.delete('/api/instances/1/certificates/site')
    assert response.status_code==409 and 'Entwurf' in response.json()['detail'] and calls==[]
    assert client.delete('/api/instances/1/certificates/site?staging=true').status_code==200
    assert calls[-1]==(1,'native','/certificates/site?staging=true','DELETE')
    assert client.delete('/api/instances/1/certificates/bad!name').status_code==422


def test_certificate_delete_old_agent_and_failed_agent_preserve_draft(api,monkeypatch):
    from fastapi import HTTPException
    client,factory=api
    monkeypatch.setattr(main,'agent',lambda *args,**kwargs:{})
    result=client.delete('/api/instances/1/certificates/site');assert result.status_code==422 and 'aktualisieren' in result.json()['detail']
    def failed(i,path='',*args,**kwargs):
        if not path:return {'certificate_delete':True}
        raise HTTPException(409,'Zertifikat wird von HAProxy geladen.')
    monkeypatch.setattr(main,'agent',failed)
    assert client.delete('/api/instances/1/certificates/site').status_code==409
    with factory() as db:assert db.get(Instance,1).document=={'hosts':[]}


def test_certificate_delete_permissions_csrf_audit_and_target(api,monkeypatch):
    from backend.db import Audit
    client,factory=api;calls=[]
    def agent(i,path='',method='GET',*args,**kwargs):
        calls.append((i.id,i.profile,path,method));return {'certificate_delete':True,'deleted':True}
    monkeypatch.setattr(main,'agent',agent)
    csrf=client.headers.pop('X-CSRF-Token')
    assert client.delete('/api/instances/1/certificates/site').status_code==403 and calls==[]
    client.headers['X-CSRF-Token']=csrf
    assert client.delete('/api/instances/1/certificates/site',headers={'Origin':'https://evil.example'}).status_code==403
    assert client.delete('/api/instances/999/certificates/site').status_code==404
    for role in ('viewer','operator'):
        login=client.post('/api/auth/login',json={'username':role,'password':PASSWORD});assert login.status_code==200
        client.headers['X-CSRF-Token']=login.json()['csrf']
        response=client.delete('/api/instances/1/certificates/site')
        assert response.status_code==(403 if role=='viewer' else 200)
    assert calls[-1]==(1,'native','/certificates/site?staging=false','DELETE')
    with factory() as db:
        entry=db.scalar(select(Audit).where(Audit.action=='certificate.deleted'))
        assert entry and entry.actor=='operator' and entry.target=='Existing edge'

@pytest.mark.parametrize('domain,cert,expected',[
    ('app.example.com',{'domains':['*.example.com']},200),
    ('example.com',{'domains':['*.example.com']},422),
    ('deep.app.example.com',{'domains':['*.example.com']},422),
    ('app.example.com',{'domains':['app.example.com'],'staging':True},422),
    ('app.example.com',{'domains':['app.example.com'],'days_remaining':-1},422),
])
def test_generation_validates_selected_server_certificate_coverage(api,monkeypatch,domain,cert,expected):
    client,_=api;calls=[]
    def agent(i,path='',*args,**kw):
        calls.append((i.id,path))
        if path=='/config':return {'config':'old','hash':'a'*64}
        if path=='/certificates':return [{'name':'site','staging':False,'days_remaining':60}|cert]
        return {'tls_site_bindings':True,'runtime_socket_config':'/run/haproxy/admin.sock','cert_dir_config':'/etc/haproxy/certs'}
    monkeypatch.setattr(main,'agent',agent)
    response=client.put('/api/instances/1/document',json={'tls_enabled':True,'hosts':[{'id':'site','domain':domain,'certificate':'site','servers':[{'address':'192.0.2.1'}]}]})
    assert response.status_code==200,response.text
    response=client.post('/api/instances/1/generate',json={})
    assert response.status_code==expected,response.text
    assert all(id==1 for id,path in calls) and (1,'/certificates') in calls


@pytest.mark.parametrize('supported',[False,True])
def test_site_certificate_generation_requires_updated_agent_and_has_domain_filter(api,monkeypatch,supported):
    client,_=api
    def agent(i,path='',*args,**kwargs):
        if path=='/config':return {'config':'old','hash':'a'*64}
        if path=='/certificates':return [{'name':'site','domains':['*.example.com'],'staging':False,'days_remaining':30}]
        return {'tls_site_bindings':supported,'runtime_socket_config':'/run/admin.sock','cert_dir_config':'/etc/haproxy/certs'}
    monkeypatch.setattr(main,'agent',agent)
    value={'tls_enabled':True,'hosts':[{'id':'site','domain':'app.example.com','certificate':'site','servers':[{'address':'192.0.2.1'}]}]}
    assert client.put('/api/instances/1/document',json=value).status_code==200
    result=client.post('/api/instances/1/generate',json={})
    if supported:
        from backend.tls_bindings import read
        assert result.status_code==200,result.text
        assert read(result.json()['config'])[0]['sites']==[{'domain':'app.example.com','certificate':'site'}]
    else:
        assert result.status_code==422 and 'Agenten' in result.json()['detail']


def test_incomplete_listener_remains_editable_in_layout(api,monkeypatch):
    client,_=api
    monkeypatch.setattr(main,'agent',lambda *a,**kw:{'runtime_socket_config':'/run/haproxy/admin.sock','cert_dir_config':'/etc/haproxy/certs'})
    response=client.put('/api/instances/1/document',json={'frontends':[{'name':'fe_tcp','port':3306,'mode':'tcp','backend':'missing_pool'}]})
    assert response.status_code==200,response.text
    response=client.get('/api/instances/1/proxy-layout')
    assert response.status_code==200 and response.json()['error']
    assert 'fe_tcp' in {f['name'] for f in response.json()['frontends']}


def test_dns_credentials_transport_redaction_and_old_agent_detection(api,monkeypatch):
    from fastapi import HTTPException
    from backend.db import Audit
    client,factory=api;calls=[]
    payload={'name':'web','domains':['example.com'],'email':'admin@example.com','challenge':'dns','provider':'hetzner','dns_token':'hidden-hetzner-token'}
    def agent(i,path='',method='GET',body=None,timeout=10):
        calls.append((i.id,i.profile,path,method,body))
        if not path:return {'dns_credentials_ui':True}
        assert body['dns_token']=='hidden-hetzner-token' and body['engine']=='auto'
        raise HTTPException(422,{'error':'token hidden-hetzner-token failed'})
    monkeypatch.setattr(main,'agent',agent)
    response=client.post('/api/instances/1/certificates/issue',json=payload)
    assert response.status_code==422 and 'hidden-hetzner-token' not in response.text
    assert calls[-1][:4]==(1,'native','/certificates/issue','POST')
    with factory() as db:assert 'hidden-hetzner-token' not in str([(e.action,e.detail) for e in db.scalars(select(Audit)).all()])
    calls.clear();monkeypatch.setattr(main,'agent',lambda *args,**kw:calls.append(args) or {})
    response=client.post('/api/instances/1/certificates/issue',json=payload)
    assert response.status_code==422 and 'Agenten aktualisieren' in response.text and len(calls)==1
    response=client.post('/api/instances/1/certificates/issue',json=payload|{'domains':['invalid-domain']})
    assert response.status_code==422 and 'hidden-hetzner-token' not in response.text and len(calls)==1
