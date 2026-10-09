"""Central site credentials, fail-closed policies and deployment consistency."""
import ctypes
import ctypes.util
import hashlib
import os
from pathlib import Path

if not Path('.env').exists():
    from cryptography.fernet import Fernet
    for key,value in {'DB_PASSWORD':'test','ENCRYPTION_KEY':Fernet.generate_key().decode(),'SESSION_SECRET':'s'*48,'ADMIN_PASSWORD':'test-password'}.items():os.environ.setdefault(key,value)

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select,event,func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from backend import basic_auth as auth,main
from backend.db import Base,BasicAuthDirectory,BasicAuthUser,BasicAuthDeployment,Instance,User,Audit,Metric,MetricBucket
from backend.schemas import BasicAuthUserIn,BasicAuthGroupIn,Document,Host
from backend.generator import generate
from backend.haproxy_config import import_config

CAP={'runtime_socket_config':'/run/admin.sock','cert_dir_config':'/etc/certs'}
PASSWORD='test-password-123'
IMPORTED='''defaults
 mode http
 timeout connect 5s
 timeout client 30s
 timeout server 30s
frontend incoming
 bind :8080
 acl protected hdr(host) -i private.example.com
 use_backend shared if protected
 acl public hdr(host) -i public.example.com
 use_backend shared if public
backend shared
 http-request set-header X-Proxy example
 server app 192.0.2.10:80 check
frontend mysql
 mode tcp
 bind :3306
 default_backend db
backend db
 mode tcp
 server db1 192.0.2.11:3306 check
userlist legacy
 user legacy insecure-password legacy-placeholder
'''


def protected_doc():return Document(hosts=[Host(id='private',domain='private.example.com',servers=[{'address':'192.0.2.1'}],basic_auth_group=1)])
def group(password=PASSWORD):return {'id':1,'realm':'Restricted','users':[{'username':'alice','hash':auth.hash_password(password)}]}
def imported_doc():return Document.model_validate(import_config(IMPORTED,hashlib.sha256(IMPORTED.encode()).hexdigest())['document'])


@pytest.mark.parametrize('length,valid',[(0,True),(1,True),(9,True),(10,True),(200,True),(201,False)])
def test_basic_password_has_no_minimum(length,valid):
    if valid:assert BasicAuthUserIn(username='a',password='a'*length)
    else:
        with pytest.raises(ValidationError):BasicAuthUserIn(username='a',password='a'*length)

@pytest.mark.parametrize('realm',['x\nuser bad','x\r','x\x00','"evil"',"a'b",'${SECRET}','#comment'])
def test_realm_cannot_inject_config_or_headers(realm):
    with pytest.raises(ValidationError):BasicAuthGroupIn(name='Team',realm=realm)

@pytest.mark.parametrize('username',['x:y','x y','a\nuser other','a/b','ä'])
def test_usernames_are_safe_basic_auth_tokens(username):
    with pytest.raises(ValidationError):BasicAuthUserIn(username=username,password=PASSWORD)


def test_password_hash_uses_random_salt_and_system_crypt():
    first,second=auth.hash_password(PASSWORD),auth.hash_password(PASSWORD)
    assert first.startswith('$6$rounds=50000$') and first!=second and PASSWORD not in first
    with auth.crypt_lock:
        crypt=ctypes.CDLL(ctypes.util.find_library('crypt')).crypt;crypt.argtypes=[ctypes.c_char_p,ctypes.c_char_p];crypt.restype=ctypes.c_char_p
        assert crypt(PASSWORD.encode(),first.encode()).decode()==first
        assert crypt(b'wrong-password',first.encode()).decode()!=first


def test_generated_policy_is_scoped_to_selected_backend_and_removes_credentials():
    doc=protected_doc();doc.hosts.append(Host(id='public',domain='public.example.com',servers=[{'address':'192.0.2.2'}]))
    config=generate(doc,CAP,{1:group()});protected=config.split('backend backend_private\n',1)[1].split('backend backend_public')[0]
    assert 'http-request auth realm' in protected and 'http-request del-header Authorization' in protected
    assert 'http-request auth' not in config.split('backend backend_public\n',1)[1].split(auth.META)[0]
    assert PASSWORD not in config and 'insecure-password' not in config
    assert auth.read_metadata(config)['sites'][0]['domain']=='private.example.com'


