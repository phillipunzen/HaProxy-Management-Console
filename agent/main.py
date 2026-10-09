"""Run on each HAProxy host. All executable names and filesystem paths are local allowlists."""
import asyncio
import csv
import fcntl
import hashlib
import hmac
import io
import json
import logging
import os
import socket
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from backend.schemas import CertificateIn,CertificateAdoptIn,CertificateRenewIn,RenewalSettingsIn,CertificatePolicyIn
from agent import certificates as certificate_jobs
from backend.haproxy_config import migration_context
from agent.config_bundle import read_bundle

CONFIG_PATH = Path(os.environ.get('AGENT_CONFIG', '/etc/haproxy-control/agent.json'))
PROFILES = {}
STATE_DIR = Path(os.environ.get('AGENT_STATE_DIR', '/var/lib/haproxy-control'))
logger = logging.getLogger('haproxy-control-agent')


def load_profiles():
    global PROFILES
    conf = json.loads(CONFIG_PATH.read_text())
    PROFILES = conf['profiles']
    for name,p in PROFILES.items():
        if len(p.get('token','')) < 32 or p['token'].startswith('REPLACE_'):
            raise RuntimeError(f'Profile {name}: token too short')
        if p['kind'] not in ('native','docker'):
            raise RuntimeError('Invalid profile kind')
        for key in ('config_path','runtime_socket','runtime_socket_config','cert_dir','cert_dir_config'):
            if not p[key].startswith('/') or any(c.isspace() for c in p[key]):
                raise RuntimeError(f'{name}: invalid {key}')
        if p['kind'] == 'docker' and not p.get('container_config_dir'):
            raise RuntimeError('container_config_dir required for Docker')
    if len({p['config_path'] for p in PROFILES.values()})!=len(PROFILES):
        raise RuntimeError('Each profile requires its own configuration file')
    if len({p['runtime_socket'] for p in PROFILES.values()})!=len(PROFILES):
        raise RuntimeError('Each profile requires its own runtime socket')
    STATE_DIR.mkdir(parents=True,exist_ok=True,mode=0o700)


def run(args, timeout=25):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise HTTPException(504,'Zeitüberschreitung beim Aufruf auf dem HAProxy-Server.')
    except OSError:
        raise HTTPException(502,'Benötigtes Programm ist auf dem Agent-Server nicht verfügbar.')
    if result.returncode:
        raise HTTPException(422,(result.stderr or result.stdout or 'Befehl fehlgeschlagen.')[-6000:])
    return result.stdout.strip() or result.stderr.strip()


def auth(profile: str, request: Request):
    p = PROFILES.get(profile)
    token = request.headers.get('authorization','').removeprefix('Bearer ')
    if not p or not hmac.compare_digest(token,p['token']):
        raise HTTPException(401,'Agent-Zugriff verweigert.')
    return p


def sha(content):
    return hashlib.sha256(content.encode()).hexdigest()


@contextmanager
def lock(p):
    digest = sha(p['config_path'])
    with (STATE_DIR / (digest+'.lock')).open('a') as handle:
        try:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise HTTPException(409,'Auf dieser Instanz läuft bereits eine Änderung. Bitte erneut versuchen.')
        try:
            yield
        finally:
            fcntl.flock(handle,fcntl.LOCK_UN)


