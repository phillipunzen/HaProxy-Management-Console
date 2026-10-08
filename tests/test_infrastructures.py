"""Organizational scopes and explicit per-server certificate/configuration targets."""
import os
from pathlib import Path
if not Path('.env').exists():
    from cryptography.fernet import Fernet
    for key,value in {'DB_PASSWORD':'test','ENCRYPTION_KEY':Fernet.generate_key().decode(),'SESSION_SECRET':'s'*48,'ADMIN_PASSWORD':'test-password'}.items():os.environ.setdefault(key,value)
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine,event,select,func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from backend import main
from backend.db import Base,User,Instance,Infrastructure,InstanceInfrastructure,InstanceMetadata,Metric,MetricBucket,Revision
from backend.schemas import InfrastructureIn,Document,CertificateIn
from backend.agent_setup import AgentSetupIn,build_plan
from agent import main as agent

PASSWORD='test-password-123'

@pytest.fixture
def api(monkeypatch):
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    @event.listens_for(engine,'connect')
    def fk(connection,record):connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine);factory=sessionmaker(engine,expire_on_commit=False)
    with factory() as db:
        hashed=main.ph.hash(PASSWORD)
        db.add_all([User(username=role,password_hash=hashed,role=role,must_change_password=False) for role in ('admin','operator','viewer')])
        for n in (1,2):db.add(Instance(name=f'Edge {n}',agent_url=f'http://192.0.2.{n}:9101',profile=f'edge-{n}',token_cipher=main.cipher.encrypt(b'x'*48).decode(),notes='Preserve notes',document=Document().model_dump()))
        db.commit()
    def get_db():
        with factory() as db:yield db
    main.app.dependency_overrides[main.get_db]=get_db
    monkeypatch.setattr(main,'agent',lambda *a,**kw:pytest.fail('Grouping must not contact an agent'))
    client=TestClient(main.app,base_url='https://testserver');login=client.post('/api/auth/login',json={'username':'admin','password':PASSWORD});assert login.status_code==200
    client.headers['X-CSRF-Token']=login.json()['csrf']
    yield client,factory
    client.close();main.app.dependency_overrides.clear();engine.dispose()

def group(client,name='Firma A'):
    response=client.post('/api/infrastructures',json={'name':name,'description':'Independent environment'});assert response.status_code==201,response.text
    return response.json()

def test_normalization():
    assert InfrastructureIn(name='  Kunde   A  ').name=='Kunde A'
    assert InfrastructureIn(name='De\u0301v').name=='Dév'

@pytest.mark.parametrize('name',['','  ','x'*121,'A\nB','A\u200bB'])
def test_invalid_names(name):
    with pytest.raises(ValidationError):InfrastructureIn(name=name)

def test_directory_crud_and_concurrency_versions(api):
    c,_=api;a=group(c,'Straße');assert c.post('/api/infrastructures',json={'name':'STRASSE'}).status_code==409
    assert c.get('/api/infrastructures').json()==[a]
    updated=c.put(f"/api/infrastructures/{a['id']}",json={'name':'Homelab','version':0});assert updated.status_code==200 and updated.json()['version']==1
    assert c.put(f"/api/infrastructures/{a['id']}",json={'name':'Stale','version':0}).status_code==409
    assert c.delete(f"/api/infrastructures/{a['id']}").status_code==200
    assert c.get('/api/infrastructures').json()==[]
    assert c.put('/api/infrastructures/999',json={'name':'Missing'}).status_code==404
    assert c.delete('/api/infrastructures/999').status_code==404