def test_empty_group_still_requires_auth_and_never_creates_invalid_empty_userlist():
    empty={'id':1,'realm':'Restricted','users':[]};config=generate(protected_doc(),CAP,{1:empty})
    assert "http-request auth realm 'Restricted'\n" in config
    assert 'userlist mgmt_basic_g1' not in config and 'http_auth(' not in config


def test_forwarding_is_opt_in_and_disabled_hosts_do_not_export_hashes():
    doc=protected_doc();doc.hosts[0].basic_auth_forward=True
    assert 'del-header Authorization' not in generate(doc,CAP,{1:group()})
    doc.hosts[0].enabled=False
    assert auth.ids(doc)==set() and auth.META not in generate(doc,CAP)


def test_imported_shared_backend_is_protected_per_frontend_and_domain():
    doc=imported_doc();private=next(r for r in doc.imported_routes if r.domain=='private.example.com');private.basic_auth_group=1
    config=generate(doc,{}, {1:group()})
    assert '{ fe_name -m str incoming } { hdr(host) -i private.example.com }' in config
    assert 'insecure-password legacy-placeholder' in config and 'mode tcp' in config
    assert config.index('http-request auth')<config.index('http-request set-header X-Proxy')
    assert '{ hdr(host) -i public.example.com } !{ http_auth' not in config
    again=Document.model_validate(import_config(config,hashlib.sha256(config.encode()).hexdigest())['document'])
    assert next(r for r in again.imported_routes if r.domain=='private.example.com').basic_auth_group==1
    regenerated=generate(again,{}, {1:group()})
    assert regenerated.count('userlist mgmt_basic_g1')==1 and regenerated.count('http-request auth')==1


def test_reimporting_native_path_hosts_preserves_uneditable_managed_protection():
    doc=protected_doc();doc.hosts[0].path='/admin'
    config=generate(doc,CAP,{1:group()});again=Document.model_validate(import_config(config,hashlib.sha256(config.encode()).hexdigest())['document'])
    assert not again.imported_routes and auth.ids(again)=={1}
    regenerated=generate(again,{}, {1:group()})
    assert "http-request auth realm 'Restricted'" in regenerated and auth.read_metadata(regenerated)['sites'][0]['path']=='/admin'


def test_removing_imported_site_group_removes_managed_auth_only():
    doc=imported_doc();doc.imported_routes[0].basic_auth_group=1
    config=generate(doc,{}, {1:group()});again=Document.model_validate(import_config(config,hashlib.sha256(config.encode()).hexdigest())['document'])
    again.imported_routes[0].basic_auth_group=None
    cleared=generate(again,{})
    assert 'http-request auth' not in cleared and 'userlist legacy' in cleared and auth.META not in cleared


def test_tcp_and_inherited_early_http_actions_are_not_claimed_as_protected():
    doc=imported_doc();doc.imported_routes[0].basic_auth_group=1;doc.imported_routes[0].backend='db'
    with pytest.raises(ValueError,match='HTTP-Backend'):generate(doc,{}, {1:group()})
    doc=imported_doc();doc.imported_routes[0].basic_auth_group=1;doc.imported_config=doc.imported_config.replace(' mode http\n',' mode http\n http-request allow\n',1)
    with pytest.raises(ValueError,match='defaults'):generate(doc,{}, {1:group()})


def test_marker_corruption_and_userlist_collisions_are_rejected():
    with pytest.raises(ValueError):auth.read_metadata(auth.META+'not-base64')
    with pytest.raises(ValueError):auth.strip_managed(auth.PREFIX+'BEGIN users\nmissing end')
    doc=imported_doc();doc.imported_routes[0].basic_auth_group=1;doc.imported_config+='\nuserlist mgmt_basic_g1\n user another insecure-password placeholder\n'
    with pytest.raises(ValueError,match='Namenskonflikt'):generate(doc,{}, {1:group()})


