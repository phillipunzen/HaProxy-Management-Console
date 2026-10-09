"""Exercise the installer's config changes with temporary paths and fake services."""
import base64
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from agent import main as agent

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (ROOT/'scripts/install-agent.sh').read_text().split(".venv/bin/python - <<'PY'\n",1)[1].split('\nPY\n',1)[0]


@pytest.fixture
def setup(tmp_path,monkeypatch):
    config=tmp_path/'haproxy/haproxy.cfg';config.parent.mkdir()
    original='global\n    maxconn 100\ndefaults\n    mode http\n'
    config.write_text(original)
    p=dict(kind='native',token='t'*64,service='test',haproxy_binary='/fake/haproxy',
           config_path=str(config),runtime_socket=str(tmp_path/'run/admin.sock'),
           runtime_socket_config='/run/haproxy/admin.sock',cert_dir=str(tmp_path/'certs'),
           cert_dir_config='/etc/haproxy/certs')
    profile_file=tmp_path/'etc/agent.json'
    code=SCRIPT.replace('/etc/haproxy-control/agent.json',str(profile_file)).replace('/var/lib/haproxy-control',str(tmp_path/'state')).replace('/etc/systemd/system',str(tmp_path/'systemd'))
    calls=[]
    monkeypatch.setattr(agent,'run',lambda args,**kw:calls.append(args) or 'active')
    monkeypatch.setattr(agent,'validate',lambda *args:'valid')
    monkeypatch.setattr(agent,'info',lambda *args:{'Pid':'123'})
    def atomic(path,content,*args):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
    monkeypatch.setattr(agent,'atomic',atomic)
    def execute():
        monkeypatch.setenv('HAPROXY_AGENT_SETUP_B64',base64.b64encode(json.dumps({'profile_name':'test','profile':p,'bind':'192.168.10.71','port':9101}).encode()).decode())
        exec(compile(code,'installer','exec'),{})
    return execute,p,config,original,profile_file,calls


def test_native_installer_preserves_existing_config_and_other_profiles(setup):
    execute,p,config,original,profile_file,calls=setup
    profile_file.parent.mkdir();profile_file.write_text(json.dumps({'profiles':{'other':{'token':'keep','config_path':'/other.cfg','runtime_socket':'/other.sock'}}}))
    execute()
    assert 'maxconn 100' in config.read_text()
    assert config.read_text().count('stats socket')==1
    assert list(config.parent.glob('*.before-agent-*'))[0].read_text()==original
    data=json.loads(profile_file.read_text())
    assert data['profiles']['other']['token']=='keep'
    assert data['profiles']['test']['token']==p['token']
    assert ['systemctl','reload','test'] in calls


def test_existing_profile_is_never_overwritten(setup):
    execute,p,config,original,profile_file,calls=setup
    profile_file.parent.mkdir();profile_file.write_text(json.dumps({'profiles':{'test':p|{'token':'keep'}}}))
    with pytest.raises(SystemExit,match='Profil existiert bereits'):execute()
    assert json.loads(profile_file.read_text())['profiles']['test']['token']=='keep'
    assert config.read_text()==original and not calls


def test_failed_reload_restores_haproxy_config(setup,monkeypatch):
    execute,p,config,original,profile_file,calls=setup
    def run(args,**kw):
        if args[1]=='reload':raise HTTPException(502,'test reload failure')
        return 'active'
    monkeypatch.setattr(agent,'run',run)
    with pytest.raises(HTTPException):execute()
    assert config.read_text()==original
    assert not profile_file.exists()


def test_missing_docker_mount_stops_before_changing_config(setup,monkeypatch):
    execute,p,config,original,profile_file,calls=setup
    p.update(kind='docker',container='test',container_config_dir='/usr/local/etc/haproxy')
    monkeypatch.setattr(agent,'run',lambda *args,**kw:'[]')
    with pytest.raises(SystemExit,match='Docker-Verzeichnis-Mount fehlt'):execute()
    assert config.read_text()==original and not profile_file.exists()


def test_shared_certificate_directory_rejected_before_config_change(setup):
    execute,p,config,original,profile_file,calls=setup
    profile_file.parent.mkdir();profile_file.write_text(json.dumps({'profiles':{'other':{'token':'keep','config_path':'/other.cfg','runtime_socket':'/other.sock','cert_dir':p['cert_dir']}}}))
    with pytest.raises(SystemExit,match='eigenes Zertifikatsverzeichnis'):execute()
    assert config.read_text()==original and not calls


@pytest.mark.parametrize('path',[None,'/etc/lego/customer','/var/lib/custom-acme'])
def test_update_grants_only_configured_lego_store_without_resetting_unit_permissions(tmp_path,path):
    source=(ROOT/'scripts/install-agent.sh').read_text()
    code=source.split(" - <<'PY'\nimport json,re\n",1)[1].split('\nPY\n',1)[0]
    code='import json,re\n'+code
    config=tmp_path/'agent.json';value={'profiles':{'test':{'token':'keep-token','lego':{'path':path}} if path else {'token':'keep-token'}}}
    config.write_text(json.dumps(value));before=config.read_bytes()
    code=code.replace('/etc/haproxy-control/agent.json',str(config)).replace('/etc/systemd/system',str(tmp_path/'systemd'))
    exec(compile(code,'update-acme','exec'),{})
    text=(tmp_path/'systemd/haproxy-control-agent.service.d/99-management-acme.conf').read_text()
    assert config.read_bytes()==before
    assert text=='[Service]\n'+('ReadWritePaths=-'+path+'\n' if path else '')
    assert 'ReadWritePaths=\n' not in text


@pytest.mark.parametrize('path',['/etc/lego\nExecStart=other','/etc/lego/%u','/etc/lego/../secret'])
def test_update_rejects_lego_unit_path_injection(tmp_path,path):
    source=(ROOT/'scripts/install-agent.sh').read_text()
    code='import json,re\n'+source.split(" - <<'PY'\nimport json,re\n",1)[1].split('\nPY\n',1)[0]
    config=tmp_path/'agent.json';config.write_text(json.dumps({'profiles':{'test':{'lego':{'path':path}}}}))
    code=code.replace('/etc/haproxy-control/agent.json',str(config)).replace('/etc/systemd/system',str(tmp_path/'systemd'))
    with pytest.raises(SystemExit,match='Ungültiger LEGO-Pfad'):exec(compile(code,'update-acme','exec'),{})
    assert not (tmp_path/'systemd').exists()


def test_archive_installer_includes_proxy_option_schema_dependency(tmp_path):
    import ast,os,subprocess,sys
    from zipfile import ZipFile
    extraction=(ROOT/'scripts/install-agent.sh').read_text().split('python3 - "$haproxy_install_temp/package.zip" "$haproxy_install_dir" <<\'PY\'\n',1)[1].split('\nPY\n',1)[0]
    tree=ast.parse(extraction)
    allowed=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='allowed' for t in node.targets))
    assert 'backend/proxy_options.py' in allowed
    with ZipFile(ROOT/'downloads/haproxy-management-docker.zip') as archive:
        for name in allowed:
            target=tmp_path/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read('haproxy-management/'+name))
    result=subprocess.run([sys.executable,'-c',"from backend.schemas import Host; h=Host(id='app',domain='app.example.com',servers=[{'address':'127.0.0.1'}],proxy_options=['http-request set-path /reset-password%[path]']); assert len(h.proxy_options)==1"],cwd=tmp_path,env=dict(os.environ,PYTHONPATH=str(tmp_path)),capture_output=True,text=True)
    assert result.returncode==0,result.stderr