def atomic(path: Path, content: bytes, mode=0o640, uid=None, gid=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp = tempfile.mkstemp(prefix='.control-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(content); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp,mode)
        if uid is not None and gid is not None:
            os.chown(tmp,uid,gid)
        os.replace(tmp,path)
        dir_fd = os.open(path.parent,os.O_DIRECTORY)
        try: os.fsync(dir_fd)
        finally: os.close(dir_fd)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def runtime(p, command):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(3)
        sock.connect(p['runtime_socket'])
        sock.sendall((command+'\n').encode())
        sock.shutdown(socket.SHUT_WR)
        chunks=[];size=0
        while True:
            part=sock.recv(65536)
            if not part: break
            chunks.append(part);size+=len(part)
            if size>8*1024*1024: raise OSError('Runtime response too large')
    return b''.join(chunks).decode(errors='replace').strip()


def info(p):
    return dict(line.split(': ',1) for line in runtime(p,'show info').splitlines() if ': ' in line)


def validate(p, config):
    current=Path(p['config_path'])
    fd,tmp=tempfile.mkstemp(prefix='.control-check-',suffix='.cfg',dir=current.parent)
    try:
        with os.fdopen(fd,'w') as f: f.write(config)
        # The HAProxy container runs unprivileged; a validation file contains only config.
        os.chmod(tmp,current.stat().st_mode & 0o777)
        os.chown(tmp,current.stat().st_uid,current.stat().st_gid)
        try:context=migration_context(config)
        except ValueError as error:raise HTTPException(422,str(error))
        sources=read_bundle(p,run,sha)['sources'] if not context else []
        if not sources:sources=[{'path':str(current),'container_path':p.get('container_config_dir','')+'/'+current.name}]
        flags=[]
        for source in sources:
            value=tmp if source['path']==str(current) else source['path']
            if p['kind']=='docker':
                value=p['container_config_dir'].rstrip('/')+'/'+Path(tmp).name if source['path']==str(current) else source['container_path']
            flags+=['-f',value]
        if p['kind']=='docker':
            container_path=p['container_config_dir'].rstrip('/')+'/'+Path(tmp).name
            running=run(['docker','inspect','--format','{{.State.Running}}',p['container']])=='true'
            if running:
                args=['docker','exec',p['container'],'haproxy','-c']+flags
            else:
                image=run(['docker','inspect','--format','{{.Image}}',p['container']])
                args=['docker','run','--rm','--network','none','--volumes-from',p['container']+':ro','--entrypoint','haproxy',image,'-c']+flags
        else:
            args=[p.get('haproxy_binary','/usr/sbin/haproxy'),'-c']+flags
        return run(args)
    finally:
        Path(tmp).unlink(missing_ok=True)


def reload_service(p):
    # Require a usable runtime socket before touching a running configuration.
    previous=info(p).get('Pid')
    if not previous: raise HTTPException(502,'Runtime-Socket liefert keine Prozess-ID; Reload abgebrochen.')
    if p['kind']=='docker':
        run(['docker','kill','--signal',p.get('reload_signal','USR2'),p['container']])
    else:
        run(['systemctl','reload',p.get('service','haproxy')])
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        try:
            current=info(p)
            if current.get('Pid') and current['Pid']!=previous:
                return current
        except OSError: pass
        time.sleep(.25)
    raise HTTPException(502,'Kein neuer HAProxy-Worker am Runtime-Socket nach dem Reload.')


class ConfigIn(BaseModel):
    config: str = Field(min_length=1,max_length=1024*1024)
    expected_hash: str = Field(pattern=r'^[a-f0-9]{64}$')

class PemIn(BaseModel):
    name: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    pem: str = Field(min_length=100,max_length=256*1024)


def certificate_meta(path, staging=False):
    cert=x509.load_pem_x509_certificate(path.read_bytes())
    try: domains=cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound: domains=[]
    return {'name':path.stem,'domains':domains,'issuer':cert.issuer.rfc4514_string(),
            'expires_at':cert.not_valid_after_utc.isoformat(),'days_remaining':(cert.not_valid_after_utc-datetime.now(timezone.utc)).days,
            'fingerprint':hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest(),
            'staging':staging,'path':str(path)}


def certificate_scope_error(p):
    directory=Path(p['cert_dir']).resolve()
    for other in PROFILES.values():
        if other['config_path']==p['config_path']:continue
        other_directory=Path(other['cert_dir']).resolve()
        if directory==other_directory or directory.is_relative_to(other_directory) or other_directory.is_relative_to(directory):
            return 'Zertifikatsverzeichnis wird von mehreren Agent-Profilen gemeinsam verwendet. Für gezielte Zuweisung getrennte Verzeichnisse pro Profil einrichten.'
    return None


def require_certificate_scope(p):
    error=certificate_scope_error(p)
    if error:raise HTTPException(409,error)


def install_pem(p,name,pem,staging=False):
    require_certificate_scope(p)
    try:
        cert=x509.load_pem_x509_certificate(pem)
        key=serialization.load_pem_private_key(pem,password=None)
        cert_pub=cert.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
        key_pub=key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
        if cert_pub!=key_pub: raise ValueError('Key mismatch')
        if cert.not_valid_after_utc<datetime.now(timezone.utc) or cert.not_valid_before_utc>datetime.now(timezone.utc): raise ValueError('Invalid validity period')
    except (ValueError,TypeError):
        raise HTTPException(422,'PEM benötigt ein gültiges Zertifikat und den passenden unverschlüsselten privaten Schlüssel.')
    directory=Path(p['cert_dir']) / ('.staging' if staging else '')
    target=directory/(name+'.pem')
    old=target.read_bytes() if target.exists() else None
    mode=p.get('cert_mode',0o640);uid=p.get('cert_uid',0);gid=p.get('cert_gid',0)
    atomic(target,pem,mode,uid,gid)
    try:
        if not staging:
            validate(p,Path(p['config_path']).read_text())
            reload_service(p)
    except Exception as exc:
        if old is None: target.unlink(missing_ok=True)
        else: atomic(target,old,mode,uid,gid)
        try: reload_service(p)
        except Exception: logger.exception('Certificate rollback reload failed')
        raise exc
    return certificate_meta(target,staging)


def cert_state(p):
    return STATE_DIR/(sha(p['config_path'])+'-certificates.json')


def certbot_args(p,body):
    cert_name='control-'+sha(p['config_path'])[:8]+'-'+body.name+('-staging' if body.staging else '')
    args=[p.get('certbot_binary','certbot'),'certonly','--non-interactive','--agree-tos','--keep-until-expiring','--renew-with-new-domains','--email',body.email,'--cert-name',cert_name]
    if body.staging: args+=['--staging']
    if body.challenge=='http':
        if not p.get('acme_webroot'): raise HTTPException(422,'HTTP-Webroot ist im Agent-Profil nicht eingerichtet.')
        args+=['--webroot','-w',p['acme_webroot']]
    else:
        provider=p.get('dns_providers',{}).get(body.provider)
        if not provider: raise HTTPException(422,'DNS-Anbieter ist im Agent-Profil nicht eingerichtet.')
        plugin='dns-'+body.provider
        args+=['--'+plugin,'--'+plugin+'-credentials',provider['credentials_file'],
               '--'+plugin+'-propagation-seconds',str(provider.get('propagation_seconds',60))]
    for domain in body.domains: args+=['-d',domain]
    return cert_name,args


def issue(p,body):
    return certificate_jobs.issue(p,body)


def renew(p):
    return certificate_jobs.renew(p)


async def renewal_loop():
    await asyncio.sleep(60)
    while True:
        for name,p in PROFILES.items():
            try:await asyncio.to_thread(certificate_jobs.scheduled_check,p)
            except HTTPException as error:
                if error.status_code!=409:logger.exception('Certificate renewal failed for %s',name)
            except Exception:logger.exception('Certificate renewal failed for %s',name)
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app):
    load_profiles()
    task=asyncio.create_task(renewal_loop())
    yield
    task.cancel()

app=FastAPI(title='HAProxy Control Agent',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)

@app.exception_handler(RequestValidationError)
async def validation_error(request,error):
    return JSONResponse({'detail':[{k:v for k,v in e.items() if k in ('loc','msg','type')} for e in error.errors()]},status_code=422)

@app.middleware('http')
async def limits(request,call_next):
    length=request.headers.get('content-length','0')
    if not length.isdigit() or int(length)>2*1024*1024:
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'Request too large'},status_code=413)
    if request.method in ('POST','PUT'):
        body=await request.body()
        if len(body)>2*1024*1024:
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail':'Request too large'},status_code=413)
    return await call_next(request)

