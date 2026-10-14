"""Opt-in Docker integration: HAPROXY_DOCKER_ONBOARDING_LAB=1 pytest this file."""
import json, os, socket, subprocess, tempfile, threading, time
from pathlib import Path
import pytest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import httpx
from agent import main as agent
from agent.setup_checks import docker_directories
from backend.haproxy_config import import_config,migration_context
from backend.generator import generate
from backend.schemas import Document

def free():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):self.send_response(200);self.end_headers();self.wfile.write(b'onboarding-ok')
    def log_message(self,*a):pass

@pytest.mark.skipif(os.environ.get("HAPROXY_DOCKER_ONBOARDING_LAB") != "1",reason="isolated Docker integration opt-in")
def test_user_compose_import_migration_reload_and_restart():
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    port=free();name='hc-compose-onboarding-'+str(os.getpid())
    root=Path(tempfile.mkdtemp(prefix='hc-compose-onboarding-',dir='/tmp'));root.chmod(0o755)
    for dirname in ('certs','vhosts','config','run','state'):(root/dirname).mkdir(mode=0o755)
    config=f'global\n maxconn 100\ndefaults\n mode http\n timeout connect 2s\n timeout client 5s\n timeout server 5s\n errorfile 400 /usr/local/etc/haproxy/errors/400.http\n errorfile 403 /usr/local/etc/haproxy/errors/403.http\n errorfile 408 /usr/local/etc/haproxy/errors/408.http\n errorfile 500 /usr/local/etc/haproxy/errors/500.http\n errorfile 502 /usr/local/etc/haproxy/errors/502.http\n errorfile 503 /usr/local/etc/haproxy/errors/503.http\n errorfile 504 /usr/local/etc/haproxy/errors/504.http\nfrontend web\n bind 127.0.0.1:{port}\n use_backend %[req.hdr(host),lower,map(/usr/local/etc/haproxy/dnsmapping.cfg,app)]\n'
    (root/'haproxy.cfg').write_text(config);(root/'dnsmapping.cfg').write_text('app.example.de app\nwww.example.de app\n')
    (root/'vhosts/01-app.cfg').write_text('backend app\n server origin 127.0.0.1:'+str(server.server_port)+' check\n')
    p=dict(kind='docker',container=name,token='synthetic-test-token-'+'x'*45,config_path=str(root/'haproxy.cfg'),container_config_dir='/usr/local/etc/haproxy',cert_dir=str(root/'certs'),cert_dir_config='/usr/local/etc/haproxy/certs',runtime_socket=str(root/'run/admin.sock'),runtime_socket_config='/run/haproxy/admin.sock',reload_signal='USR2',cert_uid=99,cert_gid=99,cert_mode=0o640)
    agent.STATE_DIR=root/'state'
    def start(prepared=False):
        mounts=[(root/('config' if prepared else 'haproxy.cfg'),'/usr/local/etc/haproxy'+('' if prepared else '/haproxy.cfg'),'ro'),(root/'certs','/usr/local/etc/haproxy/certs','ro'),(root/'vhosts','/usr/local/etc/haproxy/vhosts','ro')]
        mounts += [(root/'run','/run/haproxy','rw')] if prepared else [(root/'dnsmapping.cfg','/usr/local/etc/haproxy/dnsmapping.cfg','ro')]
        args=['docker','run','-d','--name',name,'--network','host']
        for source,target,mode in mounts:args+=['-v',str(source)+':'+target+':'+mode]
        args+=['haproxy:3.2-alpine','haproxy','-f','/usr/local/etc/haproxy/haproxy.cfg','-f','/usr/local/etc/haproxy/vhosts']
        result=subprocess.run(args,capture_output=True);assert result.returncode==0,result.stderr.decode()
        for i in range(60):
            try:
                r=httpx.get(f'http://127.0.0.1:{port}',headers={'Host':'app.example.de'},trust_env=False,timeout=1)
                if r.status_code==200:return
            except httpx.HTTPError:pass
            time.sleep(.15)
        raise AssertionError('Docker HAProxy did not serve initial config: '+subprocess.check_output(['docker','logs',name],stderr=subprocess.STDOUT).decode())
    try:
        start()
        try:docker_directories(p,agent.run,agent.sha)
        except SystemExit as e:assert 'Docker-Verzeichnis-Mount' in str(e)
        else:raise AssertionError('Original file mounts accepted')
        assert (root/'haproxy.cfg').read_text()==config
        print('PASS: original user mount layout rejected without changes',flush=True)
        subprocess.run(['docker','rm','-f',name],check=True,capture_output=True)
        (root/'config/haproxy.cfg').write_text(config);(root/'config/dnsmapping.cfg').write_bytes((root/'dnsmapping.cfg').read_bytes())
        p['config_path']=str(root/'config/haproxy.cfg')
        (root/'config/certs').mkdir();(root/'config/vhosts').mkdir()
        # Runtime-directory ownership follows the actual image identity.
        identity=subprocess.check_output(['docker','run','--rm','--entrypoint','id','haproxy:3.2-alpine','-u']).decode().strip()
        uid=int(identity);gid=int(subprocess.check_output(['docker','run','--rm','--entrypoint','id','haproxy:3.2-alpine','-g']).decode().strip())
        os.chown(root/'run',uid,gid);(root/'run').chmod(0o750);p.update(cert_uid=uid,cert_gid=gid)
        # Preserve the image's built-in error pages before the directory mount hides them.
        seed=subprocess.check_output(['docker','create','haproxy:3.2-alpine']).decode().strip()
        try:
            subprocess.run(['docker','cp',seed+':/usr/local/etc/haproxy/errors',str(root/'config/errors')],check=True,capture_output=True)
        finally:
            subprocess.run(['docker','rm',seed],check=True,capture_output=True)
        start(True)
        dirs=docker_directories(p,agent.run,agent.sha);assert dirs=={str(root/'config'),str(root/'vhosts')}
        # Same socket insertion/validation/reload as installer, with local fixture paths.
        candidate=config.replace('global\n','global\n stats socket /run/haproxy/admin.sock mode 660 level admin\n')
        agent.validate(p,candidate);agent.atomic(Path(p['config_path']),candidate.encode(),0o644)
        agent.run(['docker','kill','--signal','USR2',name])
        for i in range(60):
            try:
                before=agent.info(p)
                if before.get('Pid'):break
            except OSError:pass
            time.sleep(.15)
        else:raise AssertionError('Socket not available after insertion')
        bundle=agent.read_bundle(p,agent.run,agent.sha)
        assert len(bundle['sources'])==2 and len(bundle['maps'])==1 and bundle['complete']
        draft=import_config(bundle['config'],bundle['hash'],bundle['maps'],[{'path':s['path'],'hash':s['hash']} for s in bundle['sources']],[{'path':m['host_path'],'hash':m['hash']} for m in bundle['maps']])
        doc=Document.model_validate(draft['document']);assert len(doc.imported_routes)==2
        # Make an actual pool change before the atomic migration.
        next(b for b in doc.imported_backends if b.name=='app').proxy_options=['http-response set-header X-Control-Managed yes']
        generated=generate(doc,{'runtime_socket_config':p['runtime_socket_config'],'cert_dir_config':p['cert_dir_config']})
        body=agent.ConfigIn(config=generated,expected_hash=bundle['hash'])
        result=agent.apply_migration(p,body,migration_context(generated));assert result['applied'] and result['pid']!=before['Pid']
        assert (root/'vhosts/01-app.cfg').read_text().startswith('# Consolidated')
        for domain in ('app.example.de','www.example.de'):
            r=httpx.get(f'http://127.0.0.1:{port}',headers={'Host':domain},trust_env=False,timeout=5);assert r.status_code==200 and r.content==b'onboarding-ok' and r.headers.get('X-Control-Managed')=='yes'
        subprocess.run(['docker','restart',name],check=True,capture_output=True)
        for i in range(60):
            try:
                r=httpx.get(f'http://127.0.0.1:{port}',headers={'Host':'app.example.de'},trust_env=False,timeout=1)
                if r.status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.15)
        else:raise AssertionError('Service did not survive Docker restart')
        assert list((root/'state/backups').rglob('bundle.json'))
        print('PASS: official 3.2-alpine, directory mounts read-only, both -f sources and map import, atomic migration with backup, new worker reload and container restart',flush=True)
    finally:
        subprocess.run(['docker','rm','-f',name],capture_output=True);server.shutdown()
        import shutil
        shutil.rmtree(root)


