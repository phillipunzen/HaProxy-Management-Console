import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from agent import main as agent
from agent.setup_checks import docker_directories, tls_options


def certificate(tmp_path,names=('agent.example.de',),expired=False):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    now=datetime.now(timezone.utc)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,names[0])])
    cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=2)).not_valid_after(now+timedelta(days=-1 if expired else 1)).add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]),False).sign(key,hashes.SHA256())
    cert_file=tmp_path/'cert.pem';key_file=tmp_path/'key.pem'
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    return {'hostname':'agent.example.de','cert_file':str(cert_file),'key_file':str(key_file)}


def test_tls_accepts_chain_and_key_and_single_level_wildcard(tmp_path):
    for names in [('agent.example.de',),('*.example.de',)]:
        tls=certificate(tmp_path,names)
        options=tls_options({'tls':tls})
        assert '--ssl-certfile '+tls['cert_file'] in options
        assert '--ssl-keyfile '+tls['key_file'] in options
    assert tls_options({})==''


@pytest.mark.parametrize('case',['expired','hostname','nested_wildcard','key','missing'])
def test_tls_preflight_rejects_invalid_certificates(tmp_path,case):
    tls=certificate(tmp_path,('*.example.de',) if case=='nested_wildcard' else ('agent.example.de',),expired=case=='expired')
    if case=='hostname':tls['hostname']='other.example.de'
    if case=='nested_wildcard':tls['hostname']='nested.agent.example.de'
    if case=='missing':tls['cert_file']=str(tmp_path/'missing.pem')
    if case=='key':Path(tls['key_file']).write_text('invalid key')
    with pytest.raises(SystemExit,match='Agent-TLS'):tls_options({'tls':tls})


@pytest.fixture
def docker(tmp_path):
    config=tmp_path/'config';config.mkdir();run_dir=tmp_path/'run';run_dir.mkdir();certs=tmp_path/'certs';certs.mkdir();vhosts=tmp_path/'vhosts';vhosts.mkdir()
    primary=config/'haproxy.cfg';primary.write_text('global\ndefaults\n mode http\nfrontend web\n bind :8080\n default_backend app\n')
    extra=vhosts/'01-app.cfg';extra.write_text('backend app\n server web 127.0.0.1:8000\n')
    p=dict(kind='docker',container='test',config_path=str(primary),container_config_dir='/usr/local/etc/haproxy',runtime_socket=str(run_dir/'admin.sock'),runtime_socket_config='/run/haproxy/admin.sock',cert_dir=str(certs),cert_dir_config='/usr/local/etc/haproxy/certs')
    mounts=[{'Source':str(config),'Destination':'/usr/local/etc/haproxy','RW':False},{'Source':str(run_dir),'Destination':'/run/haproxy','RW':True},{'Source':str(certs),'Destination':'/usr/local/etc/haproxy/certs','RW':False},{'Source':str(vhosts),'Destination':'/usr/local/etc/haproxy/vhosts','RW':False}]
    def run(args):
        if args[3]=='{{json .Mounts}}':return json.dumps(mounts)
        return json.dumps(['haproxy','-f','/usr/local/etc/haproxy/haproxy.cfg','-f','/usr/local/etc/haproxy/vhosts'])
    return p,mounts,run,primary,extra


def test_all_loaded_files_require_directory_mounts_and_ro_is_supported(docker):
    p,mounts,run,primary,extra=docker
    assert docker_directories(p,run,agent.sha)=={str(primary.parent),str(extra.parent)}


@pytest.mark.parametrize('which',['primary','extra','runtime','hidden_primary'])
def test_single_file_mounts_and_readonly_runtime_rejected(docker,which):
    p,mounts,run,primary,extra=docker
    if which=='primary':mounts[0].update(Source=str(primary),Destination='/usr/local/etc/haproxy/haproxy.cfg')
    if which=='extra':mounts.append({'Source':str(extra),'Destination':'/usr/local/etc/haproxy/vhosts/01-app.cfg','RW':False})
    if which=='runtime':mounts[1]['RW']=False
    if which=='hidden_primary':mounts.append({'Source':str(primary),'Destination':'/usr/local/etc/haproxy/haproxy.cfg','RW':False})
    with pytest.raises(SystemExit,match='Docker-Verzeichnis-Mount'):docker_directories(p,run,agent.sha)
    assert 'stats socket' not in primary.read_text()


def test_https_agent_handshake_authentication_and_hostname_verification(tmp_path):
    import os, socket, ssl, subprocess, sys, time
    tls=certificate(tmp_path)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    config=tmp_path/'agent.json'
    profile=dict(kind='native',token='synthetic-test-token-'+'x'*45,config_path=str(tmp_path/'haproxy.cfg'),runtime_socket=str(tmp_path/'admin.sock'),runtime_socket_config='/run/haproxy/admin.sock',cert_dir=str(tmp_path/'certs'),cert_dir_config='/etc/haproxy/certs')
    config.write_text(json.dumps({'profiles':{'test':profile}}))
    env=dict(os.environ,AGENT_CONFIG=str(config),AGENT_STATE_DIR=str(tmp_path/'state'))
    process=subprocess.Popen([sys.executable,'-m','uvicorn','agent.main:app','--host','127.0.0.1','--port',str(port),'--ssl-certfile',tls['cert_file'],'--ssl-keyfile',tls['key_file'],'--no-access-log'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        context=ssl.create_default_context(cafile=tls['cert_file'])
        for attempt in range(100):
            try:
                connection=context.wrap_socket(socket.create_connection(('127.0.0.1',port),timeout=2),server_hostname='agent.example.de');break
            except ConnectionRefusedError:
                assert process.poll() is None,'HTTPS agent exited during startup';time.sleep(.05)
        else:pytest.fail('HTTPS agent did not start')
        with connection:
            assert connection.version() in ('TLSv1.2','TLSv1.3')
            connection.sendall(b'GET /profiles/test HTTP/1.1\r\nHost: agent.example.de\r\nConnection: close\r\n\r\n')
            response=connection.recv(4096)
            assert b' 401 ' in response
        with socket.create_connection(('127.0.0.1',port),timeout=2) as raw:
            with pytest.raises(ssl.SSLCertVerificationError):context.wrap_socket(raw,server_hostname='wrong.example.de')
    finally:
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
