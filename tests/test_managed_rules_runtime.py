"""Opt-in native/Docker HTTP/TLS lab for domain redirects and import roundtrips."""
import os
import secrets
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer

import httpx
import pytest
from backend.generator import generate
from backend.haproxy_config import import_config
from backend.schemas import Document,Rule
from tests.test_proxy_options_runtime import HTTP,port,BINARY

pytestmark=pytest.mark.skipif(os.environ.get('HAPROXY_BASIC_AUTH_LAB')!='1',reason='Requires isolated HAProxy lab')


@pytest.mark.parametrize('kind',['native','docker'])
@pytest.mark.parametrize('source',['new','imported'])
def test_http_https_redirects_keep_uri_codes_security_and_roundtrip(tmp_path,kind,source):
    for parent in [tmp_path,*tmp_path.parents]:
        if str(parent).startswith('/tmp/pytest-of-root'):parent.chmod(0o755)
    tmp_path.chmod(0o755)
    certs=tmp_path/'certs';certs.mkdir()
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-subj','/CN=lab.example.com','-days','1',
                    '-keyout',str(tmp_path/'lab.key'),'-out',str(tmp_path/'lab.crt')],check=True,capture_output=True)
    (certs/'lab.pem').write_bytes((tmp_path/'lab.crt').read_bytes()+(tmp_path/'lab.key').read_bytes())
    server=ThreadingHTTPServer(('127.0.0.1',0),HTTP);threading.Thread(target=server.serve_forever,daemon=True).start()
    listen,secure,other=port(),port(),port()
    cert_dir='/etc/haproxy/certs' if kind=='docker' else str(certs)
    cap={'runtime_socket_config':'/tmp/rules-'+secrets.token_hex(6)+'.sock','cert_dir_config':cert_dir}
    frontend='public_http' if source=='new' else 'incoming'
    if source=='new':
        doc=Document(http_port=listen,https_port=secure,tls_enabled=True,acme_enabled=True,acme_port=server.server_port,
                     hosts=[{'id':'other','domain':'other.example.com','servers':[{'address':'127.0.0.1','port':server.server_port}]},
                            {'id':'locked','domain':'locked.example.com','servers':[{'address':'127.0.0.1','port':server.server_port}],
                             'access_policy':{'enabled':True,'networks':['192.0.2.0/24'],'paths':[]}}])
    else:
        config=f'''defaults
 mode http
 timeout connect 1s
 timeout client 5s
 timeout server 5s
frontend incoming
 bind :{listen}
 bind :{secure} ssl crt {cert_dir}/lab.pem
 http-request deny if {{ path_beg /private }}
 http-request redirect scheme https code 301 if !{{ ssl_fc }} !{{ path_beg /.well-known/acme-challenge/ }}
 use_backend origin if {{ path_beg /.well-known/acme-challenge/ }}
 default_backend origin
frontend other
 bind :{other}
 default_backend origin
backend origin
 server s 127.0.0.1:{server.server_port} check
'''
        doc=Document.model_validate(import_config(config,'a'*64)['document'])
    doc.rules=[Rule(id='private',name='Private',frontend=frontend,match='path_prefix',value='/private',action='deny')]
    for code in [301,302,307,308]:
        doc.rules.append(Rule(id='r'+str(code),name=str(code),frontend=frontend,match='host',value=f'a{code}.example.com',
                              action='redirect',target='https://destination.example.com',code=code,preserve_path=True))
    doc.rules.extend([
        Rule(id='fixed',name='Fixed',frontend=frontend,match='host',value='fixed.example.com',action='redirect',target='https://destination.example.com/a%20b?q=%2F',code=302),
        Rule(id='wild',name='Wildcard',frontend=frontend,match='host',value='*.wild.example.com',action='redirect',target='https://b.wild.example.com',preserve_path=True),
        Rule(id='locked',name='Locked',frontend=frontend,match='host',value='locked.example.com',action='redirect',target='https://destination.example.com',preserve_path=True)])
    process=None;container=None
    def request(host,path='/article%20one?id=5&next=%2F',tls=False,method='GET',endpoint=None):
        return httpx.request(method,f'{"https" if tls else "http"}://127.0.0.1:{endpoint or (secure if tls else listen)}'+path,
                             headers={'Host':host},verify=False,timeout=2,trust_env=False,follow_redirects=False)
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
        stop();config=generate(value,cap);(tmp_path/'haproxy.cfg').write_text(config)
        if kind=='native':
            check=subprocess.run([BINARY,'-c','-f',str(tmp_path/'haproxy.cfg')],capture_output=True,text=True)
            assert check.returncode==0,check.stderr
            process=subprocess.Popen([BINARY,'-W','-db','-f',str(tmp_path/'haproxy.cfg')],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        else:
            check=subprocess.run(['docker','run','--rm','--user','99:99','-v',str(tmp_path)+':/etc/haproxy','haproxy:3.2.25','haproxy','-c','-f','/etc/haproxy/haproxy.cfg'],capture_output=True,text=True)
            assert check.returncode==0,check.stderr
            container='hc-rules-'+secrets.token_hex(5)
            subprocess.run(['docker','run','-d','--name',container,'--user','99:99','--network','host','-v',str(tmp_path)+':/etc/haproxy',
                            'haproxy:3.2.25','haproxy','-W','-db','-f','/etc/haproxy/haproxy.cfg'],check=True,capture_output=True)
        for _ in range(80):
            try:
                if request('other.example.com',tls=True).status_code==200:return config
            except httpx.TransportError:pass
            time.sleep(.1)
        raise AssertionError('HAProxy did not start')
    try:
        config=launch(doc)
        for tls in [False,True]:
            for code in [301,302,307,308]:
                for method in ['GET','POST']:
                    response=request(f'A{code}.EXAMPLE.COM:{secure if tls else listen}',tls=tls,method=method)
                    assert response.status_code==code and response.headers['location']=='https://destination.example.com/article%20one?id=5&next=%2F'
                assert request(f'a{code}.example.com','/private',tls=tls).status_code==403
                assert request(f'a{code}.example.com','/.well-known/acme-challenge/test',tls=tls).status_code==200
            fixed=request('fixed.example.com',tls=tls)
            assert fixed.status_code==302 and fixed.headers['location']=='https://destination.example.com/a%20b?q=%2F'
            wild=request('a.wild.example.com',tls=tls)
            assert wild.status_code==301 and wild.headers['location']=='https://b.wild.example.com/article%20one?id=5&next=%2F'
        assert request('other.example.com',tls=True).json()['path']=='/article%20one?id=5&next=%2F'
        assert request('b.wild.example.com',tls=True).status_code==(404 if source=='new' else 200)
        if source=='new':assert request('locked.example.com',tls=True).status_code==403
        else:assert request('a301.example.com',endpoint=other).status_code==200
        again=Document.model_validate(import_config(config+'# outside change\n','b'*64)['document'])
        assert again.rules==doc.rules
        launch(again);assert request('a308.example.com',tls=True).status_code==308
        again.rules=[];launch(again)
        response=request('a301.example.com',tls=True);assert response.status_code==(404 if source=='new' else 200)
        assert request('other.example.com',tls=True).status_code==200
    finally:stop();server.shutdown();server.server_close()
