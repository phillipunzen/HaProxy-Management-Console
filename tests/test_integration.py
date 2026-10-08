"""Uses the configured MariaDB, plus isolated lab profiles. Never drops database tables."""
import json
import os
import secrets
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,delete
from backend.main import app,ph,cipher
from backend.db import SessionLocal,User,Instance,Audit,LoginSession

LAB=os.environ.get('HAPROXY_LAB_CONFIG')
pytestmark=pytest.mark.skipif(not LAB,reason='Set HAPROXY_LAB_CONFIG to the isolated lab agent config')

@pytest.fixture
def accounts():
    suffix=secrets.token_hex(5);password=secrets.token_urlsafe(28);names={r:'test_'+r+'_'+suffix for r in ('admin','operator','viewer','initial')}
    with SessionLocal() as db:
        for role,name in names.items():db.add(User(username=name,password_hash=ph.hash(password),role=role if role!='initial' else 'viewer',must_change_password=role=='initial'))
        db.commit()
    yield names,password
    with SessionLocal() as db:
        ids=list(db.scalars(select(User.id).where(User.username.in_(list(names.values())))))
        db.execute(delete(LoginSession).where(LoginSession.user_id.in_(ids)))
        db.execute(delete(User).where(User.id.in_(ids)))
        db.execute(delete(Audit).where(Audit.actor.in_(list(names.values()))));db.commit()

@pytest.fixture
def clients(accounts):
    names,password=accounts
    with TestClient(app) as admin,TestClient(app) as operator,TestClient(app) as viewer,TestClient(app) as initial:
        values={}
        for role,client in zip(names,(admin,operator,viewer,initial)):
            response=client.post('/api/auth/login',json={'username':names[role],'password':password});assert response.status_code==200
            client.headers['X-CSRF-Token']=response.json()['csrf'];values[role]=client
        yield values

@pytest.fixture(params=['docker-lab','native-lab'])
def managed(request,clients):
    conf=json.loads(Path(LAB).read_text());p=conf['profiles'][request.param]
    response=clients['admin'].post('/api/instances',json={'name':'Integration '+request.param,'profile':request.param,'agent_url':os.environ.get('HAPROXY_LAB_URL','http://127.0.0.1:9101'),'allow_http':True,'token':p['token']})
    assert response.status_code==200,response.text
    id=response.json()['id']
    yield clients,id,p
    result=clients['admin'].delete(f'/api/instances/{id}');assert result.status_code==200,result.text


def test_auth_csrf_and_password_change(clients,accounts):
    initial=clients['initial'];viewer=clients['viewer'];admin=clients['admin']
    assert initial.get('/api/instances').status_code==403
    assert viewer.post('/api/instances',json={}).status_code==403
    token=admin.headers.pop('X-CSRF-Token')
    assert admin.post('/api/auth/logout',json={}).status_code==403
    admin.headers['X-CSRF-Token']=token
    assert admin.post('/api/auth/logout',json={},headers={'Origin':'https://evil.example'}).status_code==403
    result=initial.post('/api/auth/password',json={'current_password':accounts[1],'password':secrets.token_urlsafe(25)})
    assert result.status_code==200
    assert initial.get('/api/auth/me').status_code==401


def test_real_haproxy_validate_apply_restore_and_role_boundaries(managed):
    clients,id,p=managed;admin=clients['admin'];viewer=clients['viewer'];operator=clients['operator'];base=f'/api/instances/{id}'
    assert viewer.get(base+'/stats').json()['online'] is True
    assert viewer.get(base+'/config').status_code==403
    assert operator.delete(base).status_code==403
    before=admin.get(base+'/config').json()
    bad=admin.post(base+'/validate',json={'config':'this is not haproxy\n','base_hash':before['hash']})
    assert bad.status_code==422,bad.text
    assert admin.get(base+'/config').json()==before
    # Exercise all generated ACL types against the actual installed HAProxy binary.
    document=admin.get(base+'/document').json()
    document.update({'http_port':8080 if p['kind']=='docker' else 18181,'acme_enabled':True,'hosts':[{'id':'wildcard','domain':'*.example.com','path':'/api','servers':[{'address':'127.0.0.1','port':18182}]},{'id':'specific','domain':'app.example.com','path':'/','servers':[{'address':'::1','port':18182}]}],
                     'rules':[{'id':'redirect','name':'Redirect','match':'host','value':'old.example.com','action':'redirect','target':'https://app.example.com/new','code':308},{'id':'deny','name':'Block','match':'source_ip','value':'10.0.0.0/8','action':'deny'},{'id':'header','name':'Header','match':'path_prefix','value':'/','action':'set_header','target':'X-Proxy: control'}]})
    saved=admin.put(base+'/document',json=document);assert saved.status_code==200,saved.text
    stale=admin.put(base+'/document',json=document);assert stale.status_code==409
    generated=admin.post(base+'/generate',json={});assert generated.status_code==200,generated.text
    check=admin.post(base+'/validate',json=generated.json());assert check.status_code==200,check.text
    candidate=before['config']+'\n# Integration test safe reload\n'
    draft=admin.post(base+'/revisions',json={'config':candidate,'base_hash':before['hash'],'message':'Integration test'});assert draft.status_code==200
    rev=draft.json()['id']
    applied=admin.post(base+f'/revisions/{rev}/apply',json={});assert applied.status_code==200,applied.text
    assert admin.get(base+'/config').json()['config']==candidate
    assert admin.post(base+f'/revisions/{rev}/apply',json={}).status_code==409
    after=admin.get(base+'/config').json()
    restore=admin.post(base+'/revisions',json={'config':before['config'],'base_hash':after['hash'],'message':'Restore after integration test'}).json()['id']
    result=admin.post(base+f'/revisions/{restore}/apply',json={});assert result.status_code==200,result.text
    assert admin.get(base+'/config').json()==before
    assert admin.get(base+'/metrics').status_code==200


def test_real_service_stop_start_restart(managed):
    clients,id,p=managed;admin=clients['admin'];base=f'/api/instances/{id}'
    try:
        result=admin.post(base+'/service/stop',json={});assert result.status_code==200,result.text
        assert admin.get(base+'/stats').json()['online'] is False
        result=admin.post(base+'/service/start',json={});assert result.status_code==200,result.text
        assert admin.get(base+'/stats').json()['online'] is True
        result=admin.post(base+'/service/restart',json={});assert result.status_code==200,result.text
        assert admin.get(base+'/stats').json()['online'] is True
    finally:
        admin.post(base+'/service/start',json={})


def test_collector_handles_instance_removed_during_network_io(monkeypatch):
    import backend.main as main
    from backend.db import Metric
    profile='race-'+secrets.token_hex(6)
    with SessionLocal() as db:
        value=Instance(name='Collector removal race',agent_url='http://127.0.0.1:9101',profile=profile,
                       token_cipher=cipher.encrypt(secrets.token_urlsafe(40).encode()).decode(),allow_http=True)
        db.add(value);db.commit();id=value.id
    original=main.agent
    def during_request(i,*args,**kwargs):
        if i.id!=id: return original(i,*args,**kwargs)
        with SessionLocal() as db:
            db.delete(db.get(Instance,id));db.commit()
        return {'online':True,'sessions':0}
    monkeypatch.setattr(main,'agent',during_request)
    try:
        main.collect_metrics()
        with SessionLocal() as db:
            assert db.get(Instance,id) is None
            assert db.scalar(select(Metric.id).where(Metric.instance_id==id)) is None
    finally:
        with SessionLocal() as db:
            remaining=db.get(Instance,id)
            if remaining: db.delete(remaining);db.commit()
