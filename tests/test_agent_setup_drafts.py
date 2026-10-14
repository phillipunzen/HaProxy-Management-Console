"""Pending setup credentials survive UI/process restarts, with explicit lifecycle."""
import json
from cryptography.fernet import Fernet
from backend import main
from backend.db import AgentSetupDraft,User
from sqlalchemy import select
from tests.test_import_api import api,PASSWORD
from tests.test_agent_setup import values


def identity(plan):return {key:plan[key] for key in ('setup_id','setup_version')}


def test_setup_persists_encrypted_token_and_resume_rebuilds_same_command(api,monkeypatch):
    c,factory,state=api
    first=c.post('/api/agent-setup',json=values()).json();token=first['instance']['token']
    with factory() as db:
        row=db.scalar(select(AgentSetupDraft));assert token not in row.data_cipher and 'haproxy.cfg' not in row.data_cipher
        assert json.loads(main.cipher.decrypt(row.data_cipher.encode()))['token']==token
    # Recreate the encryption object as after an application process restart.
    monkeypatch.setattr(main,'cipher',Fernet(main.settings.encryption_key.encode()))
    c.cookies.clear();login=c.post('/api/auth/login',json={'username':'admin','password':PASSWORD});c.headers['X-CSRF-Token']=login.json()['csrf']
    pending=c.get('/api/agent-setup').json()['pending_setup']
    assert pending['plan']['instance']['token']==token
    assert pending['plan']['command']==first['command']
    assert identity(pending)==identity(first)
    assert state['calls']==[]


def test_edit_public_proxy_url_and_metadata_reuses_token(api):
    c,factory,state=api
    body=values()|dict(connection='reverse_proxy',public_agent_url='https://agent.example.de',port=9091)
    first=c.post('/api/agent-setup',json=body).json()
    changed=c.post('/api/agent-setup',json=body|identity(first)|dict(public_agent_url='https://agent.example.de:443',name='Renamed'))
    assert changed.status_code==200
    assert changed.json()['instance']['token']==first['instance']['token']
    assert changed.json()['instance']['agent_url']=='https://agent.example.de:443'
    assert changed.json()['setup_version']==first['setup_version']+1
    stale=c.post('/api/agent-setup',json=body|identity(first));assert stale.status_code==409


def test_new_command_cannot_silently_replace_pending_credentials(api):
    c,factory,state=api
    first=c.post('/api/agent-setup',json=values()).json()
    assert c.post('/api/agent-setup',json=values()).status_code==409
    assert c.post('/api/agent-setup',json=values()|identity(first)|dict(host='192.168.10.99')).status_code==409
    assert c.get('/api/agent-setup').json()['pending_setup']['plan']['instance']['token']==first['instance']['token']


def test_discard_is_versioned_and_does_not_contact_agent(api):
    c,factory,state=api
    first=c.post('/api/agent-setup',json=values()).json()
    assert c.request('DELETE','/api/agent-setup',json=identity(first)|dict(setup_version=2)).status_code==409
    assert c.request('DELETE','/api/agent-setup',json=identity(first)).status_code==200
    assert c.get('/api/agent-setup').json()['pending_setup'] is None
    assert state['calls']==[]
    second=c.post('/api/agent-setup',json=values()).json()
    assert second['instance']['token']!=first['instance']['token']
    assert c.post('/api/agent-setup',json=values()|identity(first)).status_code==409


def test_successful_connection_clears_pending_but_failed_connection_keeps_it(api,monkeypatch):
    from fastapi import HTTPException
    c,factory,state=api
    first=c.post('/api/agent-setup',json=values()).json()
    def fail(*a,**kw):raise HTTPException(502,'not reachable')
    monkeypatch.setattr(main,'agent',fail)
    assert c.post('/api/instances',json=first['instance']).status_code==502
    assert c.get('/api/agent-setup').json()['pending_setup']['plan']['instance']['token']==first['instance']['token']
    monkeypatch.setattr(main,'agent',lambda *a,**kw:{'kind':'native'})
    assert c.post('/api/instances',json=first['instance']).status_code==200
    assert c.get('/api/agent-setup').json()['pending_setup'] is None


