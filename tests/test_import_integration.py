"""Optional real migration test; HAPROXY_IMPORT_LAB_CONFIG must point at isolated labs."""
import json
import os
import secrets
import socket
import ssl
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from backend.main import app, ph
from backend.db import SessionLocal, User, LoginSession, Instance, Audit

LAB=os.environ.get('HAPROXY_IMPORT_LAB_CONFIG')
pytestmark=pytest.mark.skipif(not LAB,reason='Requires isolated multi-file HAProxy labs')

@pytest.fixture(params=['native-import','docker-import'])
def managed(request):
    lab=json.loads(Path(LAB).read_text());p=lab['profiles'][request.param];extra=lab['tests'][request.param]
    username='import_test_'+secrets.token_hex(6);password=secrets.token_urlsafe(24)
    with SessionLocal() as db:
        user=User(username=username,password_hash=ph.hash(password),role='admin',must_change_password=False);db.add(user);db.commit();uid=user.id
    instance_id=None
    try:
        with TestClient(app) as client:
            login=client.post('/api/auth/login',json={'username':username,'password':password});assert login.status_code==200
            client.headers['X-CSRF-Token']=login.json()['csrf']
            result=client.post('/api/instances',json={'name':'Isolated '+request.param,'agent_url':lab['url'],'profile':request.param,'token':p['token'],'allow_http':True})
            assert result.status_code==200,result.text;instance_id=result.json()['id']
            yield client,instance_id,p,extra
    finally:
        with SessionLocal() as db:
            if instance_id:db.execute(delete(Instance).where(Instance.id==instance_id))
            db.execute(delete(LoginSession).where(LoginSession.user_id==uid));db.execute(delete(User).where(User.id==uid));db.execute(delete(Audit).where(Audit.actor==username));db.commit()


def test_real_map_multifile_migration_stats_and_restore(managed):
    client,id,p,extra=managed;base=f'/api/instances/{id}'
    original_primary=Path(p['config_path']).read_bytes();original_extra=Path(extra['extra_file']).read_bytes()
    stats=client.get(base+'/stats');assert stats.status_code==200,stats.text
    rows=stats.json()['rows'];fronts={r['pxname']:r for r in rows if r['role']=='frontend'}
    assert fronts['mysql']['mode']=='tcp' and fronts['fe_https']['service']=='HTTPS-Reverse-Proxy'
    assert fronts['fe_prometheus']['service']=='Prometheus-Exporter'
    assert fronts['stats']['proxy_kind']=='listen'
    assert 'app.example.com' in fronts['fe_https']['domains']
    def get(domain):
        req=Request(f'https://127.0.0.1:{extra["https_port"]}/',headers={'Host':domain})
        return urlopen(req,context=ssl._create_unverified_context(),timeout=5).read()
    assert get('app.example.com')==b'old-app'
    preview=client.post(base+'/import-preview',json={});assert preview.status_code==200,preview.text
    values=preview.json();assert len(values['source_files'])==2 and values['summary']['editable_servers']==5
    assert Path(p['config_path']).read_bytes()==original_primary
    # An externally changed secondary file invalidates the reviewed preview.
    secondary=Path(extra['extra_file']);secondary.write_bytes(original_extra+b'\n# Changed after preview\n')
    result=client.post(base+'/import',json={k:values[k] for k in ('active_hash','document_version','preview_hash')})
    assert result.status_code==409,result.text;secondary.write_bytes(original_extra)
    preview=client.post(base+'/import-preview',json={}).json()
    result=client.post(base+'/import',json={k:preview[k] for k in ('active_hash','document_version','preview_hash')})
    assert result.status_code==200,result.text
    assert Path(p['config_path']).read_bytes()==original_primary and secondary.read_bytes()==original_extra
    document=result.json()
    route=next(r for r in document['imported_routes'] if r['domain']=='app.example.com');route['domain']='new.example.com'
    pool=next(b for b in document['imported_backends'] if b['name']=='be_app');pool['servers'][0]['port']=extra['new_target_port']
    saved=client.put(base+'/document',json=document);assert saved.status_code==200,saved.text
    generated=client.post(base+'/generate',json={});assert generated.status_code==200,generated.text
    data=generated.json();assert 'hdr(host) -i new.example.com' in data['config']
    checked=client.post(base+'/validate',json=data);assert checked.status_code==200,checked.text
    revision=client.post(base+'/revisions',json=data|{'message':'Isolated multi-file migration'}).json()['id']
    applied=client.post(base+f'/revisions/{revision}/apply',json={});assert applied.status_code==200,applied.text
    assert secondary.read_text().startswith('# Consolidated') and 'backend be_app' in Path(p['config_path']).read_text()
    assert get('new.example.com')==b'new-app'
    # Verify layer-4 forwarding survived the migration.
    with socket.create_connection(('127.0.0.1',extra['tcp_port']),timeout=5) as sock:
        sock.sendall(b'mysql-smoke');assert sock.recv(50)==b'mysql-smoke'
    again=client.post(base+'/generate',json={});assert again.status_code==200,again.text
    assert client.post(base+'/import-preview',json={}).json()['summary']['routes']==2
    revisions=client.get(base+'/revisions').json();snapshot=next(r for r in revisions if r['status']=='snapshot')
    old=client.get(base+f'/revisions/{snapshot["id"]}').json();current=client.get(base+'/config').json()
    restore=client.post(base+'/revisions',json={'config':old['config'],'base_hash':current['hash'],'message':'Restore original semantics'}).json()['id']
    result=client.post(base+f'/revisions/{restore}/apply',json={});assert result.status_code==200,result.text
    assert get('app.example.com')==b'old-app'
    # Original file boundaries are retained separately in the agent backup bundle.
    assert original_extra.decode() in old['config']
