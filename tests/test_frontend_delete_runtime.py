"""Opt-in isolated native/Docker lab: deleted ports close, other traffic survives."""
import os
import secrets
import subprocess
import threading
import time

import httpx
import pytest
from http.server import ThreadingHTTPServer
from pathlib import Path
from backend.frontend_delete import plan
from backend.generator import generate
from backend.haproxy_config import import_config,parse_sections
from backend.schemas import Document
from tests.test_proxy_options_runtime import HTTP,port,BINARY

pytestmark=pytest.mark.skipif(os.environ.get('HAPROXY_BASIC_AUTH_LAB')!='1',reason='Requires isolated HAProxy lab')

@pytest.mark.parametrize('kind',['native','docker'])
@pytest.mark.parametrize('scenario',['imported_http','imported_tcp','listen','native_shared'])
def test_deleted_port_closes_shared_pool_and_unrelated_traffic_survive(tmp_path,kind,scenario):
    for parent in [tmp_path,*tmp_path.parents]:
        if str(parent).startswith('/tmp/pytest-of-root'):parent.chmod(0o755)
    tmp_path.chmod(0o755)
    server=ThreadingHTTPServer(('127.0.0.1',0),HTTP);threading.Thread(target=server.serve_forever,daemon=True).start()
    removed_port,kept_port=port(),port();service={'address':'127.0.0.1','port':server.server_port}
    if scenario=='native_shared':
        target='public_http'
        doc=Document(http_port=removed_port,frontends=[{'name':'kept','mode':'http','bind_address':'127.0.0.1','port':kept_port}],hosts=[{'id':'deleted','domain':'deleted.example.com','servers':[service]},{'id':'kept','frontend':'kept','domain':'kept.example.com','servers':[service]}])
    else:
        target='incoming';tcp=scenario=='imported_tcp';listen=scenario=='listen'
        config=f'''defaults
 mode http
 timeout connect 1s
 timeout client 5s
 timeout server 5s
{'listen' if listen else 'frontend'} incoming
 bind 127.0.0.1:{removed_port}
 mode {'tcp' if tcp else 'http'}
'''+(f' server direct 127.0.0.1:{server.server_port} check\n' if listen else ' default_backend '+('stream' if tcp else 'shared')+'\n')+f'''frontend kept
 bind 127.0.0.1:{kept_port}
 default_backend shared
backend shared
 mode http
 server origin 127.0.0.1:{server.server_port} check
'''+(f'backend stream\n mode tcp\n server origin 127.0.0.1:{server.server_port} check\n' if tcp else '')
        doc=Document.model_validate(import_config(config,'a'*64)['document'])
    process=None;container=None
    cap={'runtime_socket_config':'/tmp/hc-front-delete-'+secrets.token_hex(5)+'.sock','cert_dir_config':'/etc/certs'}
    def request(p,domain):return httpx.get(f'http://127.0.0.1:{p}/test',headers={'Host':domain},timeout=2,trust_env=False)
    def stop():
        nonlocal process,container
        if process:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            process=None
        if container:subprocess.run(['docker','rm','-f',container],capture_output=True);container=None
    def launch(value):
        nonlocal process,container
        stop();config=generate(value,cap);primary=tmp_path/'haproxy.cfg';primary.write_text(config)
        if kind=='native':
            check=subprocess.run([BINARY,'-c','-f',str(primary)],capture_output=True,text=True);assert check.returncode==0,check.stderr
            process=subprocess.Popen([BINARY,'-W','-db','-f',str(primary)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        else:
            args=['--user','99:99','-v',str(tmp_path)+':/etc/haproxy']
            check=subprocess.run(['docker','run','--rm',*args,'haproxy:3.2.25','haproxy','-c','-f','/etc/haproxy/haproxy.cfg'],capture_output=True,text=True);assert check.returncode==0,check.stderr
            container='hc-front-delete-'+secrets.token_hex(5)
            subprocess.run(['docker','run','-d','--name',container,'--network','host',*args,'haproxy:3.2.25','haproxy','-W','-db','-f','/etc/haproxy/haproxy.cfg'],check=True,capture_output=True)
        for _ in range(100):
            try:
                if request(kept_port,'kept.example.com').status_code==200:return config
            except httpx.TransportError:pass
            time.sleep(.1)
        raise AssertionError('HAProxy did not start')
    try:
        launch(doc);assert request(removed_port,'deleted.example.com').status_code==200
        changed,_=plan(doc,target);config=launch(changed)
        with pytest.raises(httpx.ConnectError):request(removed_port,'deleted.example.com')
        assert request(kept_port,'kept.example.com').status_code==200
        assert target not in {s.name for s in parse_sections(config)[1] if s.kind in ('frontend','listen')}
        if scenario!='native_shared':assert 'backend shared\n' in config
        again=Document.model_validate(import_config(config,'b'*64)['document']);launch(again)
        with pytest.raises(httpx.ConnectError):request(removed_port,'deleted.example.com')
        assert request(kept_port,'kept.example.com').status_code==200
    finally:stop();server.shutdown();server.server_close();Path(cap['runtime_socket_config']).unlink(missing_ok=True)