@pytest.mark.parametrize('challenge',[
    ' http-request auth realm "Old team" if !{ http_auth(legacy) }',
    ' http-request auth realm "Old team" unless { http_auth(legacy) }',
    ' http-request auth if !{ http_auth(legacy) } or { path /locked }',
    ' http-request auth',
])
def test_existing_backend_auth_requires_opt_in_and_survives_regeneration(challenge):
    doc=imported_doc();doc.imported_config=doc.imported_config.replace('backend shared\n','backend shared\n'+challenge+'\n')
    route=doc.imported_routes[0];route.basic_auth_group=1
    assert auth.existing_rules(doc.imported_config)==[{'kind':'backend','name':'shared','rules':[challenge.strip()]}]
    with pytest.raises(ValueError,match='Domain-Zuordnungen'):generate(doc,{}, {1:group()})
    route.basic_auth_replace_existing=True
    config=generate(doc,{}, {1:group()})
    assert auth.strip_managed(config).count(challenge+'\n')==1
    assert auth.existing_rules(config)==auth.existing_rules(doc.imported_config)
    assert config.count(auth.PREFIX+'BEGIN legacy ')==1
    assert auth.read_metadata(config)['sites'][0]['replace_existing'] is True
    again=Document.model_validate(import_config(config,hashlib.sha256(config.encode()).hexdigest())['document'])
    selected=next(r for r in again.imported_routes if r.domain==route.domain)
    assert selected.basic_auth_group==1 and selected.basic_auth_replace_existing
    regenerated=generate(again,{}, {1:group()})
    assert regenerated.count(auth.PREFIX+'BEGIN legacy ')==1
    selected.basic_auth_group=None
    restored=generate(again,{})
    assert challenge+'\n' in restored and 'txn.mgmt_' not in restored and auth.META not in restored
    assert 'userlist legacy' in restored


def test_existing_frontend_auth_cannot_be_bypassed_by_backend_migration():
    doc=imported_doc();doc.imported_config=doc.imported_config.replace(' bind :8080\n',' bind :8080\n http-request auth unless { http_auth(legacy) }\n')
    doc.imported_routes[0].basic_auth_group=1;doc.imported_routes[0].basic_auth_replace_existing=True
    with pytest.raises(ValueError,match='Frontend incoming'):generate(doc,{}, {1:group()})


def test_migration_rejects_internal_variable_collisions_and_corrupt_originals():
    doc=imported_doc();doc.imported_routes[0].basic_auth_group=1;doc.imported_routes[0].basic_auth_replace_existing=True
    doc.imported_config=doc.imported_config.replace('backend shared\n','backend shared\n http-request auth unless { http_auth(legacy) }\n http-request set-var(txn.mgmt_skip_any) bool(true)\n')
    with pytest.raises(ValueError,match='Namenskonflikt'):generate(doc,{}, {1:group()})
    with pytest.raises(ValueError,match='ursprüngliche'):auth.strip_managed(auth.PREFIX+'BEGIN legacy invalid!\n')


@pytest.fixture
def api():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    @event.listens_for(engine,'connect')
    def foreign_keys(connection,record):connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine);factory=sessionmaker(engine,expire_on_commit=False)
    with factory() as db:
        hashed=main.ph.hash(PASSWORD)
        db.add_all([User(username=role,password_hash=hashed,role=role,must_change_password=False) for role in ('admin','operator','viewer')]);db.add(BasicAuthDirectory(id=1));db.add(Instance(name='Test',agent_url='http://192.0.2.1:9101',profile='lab',token_cipher='unused',document=protected_doc().model_dump()));db.commit()
    def get_db():
        with factory() as db:yield db
    main.app.dependency_overrides[main.get_db]=get_db
    client=TestClient(main.app,base_url='https://testserver')
    response=client.post('/api/auth/login',json={'username':'admin','password':PASSWORD});assert response.status_code==200
    client.headers['X-CSRF-Token']=response.json()['csrf']
    yield client,factory
    main.app.dependency_overrides.clear();client.close();engine.dispose()


