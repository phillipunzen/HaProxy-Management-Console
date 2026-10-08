import hashlib
import json
from pathlib import Path
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from agent import main as a

@pytest.fixture
def fixture(tmp_path,monkeypatch):
    config=tmp_path/'haproxy.cfg';config.write_text('old\n')
    p={'kind':'native','token':'a'*40,'service':'test','config_path':str(config),'runtime_socket':str(tmp_path/'admin.sock'),'runtime_socket_config':str(tmp_path/'admin.sock'),'cert_dir':str(tmp_path/'certs'),'cert_dir_config':str(tmp_path/'certs')}
    f=tmp_path/'agent.json';f.write_text(json.dumps({'profiles':{'test':p}}))
    monkeypatch.setattr(a,'CONFIG_PATH',f);monkeypatch.setattr(a,'STATE_DIR',tmp_path/'state')
    with TestClient(a.app) as client:
        yield client,p,config


def body(content='new\n',base='old\n'):
    return {'config':content,'expected_hash':hashlib.sha256(base.encode()).hexdigest()}

def test_agent_auth_and_local_allowlist(fixture):
    c,p,path=fixture
    assert c.get('/profiles/test/config').status_code==401
    assert c.get('/profiles/other/config',headers={'Authorization':'Bearer '+'a'*40}).status_code==401
    data=c.get('/profiles/test',headers={'Authorization':'Bearer '+'a'*40}).json()
    assert 'token' not in data and 'config_path' not in data


def test_failed_validation_does_not_write_config(fixture,monkeypatch):
    c,p,path=fixture
    def invalid(*args): raise HTTPException(422,'Invalid HAProxy config')
    monkeypatch.setattr(a,'validate',invalid)
    result=c.post('/profiles/test/apply',json=body(),headers={'Authorization':'Bearer '+'a'*40})
    assert result.status_code==422
    assert path.read_text()=='old\n'


def test_external_change_detected_before_apply(fixture,monkeypatch):
    c,p,path=fixture;path.write_text('external\n')
    result=c.post('/profiles/test/apply',json=body(),headers={'Authorization':'Bearer '+'a'*40})
    assert result.status_code==409 and path.read_text()=='external\n'


def test_reload_failure_restores_file_and_old_service(fixture,monkeypatch):
    c,p,path=fixture
    monkeypatch.setattr(a,'validate',lambda *args:'valid')
    monkeypatch.setattr(a,'info',lambda *args:{'Pid':'10'})
    count=[]
    def reload(p):
        count.append(path.read_text())
        if len(count)==1: raise HTTPException(502,'reload failed')
        return {'Pid':'12'}
    monkeypatch.setattr(a,'reload_service',reload)
    result=c.post('/profiles/test/apply',json=body(),headers={'Authorization':'Bearer '+'a'*40})
    assert result.status_code==502
    assert path.read_text()=='old\n' and count==['new\n','old\n']
    assert list((a.STATE_DIR/'backups').rglob('*.cfg'))


def test_certificate_key_mismatch_rejected_before_install(fixture):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from datetime import datetime,timedelta,timezone
    c,p,path=fixture;k1=rsa.generate_private_key(65537,2048);k2=rsa.generate_private_key(65537,2048)
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'example.com')])
    cert=x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(k1.public_key()).serial_number(1).not_valid_before(datetime.now(timezone.utc)).not_valid_after(datetime.now(timezone.utc)+timedelta(days=1)).sign(k1,hashes.SHA256())
    pem=cert.public_bytes(serialization.Encoding.PEM)+k2.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    result=c.post('/profiles/test/certificates/import',json={'name':'example','pem':pem.decode()},headers={'Authorization':'Bearer '+'a'*40})
    assert result.status_code==422 and not (Path(p['cert_dir'])/'example.pem').exists()