def test_wrong_encryption_key_is_reported_without_rotating_or_exposing_tokens(api,monkeypatch):
    c,factory,state=api
    first=c.post('/api/agent-setup',json=values()).json()
    monkeypatch.setattr(main,'cipher',Fernet(Fernet.generate_key()))
    result=c.get('/api/agent-setup');assert result.status_code==503
    assert 'ENCRYPTION_KEY' in result.json()['detail'] and first['instance']['token'] not in result.text
    with factory() as db:assert db.scalar(select(AgentSetupDraft)) is not None


def test_pending_setup_is_private_to_admin_user_and_requires_csrf(api):
    c,factory,state=api
    first=c.post('/api/agent-setup',json=values()).json()
    c.headers['X-CSRF-Token']='bad'
    assert c.request('DELETE','/api/agent-setup',json=identity(first)).status_code==403
    for role in ('viewer','operator'):
        login=c.post('/api/auth/login',json={'username':role,'password':PASSWORD});c.headers['X-CSRF-Token']=login.json()['csrf']
        assert c.get('/api/agent-setup').status_code==403
        assert c.post('/api/agent-setup',json=values()).status_code==403
    # Another administrator has a separate pending setup.
    with factory() as db:
        db.add(User(username='other-admin',password_hash=main.ph.hash(PASSWORD),role='admin',must_change_password=False));db.commit()
    login=c.post('/api/auth/login',json={'username':'other-admin','password':PASSWORD});c.headers['X-CSRF-Token']=login.json()['csrf']
    assert c.get('/api/agent-setup').json()['pending_setup'] is None


def test_existing_connection_reports_changed_key_without_replacing_db_token(api,monkeypatch):
    from backend.db import Instance
    c,factory,state=api
    with factory() as db:before=db.get(Instance,1).token_cipher
    with monkeypatch.context() as change:
        change.setattr(main,'cipher',Fernet(Fernet.generate_key()))
        result=c.get('/api/instances/1/config');assert result.status_code==503
        assert 'ENCRYPTION_KEY' in result.json()['detail']
    with factory() as db:assert db.get(Instance,1).token_cipher==before


def test_resume_uses_current_download_origin_with_original_token(api,monkeypatch):
    c,factory,state=api
    first=c.post('/api/agent-setup',json=values()).json()
    monkeypatch.setattr(main.settings,'agent_download_origin','https://new-download.example.de')
    restored=c.get('/api/agent-setup').json()['pending_setup']['plan']
    assert restored['instance']['token']==first['instance']['token']
    assert 'https://new-download.example.de/api/agent-installer' in restored['command']


def test_setup_survives_two_separate_python_processes(tmp_path):
    import os,subprocess,sys
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    program='''import hashlib,json,sys
from pathlib import Path
from cryptography.fernet import Fernet
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from backend.db import Base,User,AgentSetupDraft
from backend.agent_setup import AgentSetupIn
from backend.agent_setup_drafts import save,response
import os
engine=create_engine('sqlite:///'+sys.argv[1]);Base.metadata.create_all(engine)
cipher=Fernet(os.environ['ENCRYPTION_KEY'].encode())
with Session(engine) as db:
 if sys.argv[3]=='save':
  db.add(User(id=1,username='test-admin',password_hash='synthetic',role='admin'));db.commit()
  body=AgentSetupIn(name='restart-test',kind='native',host='192.168.10.71',profile='test',allow_http=True,config_path='/etc/haproxy/haproxy.cfg',runtime_socket='/run/haproxy/admin.sock',cert_dir='/etc/haproxy/certs')
  value=save(db,1,body,cipher,'https://download.example.de',Path(sys.argv[2]));db.commit()
 else:value=response(db.get(AgentSetupDraft,1),cipher,'https://download.example.de',Path(sys.argv[2]))
 print(json.dumps({'setup_id':value['setup_id'],'token_digest':hashlib.sha256(value['plan']['instance']['token'].encode()).hexdigest()}))
'''
    env={'PATH':os.environ['PATH'],'PYTHONPATH':str(root),'ENCRYPTION_KEY':Fernet.generate_key().decode(),'DB_PASSWORD':'synthetic','SESSION_SECRET':'s'*48,'ADMIN_PASSWORD':'test-password'}
    results=[]
    for mode in ('save','read'):
        result=subprocess.run([sys.executable,'-c',program,str(tmp_path/'pending.db'),str(root),mode],cwd=tmp_path,env=env,capture_output=True,text=True)
        assert result.returncode==0,result.stderr
        results.append(json.loads(result.stdout))
    assert results[0]==results[1]