def test_assign_unassign_and_safe_deletion(api):
    c,factory=api;a=group(c);b=group(c,'Homelab')
    old=c.get('/api/instances').json();assert all(s['infrastructure_id'] is None for s in old)
    assigned=c.put('/api/instances/1/metadata',json={'infrastructure_id':a['id'],'tags':['Prod'],'location':'Berlin'});assert assigned.status_code==200,assigned.text
    assert assigned.json()['infrastructure_name']=='Firma A' and assigned.json()['metadata_version']==1
    assert c.get('/api/infrastructures').json()[0]['servers']==1
    assert c.delete(f"/api/infrastructures/{a['id']}").status_code==409
    assert c.put('/api/instances/1/metadata',json={'infrastructure_id':b['id'],'metadata_version':0}).status_code==409
    moved=c.put('/api/instances/1/metadata',json={'infrastructure_id':b['id'],'metadata_version':1});assert moved.status_code==200 and moved.json()['tags']==['Prod']
    renamed=c.put(f"/api/infrastructures/{b['id']}",json={'name':'Lab','version':0});assert renamed.status_code==200
    assert c.get('/api/instances').json()[0]['infrastructure_name']=='Lab'
    cleared=c.put('/api/instances/1/metadata',json={'infrastructure_id':None,'metadata_version':2});assert cleared.status_code==200 and cleared.json()['infrastructure_id'] is None
    assert c.delete(f"/api/infrastructures/{b['id']}").status_code==200
    with factory() as db:
        assert db.get(Instance,1).document==Document().model_dump() and db.get(Instance,1).notes=='Preserve notes'
        assert db.get(Instance,2).document==Document().model_dump() and db.get(InstanceMetadata,2) is None
        assert db.scalar(select(func.count()).select_from(Metric))==0 and db.scalar(select(func.count()).select_from(MetricBucket))==0

def test_missing_infrastructure_rolls_back_metadata(api):
    c,factory=api
    assert c.put('/api/instances/1/metadata',json={'infrastructure_id':999,'tags':['Prod']}).status_code==422
    assert c.get('/api/instances').json()[0]['tags']==[]
    with factory() as db:assert db.get(InstanceMetadata,1) is None
    assert c.put('/api/instances/1/metadata',json={'infrastructure_id':0}).status_code==422

def test_instance_deletion_cascades_only_its_assignment(api):
    c,factory=api;a=group(c)
    for id in (1,2):assert c.put(f'/api/instances/{id}/metadata',json={'infrastructure_id':a['id']}).status_code==200
    assert c.delete('/api/instances/1').status_code==200
    assert c.get('/api/infrastructures').json()[0]['servers']==1
    with factory() as db:
        assert db.get(InstanceInfrastructure,1) is None and db.get(InstanceInfrastructure,2).infrastructure_id==a['id']
        assert db.get(Infrastructure,a['id']) is not None

def test_old_clients_preserve_assignment(api,monkeypatch):
    c,_=api;a=group(c);assert c.put('/api/instances/1/metadata',json={'infrastructure_id':a['id']}).status_code==200
    legacy=c.put('/api/instances/1/metadata',json={'tags':['Prod'],'metadata_version':1});assert legacy.status_code==200 and legacy.json()['infrastructure_id']==a['id']
    monkeypatch.setattr(main,'agent',lambda *a,**kw:{'kind':'native'})
    payload={'name':'Updated','agent_url':'http://192.0.2.1:9101','profile':'edge-1','token':'t'*48,'allow_http':True}
    updated=c.put('/api/instances/1',json=payload);assert updated.status_code==200 and updated.json()['infrastructure_id']==a['id']
    response=c.post('/api/instances',json={**payload,'agent_url':'http://192.0.2.3:9101','infrastructure_id':a['id']});assert response.status_code==200 and response.json()['infrastructure_name']=='Firma A'

@pytest.mark.parametrize('role',['operator','viewer'])
def test_grouping_roles_and_csrf(api,role):
    c,_=api;a=group(c);saved=c.headers.pop('X-CSRF-Token')
    assert c.post('/api/infrastructures',json={'name':'Blocked'}).status_code==403
    c.headers['X-CSRF-Token']=saved
    assert c.post('/api/infrastructures',json={'name':'Blocked'},headers={'Origin':'https://evil.example'}).status_code==403
    outsider=TestClient(main.app,base_url='https://testserver')
    try:
        assert outsider.get('/api/infrastructures').status_code==401
        login=outsider.post('/api/auth/login',json={'username':role,'password':PASSWORD});assert login.status_code==200
        outsider.headers['X-CSRF-Token']=login.json()['csrf']
        assert outsider.get('/api/infrastructures').status_code==200
        assert outsider.post('/api/infrastructures',json={'name':'Blocked'}).status_code==403
        assert outsider.put(f"/api/infrastructures/{a['id']}",json={'name':'Blocked'}).status_code==403
        assert outsider.delete(f"/api/infrastructures/{a['id']}").status_code==403
        assert outsider.put('/api/instances/1/metadata',json={'infrastructure_id':a['id']}).status_code==403
    finally:outsider.close()

