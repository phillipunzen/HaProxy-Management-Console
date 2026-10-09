"""Opt-in real source-address HTTP checks on native and Docker HAProxy."""
import os
import secrets
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer

import httpx
import pytest
from backend import access_control as access,basic_auth
from backend.generator import generate
from backend.haproxy_config import import_config
from backend.schemas import Document,AccessPolicy
from tests.test_proxy_options_runtime import HTTP,port,BINARY

class KeepaliveHTTP(HTTP):
    protocol_version='HTTP/1.1'

pytestmark=pytest.mark.skipif(os.environ.get('HAPROXY_BASIC_AUTH_LAB')!='1',reason='Requires isolated HAProxy lab')

@pytest.mark.parametrize('kind',['native','docker'])
@pytest.mark.parametrize('scenario',['host','imported','map'])
def test_source_network_path_alias_shared_pool_basic_auth_and_clear(tmp_path,kind,scenario):
    for parent in [tmp_path,*tmp_path.parents]:
        if str(parent).startswith('/tmp/pytest-of-root'):parent.chmod(0o755)
    tmp_path.chmod(0o755)
    server=ThreadingHTTPServer(('127.0.0.1',0),KeepaliveHTTP);threading.Thread(target=server.serve_forever,daemon=True).start()
    listen=port();v6=port();service={'address':'127.0.0.1','port':server.server_port}
    policy={'networks':['127.0.0.1/32','::1/128'],'paths':['/admin']}
    groups={1:{'id':1,'realm':'Members','users':[{'username':'member','hash':basic_auth.hash_password('test-password')}]}}
    maps=[]
    if scenario=='host':
        doc=Document(http_port=listen,hosts=[{'id':'app','domain':'app.example.com','aliases':['www.example.com'],'servers':[service],'access_policy':policy,'basic_auth_group':1,'proxy_options':['http-request set-path /rewritten%[path]']},
                                           {'id':'other','domain':'other.example.com','servers':[service]},
                                           {'id':'v6','domain':'v6.example.com','frontend':'ipv6','servers':[service],'access_policy':policy}],
                     frontends=[{'name':'ipv6','mode':'http','bind_address':'::1','port':v6}])
    else:
        routing=' acl app hdr(host) -i app.example.com www.example.com\n use_backend web if app\n acl other_site hdr(host) -i other.example.com\n use_backend web if other_site'
        if scenario=='map':
            routing=' use_backend %[req.hdr(host),lower,map(/etc/haproxy/vhosts.map,fallback)]'
            maps=[{'path':'/etc/haproxy/vhosts.map','content':'app.example.com web\nwww.example.com web\nother.example.com web\n'}]
        original=f'''defaults
 mode http
 timeout connect 1s
 timeout client 5s
 timeout server 5s
frontend incoming
 http-request set-header X-Connection-Port %[fc_src_port]
 http-request set-src hdr(X-Forwarded-For) if {{ hdr(X-Forwarded-For) -m found }}
 bind :{listen}
 bind [::1]:{v6}
{routing}
backend web
 http-request set-path /rewritten%[path]
 server app 127.0.0.1:{server.server_port} check
backend fallback
 http-request return status 404
'''
        doc=Document.model_validate(import_config(original,'a'*64,maps)['document'])
        for route in doc.imported_routes:
            if route.domain in ('app.example.com','www.example.com'):
                route.access_policy=AccessPolicy.model_validate(policy);route.basic_auth_group=1
    clients={source:httpx.Client(transport=httpx.HTTPTransport(local_address=source),trust_env=False,timeout=2) for source in ('127.0.0.1','127.0.0.2','::1')}
    process=None;container=None
    def request(source='127.0.0.1',domain='app.example.com',path='/admin/login',auth=True,headers=None):
        base=f'http://[::1]:{v6}' if source=='::1' else f'http://127.0.0.1:{listen}'
        return clients[source].get(base+path,headers={'Host':domain,**(headers or {})},auth=('member','test-password') if auth else None)
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
        stop();config=generate(value,{'runtime_socket_config':'/tmp/access-runtime.sock','cert_dir_config':'/etc/certs'},groups)
        primary=tmp_path/'haproxy.cfg';primary.write_text(config)
        if kind=='native':
            check=subprocess.run([BINARY,'-c','-f',str(primary)],capture_output=True,text=True);assert check.returncode==0,check.stderr
            process=subprocess.Popen([BINARY,'-W','-db','-f',str(primary)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        else:
            check=subprocess.run(['docker','run','--rm','--user','99:99','-v',str(tmp_path)+':/etc/haproxy','haproxy:3.2.25','haproxy','-c','-f','/etc/haproxy/haproxy.cfg'],capture_output=True,text=True);assert check.returncode==0,check.stderr
            container='hc-access-'+secrets.token_hex(5)
            subprocess.run(['docker','run','-d','--name',container,'--user','99:99','--network','host','-v',str(tmp_path)+':/etc/haproxy','haproxy:3.2.25','haproxy','-W','-db','-f','/etc/haproxy/haproxy.cfg'],check=True,capture_output=True)
        for _ in range(100):
            try:
                if request().status_code==200:return config
            except httpx.TransportError:pass
            time.sleep(.1)
        raise AssertionError('HAProxy did not start')
    try:
        config=launch(doc)
        assert request().json()['path']=='/rewritten/admin/login'
        assert request('127.0.0.2').status_code==403 and request('127.0.0.2',auth=False).status_code==403
        assert request('127.0.0.2',headers={'X-Forwarded-For':'127.0.0.1'}).status_code==403
        first_public=request('127.0.0.2',path='/public',headers={'X-Forwarded-For':'127.0.0.1'});assert first_public.status_code==200
        second_public=request('127.0.0.2',path='/public',headers={'X-Forwarded-For':'127.0.0.1'});assert second_public.status_code==200
        if scenario!='host':
            first_headers={k.lower():v for k,v in first_public.json()['headers'].items()}
            second_headers={k.lower():v for k,v in second_public.json()['headers'].items()}
            assert first_headers['x-connection-port']==second_headers['x-connection-port']
        assert request('127.0.0.2',headers={'X-Forwarded-For':'127.0.0.1'}).status_code==403
        assert request('127.0.0.1',auth=False).status_code==401
        assert request('127.0.0.2',domain='www.example.com').status_code==403
        assert request('127.0.0.1',domain='www.example.com').status_code==200
        assert request('127.0.0.2',path='/%61dmin/login').status_code==403
        assert request('127.0.0.2',path='/public').status_code==200
        assert request('127.0.0.2',domain='other.example.com').status_code==200
        assert request('::1',domain='v6.example.com' if scenario=='host' else 'app.example.com').status_code==200
        again=Document.model_validate(import_config(config,'b'*64,maps)['document']);launch(again)
        assert request('127.0.0.2').status_code==403
        entries=again.hosts if scenario=='host' else again.imported_routes
        for entry in entries:
            if entry.domain in ('app.example.com','www.example.com'):entry.access_policy.paths=[]
        if scenario=='host':again.acme_enabled=True;again.acme_address='127.0.0.1';again.acme_port=server.server_port
        launch(again);assert request('127.0.0.2',path='/public').status_code==403
        if scenario=='host':assert request('127.0.0.2',path='/.well-known/acme-challenge/test',auth=False).status_code==200
        for entry in entries:
            if entry.domain in ('app.example.com','www.example.com'):entry.access_policy=None
        launch(again);assert request('127.0.0.2').status_code==200 and request('127.0.0.2',auth=False).status_code==401
    finally:
        stop();server.shutdown();server.server_close()
        for client in clients.values():client.close()
