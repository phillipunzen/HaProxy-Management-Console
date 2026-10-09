"""Opt-in local lab: independent HAProxy processes, random ports, no production DB."""
import hashlib
import http.server
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import socketserver
import subprocess
import threading
import time

import httpx
import pytest
from backend import basic_auth as auth
from backend.generator import generate
from backend.haproxy_config import import_config
from backend.schemas import Document,Host

pytestmark=pytest.mark.skipif(os.environ.get('HAPROXY_BASIC_AUTH_LAB')!='1',reason='Set HAPROXY_BASIC_AUTH_LAB=1 for isolated local native/Docker tests')
BINARY=os.environ.get('HAPROXY_BASIC_AUTH_BINARY','/usr/sbin/haproxy')
PASSWORD='runtime-test-password'

class HTTP(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        content=json.dumps({'authorization':self.headers.get('Authorization'),'path':self.path}).encode()
        self.send_response(200);self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
    def log_message(self,*args):pass

class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        while data:=self.request.recv(65536):self.request.sendall(data)

def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


@pytest.mark.parametrize('kind',['native','docker'])
@pytest.mark.parametrize('condition',[
    'if !{ http_auth(legacy) }',
    'unless { http_auth(legacy) }',
    'if !{ http_auth(legacy) } or { path /locked }',
])
def test_legacy_migration_preserves_other_domains_and_frontends(tmp_path,kind,condition):
    web=http.server.ThreadingHTTPServer(('127.0.0.1',0),HTTP)
    threading.Thread(target=web.serve_forever,daemon=True).start()
    tmp_path.chmod(0o755);hp,other=port(),port()
    original=f'''defaults
 mode http
 timeout connect 5s
 timeout client 30s
 timeout server 30s
frontend edge
 bind 127.0.0.1:{hp}
 acl private hdr(host) -i private.example.com
 use_backend shared if private
 acl public hdr(host) -i public.example.com
 use_backend shared if public
frontend other
 bind 127.0.0.1:{other}
 default_backend shared
backend shared
 http-request auth realm "Legacy team" {condition}
 http-request auth realm "Extra restriction" if {{ path /double }}
 server web 127.0.0.1:{web.server_address[1]} check
userlist legacy
 user old insecure-password old-password
'''
    doc=Document.model_validate(import_config(original,hashlib.sha256(original.encode()).hexdigest())['document'])
    selected=next(r for r in doc.imported_routes if r.domain=='private.example.com')
    selected.basic_auth_group=1;selected.basic_auth_replace_existing=True
    groups={1:{'id':1,'realm':'Central team','users':[{'username':'alice','hash':auth.hash_password(PASSWORD)}]}}
    current=generate(doc,{},groups)
    # Exercise the persisted/reimported form, as used after applying a draft.
    again=Document.model_validate(import_config(current,hashlib.sha256(current.encode()).hexdigest())['document'])
    config_file=tmp_path/'haproxy.cfg';config_file.write_text(generate(again,{},groups))
    process=None;container=None
    try:
        if kind=='native':
            result=subprocess.run([BINARY,'-c','-f',str(config_file)],capture_output=True,text=True)
            assert result.returncode==0,result.stderr
            process=subprocess.Popen([BINARY,'-W','-db','-f',str(config_file)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        else:
            container='haproxy-auth-migration-lab-'+secrets.token_hex(5)
            subprocess.run(['docker','run','-d','--name',container,'--user','0','--network','host','-v',str(tmp_path)+':/etc/haproxy','haproxy:3.2.25','haproxy','-W','-db','-f','/etc/haproxy/haproxy.cfg'],check=True,capture_output=True)
        with httpx.Client(trust_env=False,timeout=4) as client:
            def request(domain,path='/',credentials=None,listener=hp):
                return client.get(f'http://127.0.0.1:{listener}'+path,headers={'Host':domain},auth=credentials)
            for _ in range(100):
                try:
                    if request('private.example.com').status_code==401:break
                except httpx.RequestError:pass
                time.sleep(.1)
            else:raise AssertionError('HAProxy did not start')
            assert request('private.example.com',credentials=('old','old-password')).status_code==401
            assert request('private.example.com',credentials=('alice','wrong')).status_code==401
            for path in ('/','/locked','/double'):
                response=request('private.example.com',path,('alice',PASSWORD))
                assert response.status_code==200,response.text
                assert response.json()['authorization'] is None
            # The same keep-alive client must not carry the bypass flag to another request.
            assert request('public.example.com').status_code==401
            assert request('public.example.com',credentials=('alice',PASSWORD)).status_code==401
            assert request('public.example.com',credentials=('old','old-password')).status_code==200
            assert request('public.example.com','/double',('old','old-password')).status_code==401
            assert request('private.example.com',credentials=('alice',PASSWORD),listener=other).status_code==401
            assert request('private.example.com',credentials=('old','old-password'),listener=other).status_code==200
            if ' or ' in condition:assert request('public.example.com','/locked',('old','old-password')).status_code==401
            # Disabling central auth restores both original challenges, even after import.
            next(r for r in again.imported_routes if r.domain==selected.domain).basic_auth_group=None
            cleared=generate(again,{})
            assert 'txn.mgmt_' not in cleared
            assert f'http-request auth realm "Legacy team" {condition}' in cleared
    finally:
        if process:process.terminate();process.wait(timeout=10)
        if container:subprocess.run(['docker','rm','-f',container],capture_output=True)
        web.shutdown();web.server_close()

@pytest.mark.parametrize('kind',['native','docker'])
@pytest.mark.parametrize('source',['generated','imported'])
def test_real_site_authentication_and_preservation(tmp_path,kind,source):
    web=http.server.ThreadingHTTPServer(('127.0.0.1',0),HTTP);echo=socketserver.ThreadingTCPServer(('127.0.0.1',0),Echo)
    for server in (web,echo):threading.Thread(target=server.serve_forever,daemon=True).start()
    tmp_path.chmod(0o755);certs=tmp_path/'certs';certs.mkdir(mode=0o755)
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(tmp_path/'key.pem'),'-out',str(tmp_path/'cert.pem'),'-days','1','-subj','/CN=localhost'],check=True,capture_output=True)
    (certs/'test.pem').write_bytes((tmp_path/'cert.pem').read_bytes()+(tmp_path/'key.pem').read_bytes())
    hp,sp,tp,other=port(),port(),port(),port();prefix=str(tmp_path) if kind=='native' else '/etc/haproxy'
    caps={'runtime_socket_config':prefix+'/admin.sock','cert_dir_config':prefix+'/certs'}
    groups={1:{'id':1,'realm':'Internal','users':[{'username':'alice','hash':auth.hash_password(PASSWORD)}]},
            2:{'id':2,'realm':'Team B','users':[{'username':'bob','hash':auth.hash_password(PASSWORD)}]},
            3:{'id':3,'realm':'Empty','users':[]}}
    backend=[{'address':'127.0.0.1','port':web.server_address[1]}]
    if source=='generated':
        doc=Document(http_port=hp,https_port=sp,tls_enabled=True,acme_enabled=True,acme_address='127.0.0.1',acme_port=web.server_address[1],hosts=[
            Host(id='private',domain='private.example.com',servers=backend,basic_auth_group=1,force_https=True),
            Host(id='publicpath',domain='private.example.com',path='/public',servers=backend),
            Host(id='public',domain='public.example.com',servers=backend),
            Host(id='team',domain='team.example.com',servers=backend,basic_auth_group=2),
            Host(id='forward',domain='forward.example.com',servers=backend,basic_auth_group=1,basic_auth_forward=True),
            Host(id='empty',domain='empty.example.com',servers=backend,basic_auth_group=3)])
    else:
        config=f'''global
 stats socket {caps['runtime_socket_config']} level admin
defaults
 mode http
 timeout connect 5s
 timeout client 30s
 timeout server 30s
frontend edge
 bind 127.0.0.1:{hp}
 acl private hdr(host) -i private.example.com
 use_backend shared if private
 acl public hdr(host) -i public.example.com
 use_backend shared if public
 acl team hdr(host) -i team.example.com
 use_backend shared if team
 acl forward hdr(host) -i forward.example.com
 use_backend shared if forward
 acl empty hdr(host) -i empty.example.com
 use_backend shared if empty
frontend other
 bind 127.0.0.1:{other}
 default_backend shared
backend shared
 http-request return status 200 content-type application/json string '{{"authorization":null}}' if {{ path /local }}
 server web 127.0.0.1:{web.server_address[1]} check
frontend mysql
 mode tcp
 bind 127.0.0.1:{tp}
 default_backend database
backend database
 mode tcp
 server db 127.0.0.1:{echo.server_address[1]} check
userlist legacy
 user legacy insecure-password placeholder
'''
        doc=Document.model_validate(import_config(config,hashlib.sha256(config.encode()).hexdigest())['document'])
        for route in doc.imported_routes:
            route.basic_auth_group={'private.example.com':1,'team.example.com':2,'forward.example.com':1,'empty.example.com':3}.get(route.domain)
            route.basic_auth_forward=route.domain=='forward.example.com'
    current=generate(doc,caps,groups);config_file=tmp_path/'haproxy.cfg';config_file.write_text(current)
    process=None;container=None
    def validate():
        command=[BINARY,'-c','-f',str(config_file)] if kind=='native' else ['docker','exec',container,'haproxy','-c','-f','/etc/haproxy/haproxy.cfg']
        result=subprocess.run(command,capture_output=True,text=True);assert result.returncode==0,result.stderr
    try:
        if kind=='native':
            validate();process=subprocess.Popen([BINARY,'-W','-db','-f',str(config_file)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        else:
            container='haproxy-basic-auth-lab-'+secrets.token_hex(5)
            subprocess.run(['docker','run','-d','--name',container,'--user','0','--network','host','-v',str(tmp_path)+':/etc/haproxy','haproxy:3.2.25','haproxy','-W','-db','-f','/etc/haproxy/haproxy.cfg'],check=True,capture_output=True);validate()
        with httpx.Client(verify=False,trust_env=False,timeout=4) as client:
            for _ in range(100):
                try:
                    if client.get(f'http://127.0.0.1:{hp}/',headers={'Host':'public.example.com'}).status_code==200:break
                except httpx.RequestError:pass
                time.sleep(.1)
            else:raise AssertionError('HAProxy did not start')
            def request(domain,path='/',credentials=None,extra=None):
                return client.get(f'{"https" if source=="generated" else "http"}://127.0.0.1:{sp if source=="generated" else hp}'+path,headers={'Host':domain,**(extra or {})},auth=credentials)
            assert request('private.example.com').status_code==401
            assert 'Internal' in request('private.example.com').headers['www-authenticate']
            assert request('private.example.com',credentials=('alice','incorrect')).status_code==401
            assert request('private.example.com',credentials=('bob',PASSWORD)).status_code==401
            assert request('private.example.com',credentials=('alice',PASSWORD)).json()['authorization'] is None
            assert request('team.example.com',credentials=('alice',PASSWORD)).status_code==401
            assert request('team.example.com',credentials=('bob',PASSWORD)).status_code==200
            assert request('forward.example.com',credentials=('alice',PASSWORD)).json()['authorization'].startswith('Basic ')
            assert request('public.example.com',extra={'Authorization':'Bearer unrelated-app-token'}).json()['authorization']=='Bearer unrelated-app-token'
            assert request('empty.example.com',credentials=('alice',PASSWORD)).status_code==401
            if source=='generated':
                assert client.get(f'http://127.0.0.1:{hp}/',headers={'Host':'private.example.com'}).status_code==301
                assert request('private.example.com','/public').status_code==200
                assert client.get(f'http://127.0.0.1:{hp}/.well-known/acme-challenge/test',headers={'Host':'private.example.com'}).status_code==200
            else:
                assert request('private.example.com','/local').status_code==401
                assert request('private.example.com','/local',credentials=('alice',PASSWORD)).status_code==200
                assert client.get(f'http://127.0.0.1:{other}/',headers={'Host':'private.example.com'}).status_code==200
                with socket.create_connection(('127.0.0.1',tp)) as connection:connection.sendall(b'tcp-intact');assert connection.recv(32)==b'tcp-intact'
            # Reload an updated password into the real native master / Docker
            # master, then prove the old credentials no longer work.
            groups[1]['users'][0]['hash']=auth.hash_password('replacement-runtime-password')
            config_file.write_text(generate(doc,caps,groups));validate()
            if kind=='native':process.send_signal(signal.SIGUSR2)
            else:subprocess.run(['docker','kill','--signal','USR2',container],check=True,capture_output=True)
            for _ in range(100):
                if request('private.example.com',credentials=('alice','replacement-runtime-password')).status_code==200:break
                time.sleep(.1)
            else:raise AssertionError('Reload did not activate new password')
            assert request('private.example.com',credentials=('alice',PASSWORD)).status_code==401
    finally:
        if process:process.terminate();process.wait(timeout=10)
        if container:subprocess.run(['docker','rm','-f',container],capture_output=True)
        for server in (web,echo):server.shutdown();server.server_close()
