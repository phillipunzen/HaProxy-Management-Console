"""Opt-in native/Docker lab: real runtime checks follow target shutdown/recovery."""
import http.server
import os
import secrets
import socket
import subprocess
import threading
import time

import pytest
from agent import main as agent
from backend.generator import generate
from backend.proxy_health import build
from backend.schemas import Document

pytestmark=pytest.mark.skipif(os.environ.get('HAPROXY_BASIC_AUTH_LAB')!='1',reason='Requires isolated HAProxy lab')
BINARY=os.environ.get('HAPROXY_BASIC_AUTH_BINARY','/usr/sbin/haproxy')


def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


class HTTP(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'OK')
    def log_message(self,*args):pass


@pytest.mark.parametrize('kind',['native','docker'])
def test_real_runtime_health_up_partial_down_recovery(tmp_path,kind):
    # Container uid 99 needs to traverse the temporary mount and create the socket.
    for parent in [tmp_path,*tmp_path.parents]:
        if str(parent).startswith('/tmp/pytest-of-root'):parent.chmod(0o755)
    tmp_path.chmod(0o777)
    services=[]
    def start(address=None):
        server=http.server.ThreadingHTTPServer(address or ('127.0.0.1',0),HTTP)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();services.append(server)
        return server
    first=start();second=start();first_address=first.server_address
    doc=Document(http_port=port(),hosts=[{'id':'web','domain':'app.example.com','aliases':['www.example.com'],
                                        'servers':[{'address':'127.0.0.1','port':first.server_port},
                                                   {'address':'127.0.0.1','port':second.server_port}]}])
    runtime_socket=tmp_path/'admin.sock'
    config=generate(doc,{'runtime_socket_config':str(runtime_socket) if kind=='native' else '/etc/haproxy/admin.sock','cert_dir_config':'/etc/certs'})
    config=config.replace(' check\n',' check inter 100ms rise 1 fall 1\n')
    primary=tmp_path/'haproxy.cfg';primary.write_text(config)
    container='hc-health-'+secrets.token_hex(5);process=None
    def wait(state):
        for _ in range(100):
            data=agent.stats('test',{'runtime_socket':str(runtime_socket)})
            result=build(doc,config,[],data)['hosts']['web']
            if result['state']==state:return result
            time.sleep(.1)
        raise AssertionError(f'Expected {state}, got {result}')
    try:
        if kind=='native':process=subprocess.Popen([BINARY,'-W','-db','-f',str(primary)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        else:subprocess.run(['docker','run','-d','--name',container,'--user','99:99','--network','host','-v',str(tmp_path)+':/etc/haproxy','haproxy:3.2.25','haproxy','-W','-db','-f','/etc/haproxy/haproxy.cfg'],check=True,capture_output=True)
        assert wait('up')['available']==2
        first.shutdown();first.server_close();services.remove(first)
        partial=wait('partial');assert partial['available']==1 and partial['total']==2
        second.shutdown();second.server_close();services.remove(second)
        down=wait('down');assert down['available']==0 and all(s['check_status']=='L4CON' for s in down['targets'])
        start(first_address);assert wait('partial')['available']==1
        changed=doc.model_copy(deep=True);changed.hosts[0].servers[0].port=port()
        assert build(changed,config,[],agent.stats('test',{'runtime_socket':str(runtime_socket)}))['hosts']['web']['state']=='pending'
    finally:
        if process:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        if kind=='docker':subprocess.run(['docker','rm','-f',container],capture_output=True)
        for server in services:server.shutdown();server.server_close()