def create_directory(client):
    g=client.post('/api/basic-auth/groups',json={'name':'Internal','realm':'Team'});assert g.status_code==201,g.text
    u=client.post('/api/basic-auth/users',json={'username':'alice','password':PASSWORD,'group_ids':[g.json()['id']]});assert u.status_code==201,u.text
    return g.json(),u.json()


def test_document_exposes_original_rules_and_persists_explicit_migration(api):
    client,factory=api;g,_=create_directory(client)
    doc=imported_doc();doc.imported_config=doc.imported_config.replace('backend shared\n','backend shared\n http-request auth unless { http_auth(legacy) }\n')
    doc.imported_routes[0].basic_auth_group=g['id'];doc.imported_routes[0].basic_auth_replace_existing=True
    response=client.put('/api/instances/1/document',json=doc.model_dump())
    assert response.status_code==200,response.text
    assert response.json()['basic_auth_existing'][0]['name']=='shared'
    assert client.get('/api/instances/1/document').json()['imported_routes'][0]['basic_auth_replace_existing'] is True
    with factory() as db:assert 'basic_auth_existing' not in db.get(Instance,1).document


def test_directory_crud_membership_and_hash_privacy(api):
    client,factory=api;g,u=create_directory(client)
    values=client.get('/api/basic-auth').json();assert values['groups'][0]['active_members']==1 and values['instances'][0]['pending']
    assert 'password' not in client.get('/api/basic-auth').text and '$6$' not in client.get('/api/basic-auth').text
    with factory() as db:
        value=db.get(BasicAuthUser,u['id']);original=value.password_hash;assert original.startswith('$6$') and original!=PASSWORD
    updated=client.put('/api/basic-auth/users/'+str(u['id']),json={**u,'enabled':False});assert updated.status_code==200
    with factory() as db:assert db.get(BasicAuthUser,u['id']).password_hash==original
    assert client.get('/api/basic-auth').json()['groups'][0]['active_members']==0
    assert client.put('/api/basic-auth/users/'+str(u['id']),json=u).status_code==409
    assert client.delete('/api/basic-auth/groups/'+str(g['id'])).status_code==409
    assert client.delete('/api/basic-auth/users/'+str(u['id'])).status_code==200
    assert client.get('/api/basic-auth').json()['groups'][0]['members']==0
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Metric))==0 and db.scalar(select(func.count()).select_from(MetricBucket))==0
        assert PASSWORD not in '\n'.join(a.detail for a in db.scalars(select(Audit)))


def test_api_validation_duplicates_and_missing_group(api):
    client,_=api;g,u=create_directory(client)
    assert client.post('/api/basic-auth/users',json={'username':'bob','password':''}).status_code==422
    assert client.post('/api/basic-auth/users',json={'username':'bob'}).status_code==422
    assert client.post('/api/basic-auth/users',json={'username':'bob','password':PASSWORD,'group_ids':[999]}).status_code==422
    assert client.post('/api/basic-auth/users',json={'username':'alice','password':PASSWORD}).status_code==409
    assert client.post('/api/basic-auth/groups',json={'name':'Internal'}).status_code==409
    assert client.put('/api/basic-auth/groups/'+str(g['id']),json={**g,'realm':'New Realm'}).status_code==200
    assert client.put('/api/basic-auth/groups/'+str(g['id']),json=g).status_code==409