@app.get('/health')
def health(): return {'status':'ok'}

@app.get('/profiles/{profile}')
def capabilities(profile: str,p=Depends(auth)):
    return {k:v for k,v in p.items() if k in ('kind','runtime_socket_config','cert_dir_config','container','service')} | {
        'dns_providers':['cloudflare','hetzner'], 'http_challenge':bool(p.get('acme_webroot')),
        'dns_credentials_ui':True,'dns_credentials':certificate_jobs.credentials_public(p),
        'lego_adoption':bool(p.get('lego')),'lego_host_credentials':bool(p.get('lego',{}).get('env_file')),
        'automatic_renewal':True,'certificate_management':True,'acme_engines':['certbot']+(['lego'] if certificate_jobs.lego_ready(p) else []),'config_bundle':True,'certificate_scope_error':certificate_scope_error(p)}

@app.get('/profiles/{profile}/config-bundle')
def config_bundle(profile: str,p=Depends(auth)):
    with lock(p):
        try:return read_bundle(p,run,sha)
        except OSError as error:
            raise HTTPException(422,f'Konfigurationsdateien oder Maps können nicht gelesen werden: {error.strerror or type(error).__name__} ({error.filename or "Dateipfad unbekannt"}). Dateipfade, Leserechte und Docker-Mounts auf dem HAProxy-Host prüfen.') from error
        except UnicodeDecodeError as error:
            raise HTTPException(422,'Konfigurationsdateien und Maps müssen als UTF-8 lesbar sein. Dateikodierung auf dem HAProxy-Host prüfen.') from error

