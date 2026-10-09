"""Opt-in isolated native/Docker TLS handshake and agent apply/renewal lab."""
import hashlib
import os
from pathlib import Path
import secrets
import signal
import socket
import ssl
import subprocess
import time

import pytest
from cryptography.hazmat.primitives.serialization import Encoding
from agent import main as agent
from agent import certificates as certificate_jobs
from fastapi import HTTPException
from backend.generator import generate
from backend.haproxy_config import import_config
from backend.schemas import Document
from test_tls_bindings import certificate

pytestmark=pytest.mark.skipif(os.environ.get('HAPROXY_BASIC_AUTH_LAB')!='1',reason='Opt-in isolated HAProxy lab')
BINARY=os.environ.get('HAPROXY_BASIC_AUTH_BINARY','/usr/sbin/haproxy')


def port():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


@pytest.mark.parametrize('kind',['native','docker'])
@pytest.mark.parametrize('domains',[['app.example.com'],['*.example.com'],['app.example.com','other.example.com']])
def test_real_tls_choice_apply_import_renewal_and_restore(tmp_path,monkeypatch,kind,domains):
    domains=domains+['alias.example.com'] if '*.example.com' not in domains else domains
    tmp_path.chmod(0o755);directory=tmp_path/'certs';directory.mkdir();primary=tmp_path/'haproxy.cfg';runtime=tmp_path/'run';runtime.mkdir();runtime.chmod(0o777)
    default=certificate(directory/'default.pem',['app.example.com','other.example.com','*.example.com'],1)
    chosen=certificate(directory/'chosen.pem',domains,2)
    digest=lambda cert:hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest()
    prefix=str(tmp_path) if kind=='native' else '/etc/haproxy';hp,other=port(),port();container='haproxy-tls-lab-'+secrets.token_hex(5)
    original=f'''global
 stats socket {prefix}/run/admin.sock level admin
defaults
 mode http
 timeout connect 5s
 timeout client 30s
 timeout server 30s
frontend edge
 bind 127.0.0.1:{hp} ssl crt {prefix}/certs/default.pem alpn h2,http/1.1
 acl app hdr(host) -i app.example.com
 use_backend shared if app
 default_backend shared
frontend other
 bind 127.0.0.1:{other} ssl crt {prefix}/certs/default.pem
 default_backend shared
backend shared
 http-request return status 200 content-type text/plain string OK
'''
    primary.write_text(original);state=tmp_path/'state';state.mkdir();monkeypatch.setattr(agent,'STATE_DIR',state)
    p={'kind':kind,'config_path':str(primary),'config_sources':[str(primary)],'cert_dir':str(directory),'cert_dir_config':prefix+'/certs','runtime_socket':str(runtime/'admin.sock'),'haproxy_binary':BINARY,'container':container,'container_config_dir':prefix,'cert_uid':99 if kind=='docker' else os.getuid(),'cert_gid':99 if kind=='docker' else os.getgid()}
    monkeypatch.setattr(agent,'PROFILES',{'test':p})
    doc=Document.model_validate(import_config(original,agent.sha(original))['document']);doc.imported_routes[0].certificate='chosen';doc.imported_routes[0].aliases=['alias.example.com'];generated=generate(doc,{'cert_dir_config':prefix+'/certs'})
    process=None
    ctx=ssl.create_default_context();ctx.check_hostname=False;ctx.verify_mode=ssl.CERT_NONE
    def fingerprint(domain,listener=hp):
        with socket.create_connection(('127.0.0.1',listener),timeout=3) as raw:
            with ctx.wrap_socket(raw,server_hostname=domain) as conn:
                value=hashlib.sha256(conn.getpeercert(binary_form=True)).hexdigest()
                conn.sendall(b'GET / HTTP/1.1\r\nHost: app.example.com\r\nConnection: close\r\n\r\n')
                assert b'200' in conn.recv(1024)
                return value
    try:
        if kind=='native':process=subprocess.Popen([BINARY,'-W','-db','-f',str(primary)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        else:subprocess.run(['docker','run','-d','--name',container,'--user','99:99','--network','host','-v',str(tmp_path)+':/etc/haproxy','haproxy:3.2.25','haproxy','-W','-db','-f','/etc/haproxy/haproxy.cfg'],check=True,capture_output=True)
        for _ in range(100):
            try:
                if fingerprint('app.example.com')==digest(default):break
            except OSError:pass
            time.sleep(.1)
        else:raise AssertionError('HAProxy did not start')
        def reload(profile):
            previous=agent.info(profile)['Pid']
            if kind=='native':process.send_signal(signal.SIGUSR2)
            else:subprocess.run(['docker','kill','--signal','USR2',container],check=True,capture_output=True)
            for _ in range(100):
                try:
                    current=agent.info(profile)
                    if current['Pid']!=previous:return current
                except OSError:pass
                time.sleep(.1)
            raise AssertionError('Reload did not activate new worker')
        monkeypatch.setattr(agent,'reload_service',reload)
        agent.validate(p,generated)
        assert not (directory/'.control-tls').exists() and primary.read_text()==original
        assert agent.apply('test',agent.ConfigIn(config=generated,expected_hash=agent.sha(original)),p)['applied']
        assert fingerprint('app.example.com')==digest(chosen)
        assert fingerprint('alias.example.com')==digest(chosen)
        assert fingerprint('alias.example.com',listener=other)==digest(default)
        assert fingerprint('other.example.com')==digest(default)
        assert fingerprint('unknown.invalid')==digest(default)
        assert fingerprint(None)==digest(default)
        assert fingerprint('app.example.com',listener=other)==digest(default)
        with pytest.raises(HTTPException) as error:certificate_jobs.delete(p,'chosen')
        assert error.value.status_code==409 and (directory/'chosen.pem').exists()
        staging=directory/'.staging';staging.mkdir();certificate(staging/'chosen.pem',domains,5)
        assert certificate_jobs.delete(p,'chosen',staging=True)['deleted']
        assert (directory/'chosen.pem').exists() and fingerprint('app.example.com')==digest(chosen)
        again=Document.model_validate(import_config(primary.read_text(),agent.sha(primary.read_text()))['document'])
        assert again.imported_routes[0].certificate=='chosen' and again.imported_routes[0].aliases==['alias.example.com']
        renewal=tmp_path/'renewal.pem';replacement=certificate(renewal,domains,3)
        agent.install_pem(p,'chosen',renewal.read_bytes())
        assert fingerprint('app.example.com')==digest(replacement)
        assert fingerprint('alias.example.com')==digest(replacement)
        assert fingerprint('other.example.com')==digest(default)
        again.imported_routes[0].certificate=None
        cleared=generate(again,{'cert_dir_config':prefix+'/certs'})
        agent.apply('test',agent.ConfigIn(config=cleared,expected_hash=agent.sha(primary.read_text())),p)
        assert fingerprint('app.example.com')==digest(default)
        assert fingerprint('alias.example.com')==digest(default)
        assert certificate_jobs.delete(p,'chosen')['deleted']
        assert not (directory/'chosen.pem').exists()
        assert fingerprint('other.example.com')==digest(default)
    finally:
        if process:process.terminate();process.wait(timeout=10)
        if kind=='docker':subprocess.run(['docker','rm','-f',container],capture_output=True)

@pytest.mark.parametrize('kind',['native','docker'])
@pytest.mark.parametrize('source',['host','pool','imported'])
def test_backend_self_signed_certificate_requires_explicit_insecure_choice(tmp_path,kind,source):
    import http.server
    import threading
    import httpx
    from backend.schemas import Host,ManagedBackend,ManagedFrontend
    from test_basic_auth_runtime import HTTP
    tmp_path.chmod(0o755)
    pem=tmp_path/'backend.pem';certificate(pem,['internal.example.com'],20)
    web=http.server.ThreadingHTTPServer(('127.0.0.1',0),HTTP)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(pem)
    web.socket=context.wrap_socket(web.socket,server_side=True)
    threading.Thread(target=web.serve_forever,daemon=True).start()
    hp=port();container='haproxy-backend-tls-lab-'+secrets.token_hex(5)
    caps={'runtime_socket_config':('/etc/haproxy' if kind=='docker' else str(tmp_path))+'/admin.sock','cert_dir_config':'/etc/haproxy/certs'}
    target={'address':'127.0.0.1','port':web.server_address[1],'tls':True}
    if source=='host':doc=Document(http_port=hp,hosts=[Host(id='app',domain='app.example.com',servers=[target])])
    elif source=='pool':doc=Document(http_port=port(),frontends=[ManagedFrontend(name='edge',port=hp,backend='app')],backends=[ManagedBackend(name='app',servers=[target])])
    else:
        original=f'defaults\n mode http\n timeout connect 1s\n timeout client 5s\n timeout server 5s\nfrontend edge\n bind 127.0.0.1:{hp}\n default_backend app\nbackend app\n server origin 127.0.0.1:{web.server_address[1]} ssl verify required ca-file /etc/ssl/certs/ca-certificates.crt check\n'
        doc=Document.model_validate(import_config(original,hashlib.sha256(original.encode()).hexdigest())['document'])
    selected=(doc.hosts[0] if source=='host' else doc.backends[0] if source=='pool' else doc.imported_backends[0]).servers[0]
    config_file=tmp_path/'haproxy.cfg'
    try:
        for verify in (True,False,True):
            selected.tls_verify=verify;config_file.write_text(generate(doc,caps));process=None;started=False
            try:
                if kind=='native':
                    result=subprocess.run([BINARY,'-c','-f',str(config_file)],capture_output=True,text=True);assert result.returncode==0,result.stderr
                    process=subprocess.Popen([BINARY,'-db','-f',str(config_file)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                else:
                    subprocess.run(['docker','run','-d','--name',container,'--user','0','--network','host','-v',str(tmp_path)+':/etc/haproxy','haproxy:3.2.25','haproxy','-db','-f','/etc/haproxy/haproxy.cfg'],check=True,capture_output=True);started=True
                with httpx.Client(trust_env=False,timeout=3) as client:
                    for _ in range(100):
                        try:
                            response=client.get(f'http://127.0.0.1:{hp}',headers={'Host':'app.example.com'})
                            if response.status_code==(503 if verify else 200):break
                        except httpx.RequestError:pass
                        time.sleep(.1)
                    else:raise AssertionError(f'Expected verify={verify} to reject/accept the self-signed backend')
                    if not verify:assert response.json()['authorization'] is None
                if source=='imported':
                    again=Document.model_validate(import_config(config_file.read_text(),hashlib.sha256(config_file.read_bytes()).hexdigest())['document'])
                    assert again.imported_backends[0].servers[0].tls_verify is verify
            finally:
                if process:process.terminate();process.wait(timeout=10)
                if started:subprocess.run(['docker','rm','-f',container],capture_output=True)
    finally:
        web.shutdown();web.server_close()
