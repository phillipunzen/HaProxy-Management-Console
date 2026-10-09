"""Opt-in real HAProxy lab: deleted services disappear while unrelated traffic works."""
import os
import secrets
import subprocess
import threading
import time

import httpx
import pytest
from backend.backend_delete import plan
from backend.generator import generate
from backend.haproxy_config import import_config
from backend.schemas import Document
from tests.test_proxy_options_runtime import HTTP,port,BINARY
from tests.test_backend_delete import CONFIG
from http.server import ThreadingHTTPServer

pytestmark=pytest.mark.skipif(os.environ.get('HAPROXY_BASIC_AUTH_LAB')!='1',reason='Requires isolated HAProxy lab')

@pytest.mark.parametrize('kind',['native','docker'])
@pytest.mark.parametrize('routing',['acl','map'])
def test_removal_and_reimport_stop_deleted_routes_preserve_other_http(tmp_path,kind,routing):
    for parent in [tmp_path,*tmp_path.parents]:
        if str(parent).startswith('/tmp/pytest-of-root'):parent.chmod(0o755)
    tmp_path.chmod(0o755)
    server=ThreadingHTTPServer(('127.0.0.1',0),HTTP);threading.Thread(target=server.serve_forever,daemon=True).start()
    listen=port();original=CONFIG.replace(':8088',f':{listen}').replace('bind :3306',f'bind :{port()}').replace('127.0.0.1:8080',f'127.0.0.1:{server.server_port}').replace('127.0.0.1:8081',f'127.0.0.1:{server.server_port}')
    maps=[]
    if routing=='map':
        original=original.replace(' acl app hdr(host) -i app.example.com www.example.com\n use_backend web if app\n acl route_other hdr(host) -i other.example.com\n use_backend other if route_other\n default_backend web',
                                  ' use_backend %[req.hdr(host),lower,map(/etc/haproxy/vhosts.map,web)]')
        maps=[{'path':'/etc/haproxy/vhosts.map','content':'app.example.com web\nwww.example.com web\nother.example.com other\n'}]
    doc=Document.model_validate(import_config(original,'a'*64,maps)['document'])
    process=None;container=None
    def request(domain):return httpx.get(f'http://127.0.0.1:{listen}/test',headers={'Host':domain},timeout=2)
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
        stop();config=generate(value,{'runtime_socket_config':'/tmp/delete-runtime.sock','cert_dir_config':'/etc/certs'})
        primary=tmp_path/'haproxy.cfg';primary.write_text(config)
        if kind=='native':
            check=subprocess.run([BINARY,'-c','-f',str(primary)],capture_output=True,text=True);assert check.returncode==0,check.stderr
            process=subprocess.Popen([BINARY,'-W','-db','-f',str(primary)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        else:
            check=subprocess.run(['docker','run','--rm','--user','99:99','-v',str(tmp_path)+':/etc/haproxy','haproxy:3.2.25','haproxy','-c','-f','/etc/haproxy/haproxy.cfg'],capture_output=True,text=True);assert check.returncode==0,check.stderr
            container='hc-delete-'+secrets.token_hex(5)
            subprocess.run(['docker','run','-d','--name',container,'--user','99:99','--network','host','-v',str(tmp_path)+':/etc/haproxy','haproxy:3.2.25','haproxy','-W','-db','-f','/etc/haproxy/haproxy.cfg'],check=True,capture_output=True)
        for _ in range(100):
            try:
                if request('other.example.com').status_code==200:return config
            except httpx.TransportError:pass
            time.sleep(.1)
        raise AssertionError('HAProxy did not start')
    try:
        launch(doc);assert request('app.example.com').status_code==200 and request('www.example.com').status_code==200
        changed,_=plan(doc,'web');config=launch(changed)
        assert request('app.example.com').status_code==503 and request('www.example.com').status_code==503
        assert request('other.example.com').status_code==200 and 'backend web\n' not in config
        again=Document.model_validate(import_config(config,'b'*64,maps)['document']);launch(again)
        assert request('app.example.com').status_code==503 and request('other.example.com').status_code==200
    finally:stop();server.shutdown();server.server_close()