@app.get('/profiles/{profile}/config')
def read_config(profile: str,p=Depends(auth)):
    config=Path(p['config_path']).read_bytes().decode()
    return {'config':config,'hash':sha(config)}

@app.post('/profiles/{profile}/validate')
def check(profile: str,body: ConfigIn,p=Depends(auth)):
    with lock(p): return {'valid':True,'output':validate(p,body.config)}

@app.post('/profiles/{profile}/apply')
def apply(profile: str,body: ConfigIn,p=Depends(auth)):
    try:context=migration_context(body.config)
    except ValueError as error:raise HTTPException(422,str(error))
    if context:return apply_migration(p,body,context)
    with lock(p):
        target=Path(p['config_path']);old=target.read_bytes().decode();st=target.stat()
        if sha(old)!=body.expected_hash: raise HTTPException(409,'Konfiguration wurde extern geändert. Neu laden und Änderungen abgleichen.')
        output=validate(p,body.config)
        # Read the runtime socket before overwriting the active configuration.
        try: info(p)
        except OSError: raise HTTPException(502,'Runtime-Socket ist nicht erreichbar; keine Änderung vorgenommen.')
        backup=STATE_DIR/'backups'/sha(p['config_path'])/(str(time.time_ns())+'.cfg')
        atomic(backup,old.encode(),0o600)
        atomic(target,body.config.encode(),st.st_mode & 0o777,st.st_uid,st.st_gid)
        try: current=reload_service(p)
        except Exception as error:
            atomic(target,old.encode(),st.st_mode & 0o777,st.st_uid,st.st_gid)
            try: reload_service(p)
            except Exception: raise HTTPException(502,'Reload und Wiederherstellung des Dienstes fehlgeschlagen. Alte Datei wiederhergestellt; Server prüfen.')
            raise HTTPException(502,'Reload fehlgeschlagen; vorherige Konfiguration und Dienst wiederhergestellt.') from error
        return {'applied':True,'hash':sha(body.config),'output':output,'pid':current['Pid']}

def apply_migration(p,body,context):
    with lock(p):
        bundle=read_bundle(p,run,sha)
        actual_files={item['path']:item['hash'] for item in bundle['sources']}
        expected_files={item['path']:item['hash'] for item in context['files']}
        if actual_files!=expected_files or bundle['hash']!=body.expected_hash:
            raise HTTPException(409,'Eine Konfigurationsdatei wurde geändert. Erneut importieren und vergleichen.')
        actual_maps={item['host_path']:item['hash'] for item in bundle['maps']}
        if actual_maps!={item['path']:item['hash'] for item in context['maps']}:
            raise HTTPException(409,'Eine Map-Datei wurde geändert. Erneut importieren und vergleichen.')
        output=validate(p,body.config)
        try:info(p)
        except OSError:raise HTTPException(502,'Runtime-Socket nicht erreichbar; keine Migration vorgenommen.')
        backup=STATE_DIR/'backups'/sha(p['config_path'])/str(time.time_ns())
        atomic(backup/'bundle.json',json.dumps(bundle).encode(),0o600)
        old=[]
        for source in bundle['sources']:
            path=Path(source['path']);attributes=path.stat()
            old.append((path,source['content'],attributes))
        def write(path,content,attributes):atomic(path,content.encode(),attributes.st_mode & 0o777,attributes.st_uid,attributes.st_gid)
        try:
            for path,content,attributes in old:
                value=body.config if str(path)==p['config_path'] else '# Consolidated into '+p['config_path']+' by HAProxy Control\n'
                write(path,value,attributes)
            current=reload_service(p)
        except Exception as error:
            restore_errors=[]
            for path,content,attributes in old:
                try:write(path,content,attributes)
                except OSError:restore_errors.append(str(path))
            if restore_errors:
                logger.error('Migration file restore failed: %s',restore_errors)
                raise HTTPException(502,'Wiederherstellung einzelner Dateien fehlgeschlagen; Agent-Sicherung und HAProxy-Dienst prüfen.') from error
            try:reload_service(p)
            except Exception:raise HTTPException(502,'Originaldateien wiederhergestellt; HAProxy-Dienst prüfen.') from error
            raise HTTPException(502,'Migration fehlgeschlagen; alle Originaldateien und Dienst wiederhergestellt.') from error
        after=read_bundle(p,run,sha)
        return {'applied':True,'hash':sha(body.config),'output':output,'pid':current['Pid'],
                'sources':[{'path':s['path'],'hash':s['hash']} for s in after['sources']],
                'map_hashes':[{'path':m['host_path'],'hash':m['hash']} for m in after['maps']]}

