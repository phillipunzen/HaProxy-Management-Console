"""Domain-scoped certificate choice, import preservation and transactional files."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import hashlib
import os

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import HTTPException

from agent import main as agent
from agent import tls_bindings as files
from backend import tls_bindings as tls
from backend.generator import generate
from backend.haproxy_config import import_config
from backend.schemas import Document,Host


def certificate(path,domains,serial):
    key=ec.generate_private_key(ec.SECP256R1());name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,domains[0])]);now=datetime.now(timezone.utc)
    cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(serial).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=10)).add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in domains]),False).sign(key,hashes.SHA256())
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM)+key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    return cert


@pytest.fixture
def setup(tmp_path):
    directory=tmp_path/'certs';directory.mkdir()
    certificate(directory/'default.pem',['app.example.com','other.example.com','*.example.com'],1)
    certificate(directory/'chosen.pem',['*.example.com'],2)
    original=f'defaults\n mode http\n timeout connect 5s\n timeout client 30s\n timeout server 30s\nfrontend edge\n bind :8443 ssl crt {directory}/default.pem alpn h2,http/1.1\n acl app hdr(host) -i app.example.com\n use_backend shared if app\nbackend shared\n http-request return status 200\n'
    path=tmp_path/'haproxy.cfg';path.write_text(original)
    profile={'kind':'native','config_path':str(path),'cert_dir':str(directory),'cert_dir_config':str(directory)}
    doc=Document.model_validate(import_config(original,hashlib.sha256(original.encode()).hexdigest())['document'])
    doc.imported_routes[0].certificate='chosen'
    config=generate(doc,{'cert_dir_config':str(directory)})
    return profile,doc,config,path


def test_selected_wildcard_certificate_has_exact_domain_filter_and_default_is_preserved(setup):
    p,_,config,_=setup;plan=tls.read(config)[0];text=files.content(p,plan).decode()
    assert text.splitlines()[0].endswith('/default.pem !*')
    assert '/chosen.pem app.example.com\n' in text
    assert '/default.pem other.example.com *.example.com !app.example.com\n' in text
    assert text.count('app.example.com')==2


def test_reimport_retains_site_assignment_and_removal_restores_original_bind(setup):
    p,_,config,_=setup
    again=Document.model_validate(import_config(config,hashlib.sha256(config.encode()).hexdigest())['document'])
    assert again.imported_routes[0].certificate=='chosen'
    regenerated=generate(again,{'cert_dir_config':p['cert_dir_config']})
    assert tls.read(regenerated)==tls.read(config)
    again.imported_routes[0].certificate=None
    cleared=generate(again,{'cert_dir_config':p['cert_dir_config']})
    assert 'crt-list' not in cleared and tls.PREFIX not in cleared
    assert 'crt '+p['cert_dir_config']+'/default.pem alpn h2,http/1.1' in cleared


def test_conflicting_certificate_selections_for_url_paths_are_rejected(setup):
    p,_,_,_=setup
    doc=Document(tls_enabled=True,hosts=[Host(id='a',domain='app.example.com',path='/',certificate='chosen',servers=[{'address':'192.0.2.1'}]),Host(id='b',domain='app.example.com',path='/admin',certificate='default',servers=[{'address':'192.0.2.1'}])])
    with pytest.raises(ValueError,match='verschiedenen Pfaden'):generate(doc,{'cert_dir_config':p['cert_dir_config'],'runtime_socket_config':'/run/admin.sock'})


def test_validation_creates_only_temporary_lists_and_apply_rolls_them_back(setup):
    p,_,config,_=setup;directory=Path(p['cert_dir'])/'.control-tls';target=directory/Path(tls.read(config)[0]['path']).name
    with files.materialize(p,config,agent.atomic):assert target.exists()
    assert not directory.exists()
    with pytest.raises(RuntimeError):
        with files.materialize(p,config,agent.atomic,persist=True):
            assert target.exists();raise RuntimeError('validation or reload failed')
    assert not directory.exists()
    with files.materialize(p,config,agent.atomic,persist=True):assert target.exists()
    old=target.read_bytes();target.write_bytes(b'previous bytes\n')
    with pytest.raises(RuntimeError):
        with files.materialize(p,config,agent.atomic,persist=True):
            assert target.read_bytes()==old;raise RuntimeError('reload failed')
    assert target.read_bytes()==b'previous bytes\n'


def test_agent_umask_does_not_hide_lists_from_unprivileged_haproxy(setup):
    p,_,config,_=setup;mask=os.umask(0o077)
    try:
        with files.materialize(p,config,agent.atomic):
            assert (Path(p['cert_dir'])/'.control-tls').stat().st_mode & 0o777==0o755
    finally:os.umask(mask)


def test_directory_binding_enumerates_pems_and_keeps_other_domains(setup):
    p,doc,_,_=setup;doc.imported_config=doc.imported_config.replace('/default.pem alpn','/ alpn')
    config=generate(doc,{'cert_dir_config':p['cert_dir_config']});text=files.content(p,tls.read(config)[0]).decode()
    assert '/chosen.pem app.example.com' in text and 'other.example.com' in text


def test_missing_certificate_mismatched_domain_and_cross_profile_directory_fail_before_writes(setup):
    p,doc,config,_=setup;chosen=Path(p['cert_dir'])/'chosen.pem';chosen.unlink()
    with pytest.raises(HTTPException):
        with files.materialize(p,config,agent.atomic,persist=True):pass
    assert not (chosen.parent/'.control-tls').exists()
    certificate(chosen,['unrelated.example.net'],3)
    with pytest.raises(HTTPException) as error:
        with files.materialize(p,config,agent.atomic,persist=True):pass
    assert 'deckt app.example.com nicht ab' in str(error.value.detail)
    with pytest.raises(HTTPException):
        with files.materialize(p|{'cert_dir_config':'/other/profile'},config,agent.atomic,persist=True):pass


def test_symlink_and_writable_list_directory_are_rejected(setup,tmp_path):
    p,_,config,_=setup;directory=Path(p['cert_dir'])/'.control-tls';outside=tmp_path/'outside';outside.mkdir();directory.symlink_to(outside,target_is_directory=True)
    with pytest.raises(HTTPException):
        with files.materialize(p,config,agent.atomic):pass
    directory.unlink();directory.mkdir();directory.chmod(0o777)
    with pytest.raises(HTTPException):
        with files.materialize(p,config,agent.atomic):pass
    assert not list(outside.iterdir())


def test_agent_apply_rolls_back_configuration_and_new_list_on_reload_failure(setup,monkeypatch,tmp_path):
    p,_,config,path=setup;old=path.read_text();monkeypatch.setattr(agent,'STATE_DIR',tmp_path/'state');agent.STATE_DIR.mkdir()
    monkeypatch.setattr(agent,'validate',lambda *args:'valid');monkeypatch.setattr(agent,'info',lambda *args:{'Pid':'1'})
    calls=[]
    def reload(profile):
        calls.append(path.read_text())
        if len(calls)==1:raise HTTPException(502,'reload failed')
        return {'Pid':'2'}
    monkeypatch.setattr(agent,'reload_service',reload)
    with pytest.raises(HTTPException):agent.apply('test',agent.ConfigIn(config=config,expected_hash=agent.sha(old)),p)
    assert path.read_text()==old and calls==[config,old]
    assert not (Path(p['cert_dir'])/'.control-tls').exists()


def test_agent_successful_apply_keeps_list_and_metadata(setup,monkeypatch,tmp_path):
    p,_,config,path=setup;monkeypatch.setattr(agent,'STATE_DIR',tmp_path/'state');agent.STATE_DIR.mkdir()
    monkeypatch.setattr(agent,'validate',lambda *args:'valid');monkeypatch.setattr(agent,'info',lambda *args:{'Pid':'1'});monkeypatch.setattr(agent,'reload_service',lambda p:{'Pid':'2'})
    assert agent.apply('test',agent.ConfigIn(config=config,expected_hash=agent.sha(path.read_text())),p)['applied']
    assert path.read_text()==config and Path(tls.read(config)[0]['path']).is_file()


def test_manual_binding_changes_and_invalid_metadata_are_rejected(setup):
    _,_,config,_=setup
    with pytest.raises(ValueError,match='manuell'):tls.restore(config.replace('alpn h2,http/1.1','alpn http/1.1'))
    with pytest.raises(ValueError,match='metadaten'):tls.read(tls.PREFIX+'bad!')
