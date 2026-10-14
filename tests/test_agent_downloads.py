"""The public download service must never expose management routes or credentials."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from backend.agent_downloads import app
from backend.settings import Settings
from tests.test_import_api import api
from tests.test_agent_setup import values

ROOT=Path(__file__).resolve().parents[1]
FILES={'/api/agent-installer':('scripts/install-agent.sh','text/plain','install-agent.sh'),
       '/api/agent-package':('downloads/haproxy-management-docker.zip','application/zip','haproxy-management-docker.zip')}


@pytest.mark.parametrize('path',FILES)
def test_anonymous_fixed_download_matches_packaged_file_and_head(path):
    source,media,filename=FILES[path]
    with TestClient(app) as c:
        r=c.get(path);assert r.status_code==200 and r.content==(ROOT/source).read_bytes()
        assert r.headers['content-type'].startswith(media)
        assert r.headers['content-disposition'].startswith('attachment;') and filename in r.headers['content-disposition']
        assert 'set-cookie' not in r.headers and 'access-control-allow-origin' not in r.headers
        assert r.headers['x-content-type-options']=='nosniff' and r.headers['cache-control']=='no-store'
        assert "default-src 'none'" in r.headers['content-security-policy']
        head=c.head(path);assert head.status_code==200 and not head.content
        assert head.headers['content-length']==str(len(r.content))
        query=c.get(path+'?path=/etc/shadow');assert query.status_code==200 and query.content==r.content


@pytest.mark.parametrize('path',['/','/index.html','/favicon.svg','/docs','/redoc','/openapi.json',
    '/api/health','/api/auth/me','/api/auth/login','/api/instances','/api/agent-config','/api/agent-setup',
    '/api/instances/1/config','/api/instances/1/agent-update','/profiles/native',
    '/api/agent-installer/','/api/agent-package/','/api/agent-package/../agent-config',
    '/api/agent-package/%2e%2e/agent-config','/api/agent-package/%2fetc%2fshadow',
    '/.env','/%2eenv','/backend/settings.py','/api/agent-guide','/downloads/'])
def test_management_and_arbitrary_paths_are_not_available(path):
    with TestClient(app) as c:
        for method in ('GET','HEAD'):
            r=c.request(method,path,follow_redirects=False)
            assert r.status_code==404,(method,path,r.status_code)
            assert 'location' not in r.headers and 'set-cookie' not in r.headers


@pytest.mark.parametrize('method',['POST','PUT','PATCH','DELETE','OPTIONS','TRACE','CONNECT'])
def test_no_upload_or_management_method(method):
    with TestClient(app) as c:
        r=c.request(method,'/api/agent-package',content=b'private data')
        assert r.status_code==405 and r.headers['allow']=='GET, HEAD'
        assert 'private data' not in r.text


@pytest.mark.parametrize('headers',[{'Range':'bytes=0-1'},{'Range':'bytes=0-1,2-3,4-5'}])
def test_ranges_are_rejected(headers):
    with TestClient(app) as c:assert c.get('/api/agent-package',headers=headers).status_code==416


def test_get_bodies_are_not_accepted():
    with TestClient(app) as c:
        assert c.request('GET','/api/agent-installer',content=b'upload').status_code==400
        assert c.request('GET','/api/agent-installer',headers={'Transfer-Encoding':'chunked'}).status_code==400


def test_standalone_service_starts_without_env_or_management_imports(tmp_path):
    script='''import sys
from fastapi.testclient import TestClient
from backend.agent_downloads import app
assert not any(m in sys.modules for m in ['backend.main','backend.settings','backend.db','agent.main'])
with TestClient(app) as c:
 assert c.get('/api/agent-package').status_code==200
 assert c.get('/api/auth/me').status_code==404
'''
    result=subprocess.run([sys.executable,'-c',script],cwd=tmp_path,env={'PATH':os.environ['PATH'],'PYTHONPATH':str(ROOT)},capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def settings(origin):
    return Settings(_env_file=None,db_password='fake-db',encryption_key='fake-key',session_secret='s'*48,
                    admin_password='test-password',agent_download_origin=origin)


@pytest.mark.parametrize('value,expected',[(None,None),('',None),('https://download.example.com/','https://download.example.com'),
    ('http://192.168.10.70:8101','http://192.168.10.70:8101'),('https://[2001:db8::1]:8443','https://[2001:db8::1]:8443')])
def test_optional_download_origin(value,expected):assert settings(value).agent_download_origin==expected


@pytest.mark.parametrize('value',['ftp://download.example.com','https://u:p@download.example.com','https://download.example.com/path',
    'https://download.example.com?q=1','https://download.example.com#frag','https://download.example.com:invalid',
    'https://download.example.com:65536','https://download.example.com\n','https://download.example.com/a b','https://',
    'https://download.example.com\\evil','https://download.example.com/"'])
def test_invalid_download_origin_rejected(value):
    with pytest.raises(ValidationError):settings(value)


@pytest.mark.parametrize('origin',[None,'https://download.example.com'])
def test_authenticated_setup_and_update_use_separate_origin_and_same_pinned_files(api,monkeypatch,origin):
    from backend import main
    c,factory,state=api;monkeypatch.setattr(main.settings,'agent_download_origin',origin)
    expected=origin or main.settings.app_origin
    defaults=c.get('/api/agent-setup');assert defaults.status_code==200
    assert defaults.json()=={'download_origin':expected,'pending_setup':None}
    setup=c.post('/api/agent-setup',json=values());assert setup.status_code==200,setup.text
    update=c.post('/api/instances/1/agent-update',json={});assert update.status_code==200
    for command in [setup.json()['command'],update.json()['command']]:
        assert expected+'/api/agent-installer' in command and 'HAPROXY_SOURCE_URL='+expected in command
        assert hashlib.sha256((ROOT/'scripts/install-agent.sh').read_bytes()).hexdigest() in command
        assert hashlib.sha256((ROOT/'downloads/haproxy-management-docker.zip').read_bytes()).hexdigest() in command
        if origin:assert main.settings.app_origin not in command
    with TestClient(app) as public:
        for path in ['/api/agent-setup','/api/instances/1/agent-update']:
            assert public.post(path,json=values()).status_code==405
        data=public.get('/api/agent-package').content
        from io import BytesIO
        with zipfile.ZipFile(BytesIO(data)) as z:
            assert all(setup.json()['instance']['token'].encode() not in z.read(name) for name in z.namelist())
    assert state['calls']==[]


@pytest.mark.parametrize('name',['compose.yaml','compose.registry.yaml','docker-compose.yml'])
def test_compose_download_service_has_no_management_credentials_or_mounts(name,tmp_path):
    # Stub values; never print the actual local .env into test output.
    if not shutil.which('docker'):pytest.skip('Docker Compose unavailable')
    sanitized=tmp_path/name
    sanitized.write_text((ROOT/name).read_text().replace('env_file: .env','env_file: /dev/null'))
    variables={k:'test-value' for k in ['DB_HOST','DB_NAME','DB_USER','DB_PASSWORD','ENCRYPTION_KEY','SESSION_SECRET','ADMIN_PASSWORD']}
    variables['APP_ORIGIN']='http://localhost:8100'
    result=subprocess.run(['docker','compose','--env-file','/dev/null','-f',str(sanitized),'--profile','agent-downloads','config','--format','json'],env=os.environ|variables,capture_output=True,text=True)
    if result.returncode!=0 and ('No such file' in result.stderr or 'not found' in result.stderr):pytest.skip('Docker Compose unavailable')
    assert result.returncode==0,result.stderr
    config=json.loads(result.stdout);service=config['services']['agent-downloads']
    assert 'env_file' not in service and not service.get('environment') and not service.get('volumes')
    assert service['read_only'] and service['cap_drop']==['ALL'] and service['profiles']==['agent-downloads']
    assert 'backend.agent_downloads:app' in service['command'] and 'backend.main:app' not in service['command']
    assert service['ports'][0]['target']==8001 and service['ports'][0]['published']=='8101'
    assert set(service['networks'])=={'agent-downloads'} and set(config['services']['app']['networks'])=={'default'}


def test_archive_contains_updated_compose_and_download_instructions_without_private_env():
    with zipfile.ZipFile(ROOT/'downloads/haproxy-management-docker.zip') as z:
        assert 'haproxy-management/.env' not in z.namelist()
        for source in ['docker-compose.yml','.env.example','docs/AGENT_DOWNLOADS.md']:
            assert z.read('haproxy-management/'+source)==(ROOT/source).read_bytes()