@app.post('/profiles/{profile}/service/{action}')
def service(profile: str,action: str,p=Depends(auth)):
    if action not in ('reload','start','stop','restart'): raise HTTPException(404)
    with lock(p):
        if action=='reload':
            validate(p,Path(p['config_path']).read_text());return {'ok':True,'info':reload_service(p)}
        if action in ('start','restart'): validate(p,Path(p['config_path']).read_text())
        if p['kind']=='docker':
            # Validation requires a running container; start can validate using the image outside it.
            result=run(['docker',action,p['container']])
        else: result=run(['systemctl',action,p.get('service','haproxy')])
        if action in ('start','restart'):
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                try:
                    if info(p).get('Pid'): return {'ok':True,'output':result}
                except OSError: pass
                time.sleep(.25)
            raise HTTPException(502,'Dienstaktion wurde ausgeführt, aber HAProxy meldet sich nicht am Runtime-Socket.')
        return {'ok':True,'output':result}

@app.get('/profiles/{profile}/stats')
def stats(profile: str,p=Depends(auth)):
    try:
        details=info(p)
        text=runtime(p,'show stat')
        if text.startswith('# '): text=text[2:]
        rows=list(csv.DictReader(io.StringIO(text)))
        fronts=[r for r in rows if r.get('svname')=='FRONTEND']
        def total(field): return sum(int(r.get(field) or 0) for r in fronts)
        return {'online':True,'info':details,'rows':rows,'sessions':total('scur'),'request_rate':total('req_rate'),
                'requests':total('req_tot'),'bytes_in':total('bin'),'bytes_out':total('bout'),
                'errors_5xx':total('hrsp_5xx'),'version':details.get('Version'),'uptime':details.get('Uptime')}
    except (OSError,ValueError) as error:
        return {'online':False,'error':'Runtime-Socket nicht erreichbar: '+str(error)}

@app.get('/profiles/{profile}/certificates')
def certificates(profile: str,p=Depends(auth)):
    result=[]
    for staging,directory in ((False,Path(p['cert_dir'])),(True,Path(p['cert_dir'])/'.staging')):
        for path in directory.glob('*.pem'):
            try: result.append(certificate_meta(path,staging)|certificate_jobs.status(p,path.stem,staging))
            except ValueError: result.append({'name':path.stem,'error':'Ungültiges PEM','staging':staging})
    return result

@app.post('/profiles/{profile}/certificates/issue')
def issue_endpoint(profile: str,body: CertificateIn,p=Depends(auth)):
    with lock(p): return issue(p,body)

@app.post('/profiles/{profile}/certificates/import')
def import_endpoint(profile: str,body: PemIn,p=Depends(auth)):
    with lock(p):
        result=install_pem(p,body.name,body.pem.encode())
        certificate_jobs.forget(p,body.name)
        return result

@app.post('/profiles/{profile}/certificates/renew')
def renew_endpoint(profile: str,body:CertificateRenewIn,p=Depends(auth)):
    with lock(p): return certificate_jobs.renew(p,body.name,body.force)

@app.get('/profiles/{profile}/certificates/renewal-settings')
def renewal_settings(profile: str,p=Depends(auth)):
    return certificate_jobs.schedule_public(p)

@app.put('/profiles/{profile}/certificates/renewal-settings')
def save_renewal_settings(profile: str,body:RenewalSettingsIn,p=Depends(auth)):
    with lock(p):
        require_certificate_scope(p)
        return certificate_jobs.save_schedule(p,body)

@app.put('/profiles/{profile}/certificates/{name}/policy')
def certificate_policy(profile: str,name:str,body:CertificatePolicyIn,p=Depends(auth)):
    with lock(p):return certificate_jobs.policy(p,name,body)

@app.post('/profiles/{profile}/certificates/adopt-lego')
def adopt_lego(profile: str,body:CertificateAdoptIn,p=Depends(auth)):
    with lock(p):return certificate_jobs.adopt_lego(p,body)
