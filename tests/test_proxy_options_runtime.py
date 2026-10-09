"""Opt-in native/Docker lab verifies path/headers and authentication over real HTTP."""
import http.server
import json
import os
import secrets
import socket
import subprocess
import threading
import time

import httpx
import pytest
from backend import basic_auth
from backend.generator import generate
from backend.haproxy_config import import_config
from backend.schemas import Document
from tests.test_proxy_options import OPTIONS

pytestmark=pytest.mark.skipif(os.environ.get('HAPROXY_BASIC_AUTH_LAB')!='1',reason='Requires isolated HAProxy lab')
BINARY=os.environ.get('HAPROXY_BASIC_AUTH_BINARY','/usr/sbin/haproxy')


def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


class HTTP(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        content=json.dumps({'path':self.path,'headers':dict(self.headers)}).encode()
        self.send_response(200);self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
    def log_message(self,*args):pass


@pytest.mark.parametrize('kind',['native','docker'])
@pytest.mark.parametrize('scenario',['host','imported','pool','legacy'])
def test_real_http_options_removal_roundtrip_and_basic_auth(tmp_path,kind,scenario):
    for parent in [tmp_path,*tmp_path.parents]:
        if str(parent).startswith('/tmp/pytest-of-root'):parent.chmod(0o755)
    tmp_path.chmod(0o755)
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),HTTP)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    listen=port();service={'address':'127.0.0.1','port':server.server_port}
    group={1:{'id':1,'realm':'Members','users':[{'username':'member','hash':basic_auth.hash_password('test-password')}]}}
    if scenario=='host':
        doc=Document(http_port=listen,hosts=[{'id':'app','domain':'app.example.com','aliases':['www.example.com'],'servers':[service],'proxy_options':OPTIONS,'basic_auth_group':1},
                                          {'id':'other','domain':'other.example.com','servers':[service]}])
    elif scenario=='pool':
        doc=Document(http_port=port(),frontends=[{'name':'incoming','mode':'http','port':listen,'backend':'web'}],
                     backends=[{'name':'web','mode':'http','servers':[service],'proxy_options':OPTIONS}])
    else:
        original=f'''global
 maxconn 100
defaults
 mode http
 timeout connect 1s
 timeout client 5s
 timeout server 5s
frontend incoming
 bind :{listen}
 acl app hdr(host) -i app.example.com www.example.com
 use_backend web if app
 acl other hdr(host) -i other.example.com
 use_backend web if other
backend web
 http-request set-path /old%[path]
 server srv 127.0.0.1:{server.server_port} check
'''
        if scenario=='legacy':
            original=original.replace(' http-request set-path /old%[path]\n', ' http-request auth realm Legacy if { hdr(host) -i app.example.com www.example.com } !{ http_auth(users) }\n')+'userlist users\n user member insecure-password test-password\n'
        doc=Document.model_validate(import_config(original,'a'*64)['document'])
        if scenario!='legacy':doc.imported_routes[0].basic_auth_group=1
        doc.imported_backends[0].proxy_options=OPTIONS if scenario!='legacy' else [line.replace('set-header Host %[req.hdr(host)]','set-header Host backend.internal') for line in OPTIONS]
    def request(host='app.example.com',auth=('member','test-password')):
        return httpx.get(f'http://127.0.0.1:{listen}/login?token=test',headers={'Host':host},auth=auth,timeout=2,trust_env=False)
    process=None;container=None
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
        stop();config=generate(value,{'runtime_socket_config':'/tmp/options-runtime.sock','cert_dir_config':'/etc/certs'},group)
        primary=tmp_path/'haproxy.cfg';primary.write_text(config)
        if kind=='native':
            check=subprocess.run([BINARY,'-c','-f',str(primary)],capture_output=True,text=True);assert check.returncode==0,check.stderr
            process=subprocess.Popen([BINARY,'-W','-db','-f',str(primary)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        else:
            container='hc-options-'+secrets.token_hex(5)
            check=subprocess.run(['docker','run','--rm','--user','99:99','-v',str(tmp_path)+':/etc/haproxy','haproxy:3.2.25','haproxy','-c','-f','/etc/haproxy/haproxy.cfg'],capture_output=True,text=True);assert check.returncode==0,check.stderr
            subprocess.run(['docker','run','-d','--name',container,'--user','99:99','--network','host','-v',str(tmp_path)+':/etc/haproxy','haproxy:3.2.25','haproxy','-W','-db','-f','/etc/haproxy/haproxy.cfg'],check=True,capture_output=True)
        for _ in range(100):
            try:
                response=request()
                if response.status_code==200:return config
            except httpx.TransportError:pass
            time.sleep(.1)
        raise AssertionError('HAProxy did not start')
    try:
        config=launch(doc)
        result=request().json();headers={k.lower():v for k,v in result['headers'].items()}
        assert result['path']=='/reset-password/login?token=test'
        assert headers['host']==('backend.internal' if scenario=='legacy' else 'app.example.com')
        assert headers['x-forwarded-host']==('backend.internal' if scenario=='legacy' else 'app.example.com')
        assert headers['x-forwarded-proto']=='https' and headers['x-forwarded-port']=='443'
        assert headers['x-forwarded-for']=='127.0.0.1'
        if scenario!='pool':
            assert request(auth=None).status_code==401
            assert request('www.example.com',auth=None).status_code==401
            assert request('www.example.com').status_code==200
            if scenario!='legacy':assert 'authorization' not in headers
        other=request('other.example.com',auth=None);assert other.status_code==200
        assert other.json()['path']==('/login?token=test' if scenario=='host' else '/reset-password/login?token=test')
        again=Document.model_validate(import_config(config,'b'*64)['document'])
        owner=again.hosts[0] if scenario=='host' else again.imported_backends[0] if scenario in ('imported','legacy') else again.backends[0]
        assert owner.proxy_options==(OPTIONS if scenario!='legacy' else [line.replace('set-header Host %[req.hdr(host)]','set-header Host backend.internal') for line in OPTIONS])
        owner.proxy_options=['http-request set-header X-Forwarded-Proto %[ssl_fc,iif(https,http)]','http-request set-header X-Forwarded-Port %[dst_port]'];launch(again)
        dynamic={k.lower():v for k,v in request().json()['headers'].items()};assert dynamic['x-forwarded-proto']=='http' and dynamic['x-forwarded-port']==str(listen)
        owner.proxy_options=[];launch(again)
        result=request().json();assert result['path']=='/login?token=test'
        assert not any(k.lower().startswith('x-forwarded-') for k in result['headers'])
        if scenario!='pool':assert request(auth=None).status_code==401
    finally:stop();server.shutdown();server.server_close()
