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
    with TestClient(main.app,base_url='https://testserver') as outsider:
        assert outsider.get('/api/instances').status_code==401
        assert outsider.put('/api/instances/1/metadata',json={}).status_code==401
        for role in ('operator','viewer'):
            login=outsider.post('/api/auth/login',json={'username':role,'password':PASSWORD});assert login.status_code==200
            outsider.headers['X-CSRF-Token']=login.json()['csrf']
            assert outsider.get('/api/instances').status_code==200
            assert outsider.put('/api/instances/1/metadata',json={'tags':['Prod']}).status_code==403

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