@pytest.mark.parametrize('action',['issue','import','renew'])
def test_certificate_requests_only_contact_explicit_target(api,monkeypatch,action):
    c,factory=api;a=group(c);b=group(c,'Homelab')
    for id,infrastructure in [(1,a),(2,b)]:assert c.put(f'/api/instances/{id}/metadata',json={'infrastructure_id':infrastructure['id']}).status_code==200
    calls=[]
    def fake(instance,path='',method='GET',body=None,**kw):
        calls.append((instance.id,instance.agent_url,instance.profile,path));return {'checked':1,'renewed':[]}
    monkeypatch.setattr(main,'agent',fake)
    payload={'name':'shared-name','domains':['example.com'],'email':'admin@example.com','challenge':'dns','provider':'cloudflare','staging':True} if action=='issue' else {'name':'shared-name','pem':'p'*100} if action=='import' else {}
    response=c.post('/api/instances/2/certificates/'+action,json=payload);assert response.status_code==200,response.text
    assert calls==[(2,'http://192.0.2.2:9101','edge-2','/certificates/'+action)]
    assert c.post('/api/instances/999/certificates/'+action,json=payload).status_code==404
    with factory() as db:
        assert all(db.get(Instance,id).document==Document().model_dump() for id in (1,2))
        assert db.scalar(select(func.count()).select_from(Revision))==0


def test_documents_only_change_selected_server(api):
    c,factory=api;a=group(c);b=group(c,'Homelab')
    for id,g in [(1,a),(2,b)]:assert c.put(f'/api/instances/{id}/metadata',json={'infrastructure_id':g['id']}).status_code==200
    value={**Document().model_dump(),'http_port':8081,'version':0}
    response=c.put('/api/instances/1/document',json=value);assert response.status_code==200,response.text
    assert c.get('/api/instances/2/document').json()['http_port']==80
    with factory() as db:assert db.get(Instance,2).document_version==0


def test_setup_assignment_stays_out_of_agent_profile():
    value=AgentSetupIn(infrastructure_id=42,name='Edge',kind='native',host='192.168.10.71',profile='native',config_path='/etc/haproxy/haproxy.cfg',runtime_socket='/run/haproxy/admin.sock',cert_dir='/etc/haproxy/certs',allow_http=True)
    plan=build_plan(value,'http://192.168.10.70:8100',Path(__file__).resolve().parents[1]);assert plan['instance']['infrastructure_id']==42
    import base64,json,re
    payload=json.loads(base64.b64decode(re.search(r'HAPROXY_AGENT_SETUP_B64=([a-zA-Z0-9+/=]+)',plan['command']).group(1)))
    assert 'infrastructure_id' not in payload


def test_certbot_state_and_cloudflare_credentials_separate_per_profile(monkeypatch,tmp_path):
    monkeypatch.setattr(agent,'STATE_DIR',tmp_path)
    p={'config_path':'/opt/customer-a/haproxy.cfg','dns_providers':{'cloudflare':{'credentials_file':'/etc/customer-a/cloudflare.ini'}}}
    q={'config_path':'/opt/customer-b/haproxy.cfg','dns_providers':{'cloudflare':{'credentials_file':'/etc/customer-b/cloudflare.ini'}}}
    value=CertificateIn(name='same-name',domains=['example.com'],email='admin@example.com',challenge='dns',provider='cloudflare')
    name_a,args_a=agent.certbot_args(p,value);name_b,args_b=agent.certbot_args(q,value)
    assert name_a!=name_b and agent.cert_state(p)!=agent.cert_state(q)
    assert '/etc/customer-a/cloudflare.ini' in args_a and '/etc/customer-b/cloudflare.ini' in args_b