def test_api_permissions_csrf_and_management_login_separation(api):
    client,_=api;g,u=create_directory(client)
    csrf=client.headers.pop('X-CSRF-Token')
    assert client.post('/api/basic-auth/groups',json={'name':'Unsafe'}).status_code==403
    client.headers['X-CSRF-Token']=csrf
    assert client.post('/api/basic-auth/groups',json={'name':'Unsafe'},headers={'Origin':'https://evil.example'}).status_code==403
    outsider=TestClient(main.app,base_url='https://testserver')
    assert outsider.get('/api/basic-auth').status_code==401
    assert outsider.post('/api/auth/login',json={'username':'alice','password':PASSWORD}).status_code==401
    for role in ('operator','viewer'):
        response=outsider.post('/api/auth/login',json={'username':role,'password':PASSWORD});assert response.status_code==200
        outsider.headers['X-CSRF-Token']=response.json()['csrf']
        assert outsider.get('/api/basic-auth').status_code==(200 if role=='operator' else 403)
        assert outsider.post('/api/basic-auth/groups',json={'name':'Forbidden'}).status_code==403
        assert outsider.put('/api/basic-auth/users/'+str(u['id']),json=u).status_code==403
    outsider.close()


def test_missing_document_group_is_rejected(api):
    client,_=api
    doc=client.get('/api/instances/1/document').json()
    assert client.put('/api/instances/1/document',json=doc).status_code==422


def test_password_change_blocks_old_revision_and_marks_deployment_pending(api,monkeypatch):
    client,factory=api;g,u=create_directory(client);active='global\n';active_hash=hashlib.sha256(active.encode()).hexdigest();applied=[]
    def agent(i,path='',method='GET',body=None,**kw):
        nonlocal active,active_hash
        if not path:return CAP
        if path=='/config':return {'config':active,'hash':active_hash}
        if path=='/apply':active=body['config'];active_hash=hashlib.sha256(active.encode()).hexdigest();applied.append(active);return {'hash':active_hash}
        raise AssertionError(path)
    monkeypatch.setattr(main,'agent',agent)
    generated=client.post('/api/instances/1/generate',json={});assert generated.status_code==200,generated.text
    rev=client.post('/api/instances/1/revisions',json=generated.json()).json()['id']
    updated=client.put('/api/basic-auth/users/'+str(u['id']),json={**u,'password':'replacement-password'});assert updated.status_code==200
    assert client.post(f'/api/instances/1/revisions/{rev}/apply',json={}).status_code==409 and not applied
    generated=client.post('/api/instances/1/generate',json={});rev=client.post('/api/instances/1/revisions',json=generated.json()).json()['id']
    assert client.post(f'/api/instances/1/revisions/{rev}/apply',json={}).status_code==200
    assert not client.get('/api/basic-auth').json()['instances'][0]['pending']
    assert client.put('/api/basic-auth/users/'+str(u['id']),json={**updated.json(),'enabled':False}).status_code==200
    assert client.get('/api/basic-auth').json()['instances'][0]['pending']
    with factory() as db:assert db.get(BasicAuthDeployment,1).metadata_json['groups']


def test_api_short_password_creation_change_and_blank_update(api):
    client,factory=api
    created=client.post('/api/basic-auth/users',json={'username':'short','password':'x'});assert created.status_code==201,created.text
    user=created.json()
    with factory() as db:original=db.get(BasicAuthUser,user['id']).password_hash
    changed=client.put('/api/basic-auth/users/'+str(user['id']),json={**user,'password':'ab'});assert changed.status_code==200,changed.text
    with factory() as db:updated=db.get(BasicAuthUser,user['id']).password_hash
    assert original!=updated
    with auth.crypt_lock:
        crypt=ctypes.CDLL(ctypes.util.find_library('crypt')).crypt;crypt.argtypes=[ctypes.c_char_p,ctypes.c_char_p];crypt.restype=ctypes.c_char_p
        assert crypt(b'x',original.encode()).decode()==original
        assert crypt(b'ab',updated.encode()).decode()==updated
        assert crypt(b'x',updated.encode()).decode()!=updated
    retained=client.put('/api/basic-auth/users/'+str(user['id']),json={**changed.json(),'password':''});assert retained.status_code==200,retained.text
    with factory() as db:assert db.get(BasicAuthUser,user['id']).password_hash==updated
    assert client.post('/api/basic-auth/users',json={'username':'missing','password':None}).status_code==422