@pytest.mark.skipif(os.environ.get("HAPROXY_DOCKER_ONBOARDING_LAB") != "1",reason="isolated Docker integration opt-in")
def test_generated_compose_preparation_preserves_errorfiles_before_recreation():
    """Execute actual wizard shell snippets against the official image and new mounts."""
    import shutil
    compiled=Path(__file__).resolve().parent.parent/'frontend/.test-build/agentSetupGuidance.js'
    assert compiled.exists(), 'Run npm test in frontend before this opt-in Docker test'
    root=Path(tempfile.mkdtemp(prefix='hc-compose-errors-',dir='/tmp'));root.chmod(0o755)
    name='hc-compose-errors-'+str(os.getpid());port=free()
    def cmd(*args):
        return subprocess.run(args,cwd=root,capture_output=True,text=True,timeout=60)
    def shell(script):
        return subprocess.run(['bash'],cwd='/tmp',input='sudo() { "$@"; }\n'+script,capture_output=True,text=True,timeout=60)
    def guidance(function,*args):
        js=f"import {{ {function},composeDirectoryPaths }} from {json.dumps(str(compiled))};process.stdout.write({function}("+','.join(json.dumps(a) for a in args)+'));'
        return subprocess.check_output(['node','--input-type=module','-e',js],text=True)
    def compose(prepared):
        mounts=['./config:/usr/local/etc/haproxy:ro'] if prepared else ['./haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro']
        mounts+=['./certs:/usr/local/etc/haproxy/certs:ro','./vhosts:/usr/local/etc/haproxy/vhosts:ro']
        if prepared:mounts+=['./run:/run/haproxy:rw']
        (root/'docker-compose.yml').write_text(json.dumps({'services':{'haproxy':{'image':'haproxy:3.2-alpine','container_name':name,'command':['haproxy','-f','/usr/local/etc/haproxy/haproxy.cfg','-f','/usr/local/etc/haproxy/vhosts'],'ports':[f'127.0.0.1:{port}:8080'],'volumes':mounts}}}))
    def request(path='/'):
        return httpx.get(f'http://127.0.0.1:{port}{path}',trust_env=False,timeout=1)
    try:
        for dirname in ('certs','vhosts','config','config/certs','config/vhosts','run','errors','config/errors'):(root/dirname).mkdir(mode=0o755)
        config='global\n maxconn 100\ndefaults\n mode http\n timeout connect 2s\n timeout client 5s\n timeout server 5s\n'+''.join(f' errorfile {code} /usr/local/etc/haproxy/errors/{code}.http\n' for code in (400,403,408,500,502,503,504))+'frontend web\n bind :8080\n http-request deny if { path /blocked }\n http-request return status 200 content-type text/plain string ok\n'
        (root/'haproxy.cfg').write_text(config);(root/'config/haproxy.cfg').write_text(config)
        compose(False)
        started=cmd('docker','compose','up','-d','haproxy');assert started.returncode==0,started.stderr
        for _ in range(60):
            try:
                if request().status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.15)
        else:raise AssertionError('Original file-mounted proxy never started')
        initial_id=cmd('docker','inspect','--format','{{.Id}}',name).stdout.strip()
        compose(True)
        paths=dict(config_path=str(root/'config/haproxy.cfg'),container_config_dir='/usr/local/etc/haproxy',runtime_socket=str(root/'run/admin.sock'),runtime_socket_config='/run/haproxy/admin.sock',cert_dir=str(root/'certs'),cert_dir_config='/usr/local/etc/haproxy/certs')
        recreate=guidance('recreateComposeCommands',paths,name)
        failed=shell(recreate)
        assert failed.returncode!=0 and 'error opening file' in failed.stderr,failed.stdout+failed.stderr
        assert cmd('docker','inspect','--format','{{.Id}}',name).stdout.strip()==initial_id
        assert request().status_code==200, 'Failed new-mount validation must leave the old proxy serving'
        # Existing target wins; custom local error pages win over image defaults.
        custom403=b'HTTP/1.0 403 Forbidden\r\nContent-Length: 12\r\nContent-Type: text/plain\r\n\r\ncustom-error'
        (root/'errors/403.http').write_bytes(custom403)
        (root/'config/errors/500.http').write_bytes(b'HTTP/1.0 500 Internal Server Error\r\nContent-Length: 6\r\n\r\ncustom')
        original500=(root/'config/errors/500.http').read_bytes()
        before_containers=set(cmd('docker','ps','-aq').stdout.split())
        prepared=shell(guidance('prepareComposeDirectory',str(root),name));assert prepared.returncode==0,prepared.stdout+prepared.stderr
        assert set(cmd('docker','ps','-aq').stdout.split())==before_containers, 'Seed container must be cleaned up'
        assert (root/'config/errors/403.http').read_bytes()==custom403
        assert (root/'config/errors/500.http').read_bytes()==original500
        assert all((root/f'config/errors/{code}.http').is_file() for code in (400,403,408,500,502,503,504))
        result=shell(recreate);assert result.returncode==0,result.stdout+result.stderr
        assert cmd('docker','inspect','--format','{{.Id}}',name).stdout.strip()!=initial_id
        for _ in range(60):
            try:
                if request().status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.15)
        else:raise AssertionError('Prepared directory-mounted proxy never started')
        assert request('/blocked').content==b'custom-error'
        restarted=cmd('docker','compose','restart','haproxy');assert restarted.returncode==0,restarted.stderr
        for _ in range(60):
            try:
                if request().status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.15)
        else:raise AssertionError('Prepared proxy failed after restart')
    finally:
        cmd('docker','compose','down','--remove-orphans');cmd('docker','rm','-f',name);shutil.rmtree(root)
