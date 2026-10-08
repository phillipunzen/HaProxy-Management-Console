import asyncio
import hashlib
import json
import hmac
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from cryptography.fernet import Fernet
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, delete, func
from sqlalchemy.exc import IntegrityError,SQLAlchemyError
from sqlalchemy.orm import Session

from backend.db import Base,engine,SessionLocal,get_db,now,User,LoginSession,Instance,Revision,MetricLatest,Audit,BasicAuthDirectory,BasicAuthDeployment
from backend import metrics as metric_store
from backend import topology as topology_store
from backend import basic_auth,basic_auth_api
from backend.settings import settings
from backend.schemas import LoginIn,PasswordIn,UserIn,InstanceIn,Document,DraftIn,CertificateIn,ImportedMap
from backend.generator import generate
from backend.agent_setup import AgentSetupIn, build_plan, build_update_command
from backend.haproxy_config import import_config, enrich_stats, migration_context, MIGRATION_PREFIX
from pydantic import BaseModel, Field

ph=PasswordHasher()
cipher=Fernet(settings.encryption_key.encode())
if len(settings.session_secret)<32:
    raise RuntimeError('Session secret must contain at least 32 characters')
logger=logging.getLogger('haproxy-control')
failures=defaultdict(deque)
rate_lock=threading.Lock()
dummy_hash=ph.hash(secrets.token_urlsafe(32))
topology_cache={}
topology_cache_lock=threading.Lock()


def audit(db,actor,action,target='',detail=''):
    db.add(Audit(actor=actor,action=action,target=str(target),detail=detail))


def token_hash(token): return hashlib.sha256(token.encode()).hexdigest()
def csrf(token): return hmac.new(settings.session_secret.encode(),token.encode(),hashlib.sha256).hexdigest()


def user_data(user):
    return {'id':user.id,'username':user.username,'role':user.role,'must_change_password':user.must_change_password}


def current_user(request: Request,db: Session=Depends(get_db)):
    token=request.cookies.get('haproxy_session','')
    session=db.get(LoginSession,token_hash(token)) if token else None
    if not session or session.expires_at<now(): raise HTTPException(401,'Bitte anmelden.')
    user=db.get(User,session.user_id)
    if not user: raise HTTPException(401,'Sitzung ungültig.')
    if request.method not in ('GET','HEAD','OPTIONS'):
        if not hmac.compare_digest(request.headers.get('x-csrf-token',''),csrf(token)):
            raise HTTPException(403,'Sicherheitsprüfung fehlgeschlagen. Seite neu laden.')
    if user.must_change_password and request.url.path not in ('/api/auth/me','/api/auth/password','/api/auth/logout'):
        raise HTTPException(403,'Bitte zuerst das Startpasswort ändern.')
    return user


def operator(user=Depends(current_user)):
    if user.role not in ('admin','operator'): raise HTTPException(403,'Bearbeitungsrechte erforderlich.')
    return user


def admin(user=Depends(current_user)):
    if user.role!='admin': raise HTTPException(403,'Administratorrechte erforderlich.')
    return user


def instance(db,id):
    value=db.get(Instance,id)
    if not value: raise HTTPException(404,'Instanz nicht gefunden.')
    return value


def public_instance(i):
    return {'id':i.id,'name':i.name,'agent_url':i.agent_url,'profile':i.profile,'kind':i.kind,'notes':i.notes,
            'allow_http':i.allow_http,'created_at':i.created_at.isoformat()+'Z'}


def agent(i,path='',method='GET',body=None,timeout=10):
    try:
        with httpx.Client(timeout=timeout,trust_env=False,follow_redirects=False,verify=settings.agent_ca_file or True) as client:
            result=client.request(method,i.agent_url+'/profiles/'+i.profile+path,
                                  headers={'Authorization':'Bearer '+cipher.decrypt(i.token_cipher.encode()).decode()},json=body)
        if result.status_code>=400:
            try: detail=result.json().get('detail','Agent-Fehler')
            except ValueError: detail='Agent antwortet mit einem Fehler.'
            raise HTTPException(result.status_code if result.status_code in (401,403,409,422,502,504) else 502,detail)
        if result.status_code>=300: raise HTTPException(502,'Agent-Weiterleitungen sind nicht erlaubt.')
        return result.json()
    except (httpx.RequestError,ValueError):
        raise HTTPException(502,'Agent nicht erreichbar. Adresse, TLS-Zertifikat und Firewall prüfen.')


collection_lock=threading.Lock()

def collect_metrics():
    if not collection_lock.acquire(blocking=False): return
    try:
        with SessionLocal() as db:
            ids=list(db.scalars(select(Instance.id)))
        for id in ids:
            with SessionLocal() as db:
                i=db.get(Instance,id)
                if not i: continue
            # Do not hold a MariaDB transaction during network I/O.
            try: data=agent(i,'/stats',timeout=8)
            except HTTPException as e: data={'online':False,'error':str(e.detail)}
            with SessionLocal() as db:
                metric_store.record(db,id,data,now(),settings);db.commit()
        metric_store.maintain(SessionLocal,settings)
    finally:
        collection_lock.release()


async def metric_loop():
    while True:
        try: await asyncio.to_thread(collect_metrics)
        except Exception: logger.exception('Metrics collection failed')
        await asyncio.sleep(max(settings.metrics_interval,10))


@asynccontextmanager
async def lifespan(app):
    for attempt in range(30):
        try:
            Base.metadata.create_all(engine)
            break
        except Exception:
            if attempt==29: raise
            await asyncio.sleep(2)
    with SessionLocal() as db:
        if not db.scalar(select(User.id).limit(1)):
            db.add(User(username=settings.admin_username,password_hash=ph.hash(settings.admin_password),role='admin'))
            audit(db,'system','admin.bootstrap',settings.admin_username)
            db.commit()
        if not db.get(BasicAuthDirectory,1):db.add(BasicAuthDirectory(id=1));db.commit()
    task=asyncio.create_task(metric_loop())
    yield
    task.cancel()

app=FastAPI(title='HAProxy Control',version='0.1.0',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
app.include_router(basic_auth_api.router(admin,operator,audit))

SETUP_ROOT = Path(__file__).resolve().parent.parent

@app.post('/api/agent-setup')
def agent_setup(body:AgentSetupIn,user=Depends(admin)):
    return build_plan(body, settings.app_origin, SETUP_ROOT)

@app.post('/api/instances/{id}/agent-update')
def agent_update(id:int,user=Depends(admin),db=Depends(get_db)):
    instance(db,id)
    return build_update_command(settings.app_origin,SETUP_ROOT)

@app.get('/api/agent-installer')
def agent_installer():
    # Public installation code; credentials exist only in the admin's setup plan.
    return FileResponse(SETUP_ROOT/'scripts/install-agent.sh', media_type='text/plain')

@app.get('/api/agent-package')
def agent_package():
    return FileResponse(SETUP_ROOT/'downloads/haproxy-management-docker.zip',
                        media_type='application/zip', filename='haproxy-management-docker.zip')

@app.get('/api/import-guide')
def import_guide(user=Depends(operator)):
    return FileResponse(SETUP_ROOT/'docs/IMPORT.md',media_type='text/plain',filename='HAPROXY-IMPORT.md')

class ImportRequest(BaseModel):
    config: str | None = Field(default=None,min_length=1,max_length=1024*1024)
    maps: list[ImportedMap] = Field(default_factory=list,max_length=100)
    active_hash: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$')
    document_version: int | None = None
    preview_hash: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$')

def import_preview_for(i,body):
    try:bundle=agent(i,'/config-bundle')
    except HTTPException as error:
        if body.config is None:
            raise HTTPException(422,'Datei-Import benötigt einen aktuellen Agenten. Unter Server den Agenten aktualisieren oder Konfiguration manuell laden.') from error
        active=agent(i,'/config')
        bundle={'config':active['config'],'hash':active['hash'],'sources':[],'maps':[],
                'warnings':['Agent ohne Datei-Bündel: weitere geladene Dateien können nicht erkannt werden. Vor einer Migration Agent aktualisieren.']}
    source=body.config if body.config is not None else bundle['config']
    if bundle.get('complete') is False:
        raise HTTPException(422,'Geladene Dateien konnten nicht vollständig ermittelt werden. config_sources im Agent-Profil mit allen -f-Dateien bzw. Verzeichnissen setzen, dann erneut einlesen.')
    maps=[m.model_dump() for m in body.maps] if body.config is not None else [{'path':m['path'],'content':m['content']} for m in bundle['maps']]
    try:preview=import_config(source,bundle['hash'],maps,
        [{'path':s['path'],'hash':s['hash']} for s in bundle['sources']],
        [{'path':m['host_path'],'hash':m['hash']} for m in bundle['maps']])
    except ValueError as error:raise HTTPException(422,str(error))
    preview['warnings']+=bundle['warnings'];preview['active_hash']=bundle['hash']
    preview['source_files']=[s['path'] for s in bundle['sources']]
    preview['preview_hash']=hashlib.sha256(json.dumps(preview['document'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return preview

@app.post('/api/instances/{id}/import-preview')
def preview_import(id:int,body:ImportRequest,user=Depends(operator),db=Depends(get_db)):
    i=instance(db,id);preview=import_preview_for(i,body)
    preview['document_version']=i.document_version
    return preview

@app.post('/api/instances/{id}/import')
def commit_import(id:int,body:ImportRequest,user=Depends(operator),db=Depends(get_db)):
    i=instance(db,id);preview=import_preview_for(i,body)
    if body.active_hash!=preview['active_hash']:raise HTTPException(409,'Aktive Hauptdatei wurde geändert; Vorschau erneut laden.')
    if body.preview_hash!=preview['preview_hash']:raise HTTPException(409,'Dateien, Maps oder Import-Inhalt wurden geändert; Vorschau erneut laden.')
    basic_auth.lock_directory(db)
    try:basic_auth.validate_document(db,Document.model_validate(preview['document']))
    except ValueError as error:raise HTTPException(422,str(error))
    i=db.scalar(select(Instance).where(Instance.id==id).with_for_update().execution_options(populate_existing=True))
    if not i:raise HTTPException(404,'Instanz wurde entfernt.')
    if i.document_version!=body.document_version:raise HTTPException(409,'Grafischer Entwurf wurde parallel geändert; Vorschau erneut laden.')
    i.document=preview['document'];i.document_version+=1
    audit(db,user.username,'configuration.imported',i.name);db.commit()
    return i.document|{'version':i.document_version}

@app.middleware('http')
async def security_headers(request,call_next):
    if request.url.path.startswith('/api') and request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('origin')
        if origin and origin!=settings.app_origin.rstrip('/'):
            return JSONResponse({'detail':'Nicht erlaubter Ursprung.'},status_code=403)
        if request.method!='DELETE' and request.headers.get('content-type','').split(';')[0]!='application/json':
            return JSONResponse({'detail':'JSON erforderlich.'},status_code=415)
    length=request.headers.get('content-length','0')
    if not length.isdigit() or int(length)>2*1024*1024:
        return JSONResponse({'detail':'Anfrage zu groß.'},status_code=413)
    if request.method in ('POST','PUT','PATCH') and len(await request.body())>2*1024*1024:
        return JSONResponse({'detail':'Anfrage zu groß.'},status_code=413)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith('/api'): response.headers['Cache-Control']='no-store'
    return response

@app.get('/api/health')
def health(db=Depends(get_db)):
    db.execute(select(1));return {'status':'ok','database':'MariaDB'}

@app.post('/api/auth/login')
def login(body:LoginIn,request:Request,response:Response,db=Depends(get_db)):
    # Single application worker; never trust X-Forwarded-For from an unauthenticated caller.
    key=(request.client.host,body.username.lower()); stamp=time.monotonic()
    with rate_lock:
        # Also cap attempts across accounts from the same source.
        for k in (key,(request.client.host,'*')):
            while failures[k] and failures[k][0]<stamp-900: failures[k].popleft()
        if len(failures[key])>=8 or len(failures[(request.client.host,'*')])>=40:
            raise HTTPException(429,'Zu viele Anmeldeversuche. Bitte 15 Minuten warten.')
        failures[key].append(stamp);failures[(request.client.host,'*')].append(stamp)
    user=db.scalar(select(User).where(User.username==body.username))
    try: ph.verify(user.password_hash if user else dummy_hash,body.password)
    except (VerifyMismatchError,InvalidHashError):
        audit(db,body.username,'login.failed',detail=request.client.host);db.commit()
        raise HTTPException(401,'Benutzername oder Passwort ist falsch.')
    if not user: raise HTTPException(401,'Benutzername oder Passwort ist falsch.')
    with rate_lock: failures.pop(key,None)
    token=secrets.token_urlsafe(48)
    db.add(LoginSession(token_hash=token_hash(token),user_id=user.id,expires_at=now()+timedelta(hours=12)))
    audit(db,user.username,'login.success');db.commit()
    response.set_cookie('haproxy_session',token,max_age=43200,httponly=True,secure=settings.cookie_secure,samesite='strict',path='/')
    return {'user':user_data(user),'csrf':csrf(token)}

@app.get('/api/auth/me')
def me(request:Request,user=Depends(current_user)):
    return {'user':user_data(user),'csrf':csrf(request.cookies['haproxy_session'])}

@app.post('/api/auth/logout')
def logout(request:Request,response:Response,user=Depends(current_user),db=Depends(get_db)):
    db.execute(delete(LoginSession).where(LoginSession.token_hash==token_hash(request.cookies['haproxy_session'])))
    db.commit();response.delete_cookie('haproxy_session',path='/');return {'ok':True}

@app.post('/api/auth/password')
def password(body:PasswordIn,request:Request,response:Response,user=Depends(current_user),db=Depends(get_db)):
    try: ph.verify(user.password_hash,body.current_password)
    except VerifyMismatchError: raise HTTPException(422,'Aktuelles Passwort ist falsch.')
    if body.current_password==body.password: raise HTTPException(422,'Bitte ein neues Passwort wählen.')
    user.password_hash=ph.hash(body.password);user.must_change_password=False
    # Password changes invalidate every existing session, including the current one.
    db.execute(delete(LoginSession).where(LoginSession.user_id==user.id))
    audit(db,user.username,'password.changed');db.commit()
    response.delete_cookie('haproxy_session',path='/');return {'ok':True}

@app.get('/api/instances')
def list_instances(user=Depends(current_user),db=Depends(get_db)):
    out=[]
    for i,metric in db.execute(select(Instance,MetricLatest).outerjoin(MetricLatest).order_by(Instance.id)):
        out.append(public_instance(i)|{'stats':metric.data if metric else None,'collected_at':metric.collected_at.isoformat()+'Z' if metric else None,'fresh_for_seconds':max(90,settings.metrics_interval*3)})
    return out

@app.post('/api/instances')
def add_instance(body:InstanceIn,user=Depends(admin),db=Depends(get_db)):
    duplicate=db.scalar(select(Instance.id).where(Instance.agent_url==body.agent_url,Instance.profile==body.profile))
    if duplicate: raise HTTPException(409,'Dieses Agent-Profil ist bereits angebunden.')
    i=Instance(name=body.name,agent_url=body.agent_url,profile=body.profile,allow_http=body.allow_http,
               notes=body.notes,token_cipher=cipher.encrypt(body.token.encode()).decode(),document=Document().model_dump())
    cap=agent(i);i.kind=cap['kind']
    db.add(i);db.flush();audit(db,user.username,'instance.created',i.name);db.commit()
    return public_instance(i)

@app.put('/api/instances/{id}')
def edit_instance(id:int,body:InstanceIn,user=Depends(admin),db=Depends(get_db)):
    i=instance(db,id)
    if db.scalar(select(Instance.id).where(Instance.id!=id,Instance.agent_url==body.agent_url,Instance.profile==body.profile)):
        raise HTTPException(409,'Dieses Agent-Profil ist bereits angebunden.')
    candidate=Instance(agent_url=body.agent_url,profile=body.profile,token_cipher=cipher.encrypt(body.token.encode()).decode())
    cap=agent(candidate)
    for key in ('name','agent_url','profile','allow_http','notes'): setattr(i,key,getattr(body,key))
    i.token_cipher=candidate.token_cipher;i.kind=cap['kind']
    audit(db,user.username,'instance.updated',i.name);db.commit();return public_instance(i)

@app.delete('/api/instances/{id}')
def remove_instance(id:int,user=Depends(admin),db=Depends(get_db)):
    i=instance(db,id);audit(db,user.username,'instance.removed',i.name);db.delete(i);db.commit();return {'ok':True}

@app.get('/api/instances/{id}/capabilities')
def capabilities(id:int,user=Depends(current_user),db=Depends(get_db)): return agent(instance(db,id))

@app.get('/api/instances/{id}/config')
def config(id:int,user=Depends(operator),db=Depends(get_db)): return agent(instance(db,id),'/config')

@app.get('/api/instances/{id}/document')
def document(id:int,user=Depends(operator),db=Depends(get_db)):
    i=instance(db,id);return i.document|{'version':i.document_version}

@app.put('/api/instances/{id}/document')
def save_document(id:int,body:Document,user=Depends(operator),db=Depends(get_db)):
    basic_auth.lock_directory(db)
    try:basic_auth.validate_document(db,body)
    except ValueError as error:raise HTTPException(422,str(error))
    i=db.scalar(select(Instance).where(Instance.id==id).with_for_update().execution_options(populate_existing=True))
    if not i: raise HTTPException(404)
    if body.version!=i.document_version: raise HTTPException(409,'Entwurf wurde parallel geändert. Bitte neu laden.')
    i.document=body.model_dump(exclude={'version'});i.document_version+=1
    audit(db,user.username,'document.saved',i.name);db.commit();return i.document|{'version':i.document_version}

@app.post('/api/instances/{id}/generate')
def generate_config(id:int,user=Depends(operator),db=Depends(get_db)):
    i=instance(db,id);cap=agent(i)
    current=agent(i,'/config')
    try:
        doc=Document.model_validate(i.document)
        if doc.imported_config is not None:
            if doc.imported_active_hash!=current['hash']:raise HTTPException(409,'Konfiguration seit dem Import geändert. Erneut importieren, damit externe Änderungen erhalten bleiben.')
            if doc.imported_sources:
                bundle=agent(i,'/config-bundle')
                if {s.path:s.hash for s in doc.imported_sources}!={s['path']:s['hash'] for s in bundle['sources']} or {m.path:m.hash for m in doc.imported_map_hashes}!={m['host_path']:m['hash'] for m in bundle['maps']}:
                    raise HTTPException(409,'Eine Konfigurations- oder Map-Datei wurde geändert. Erneut importieren.')
        config=generate(doc,cap,basic_auth.snapshots(db,basic_auth.ids(doc)))
        if len(config.encode())>1024*1024:raise ValueError('Erzeugte Konfiguration mit Basic-Auth-Benutzern ist größer als 1 MB.')
    except ValueError as e: raise HTTPException(422,str(e))
    return {'config':config,'base_hash':current['hash']}

@app.get('/api/instances/{id}/revisions')
def revisions(id:int,user=Depends(operator),db=Depends(get_db)):
    instance(db,id)
    return [{'id':r.id,'status':r.status,'message':r.message,'author':r.author,'created_at':r.created_at.isoformat()+'Z','applied_at':r.applied_at.isoformat()+'Z' if r.applied_at else None}
            for r in db.scalars(select(Revision).where(Revision.instance_id==id).order_by(Revision.id.desc()).limit(100))]

@app.get('/api/instances/{id}/revisions/{rev}')
def read_revision(id:int,rev:int,user=Depends(operator),db=Depends(get_db)):
    r=db.get(Revision,rev)
    if not r or r.instance_id!=id: raise HTTPException(404)
    return {'config':r.config,'base_hash':r.base_hash,'id':r.id,'status':r.status}

@app.post('/api/instances/{id}/revisions')
def save_revision(id:int,body:DraftIn,user=Depends(operator),db=Depends(get_db)):
    i=instance(db,id)
    r=Revision(instance_id=id,config=body.config,base_hash=body.base_hash,message=body.message,author=user.username)
    db.add(r);db.flush();audit(db,user.username,'revision.saved',i.name,str(r.id));db.commit();return {'id':r.id}

@app.post('/api/instances/{id}/validate')
def validate_config(id:int,body:DraftIn,user=Depends(operator),db=Depends(get_db)):
    return agent(instance(db,id),'/validate','POST',{'config':body.config,'expected_hash':body.base_hash},timeout=40)

@app.post('/api/instances/{id}/revisions/{rev}/apply')
def apply_revision(id:int,rev:int,user=Depends(operator),db=Depends(get_db)):
    i=instance(db,id);r=db.scalar(select(Revision).where(Revision.id==rev).with_for_update())
    if not r or r.instance_id!=id: raise HTTPException(404)
    if r.status in ('applied','applying','uncertain'): raise HTTPException(409,'Diese Version wurde bereits angewendet. Für einen Rollback als neuen Entwurf laden.')
    try:auth_metadata=basic_auth.assert_current(db,r.config)
    except ValueError as error:raise HTTPException(409,str(error))
    # Persist the previous configuration before executing the remote mutation.
    previous=agent(i,'/config')
    if previous['hash']!=r.base_hash: raise HTTPException(409,'Aktive Konfiguration wurde geändert. Neu laden und abgleichen.')
    try:context=migration_context(r.config)
    except ValueError as error:raise HTTPException(422,str(error))
    previous_config=previous['config']
    if context:
        bundle=agent(i,'/config-bundle');previous_config=bundle['config']
    previous_config=''.join(line for line in previous_config.splitlines(keepends=True) if not line.startswith(MIGRATION_PREFIX))
    snapshot=Revision(instance_id=id,config=previous_config,base_hash=previous['hash'],status='snapshot',message='Sicherung vor Version '+str(rev),author=user.username)
    db.add(snapshot);r.status='applying';audit(db,user.username,'revision.apply.requested',i.name,str(rev));db.commit()
    try: result=agent(i,'/apply','POST',{'config':r.config,'expected_hash':r.base_hash},timeout=65)
    except HTTPException as error:
        # A network timeout is ambiguous: preserve that distinction in the audit trail.
        r.status='uncertain' if error.status_code==502 and str(error.detail).startswith('Agent nicht erreichbar') else 'failed'
        audit(db,user.username,'revision.apply.'+r.status,i.name,str(error.detail));db.commit();raise
    r.status='applied';r.applied_at=now()
    i=db.scalar(select(Instance).where(Instance.id==id).with_for_update().execution_options(populate_existing=True))
    if not i:raise HTTPException(404,'Instanz wurde während des Anwendens entfernt.')
    if i.document.get('imported_config') is not None:
        try:
            doc=Document.model_validate(i.document)
            matches=generate(doc,{},basic_auth.snapshots(db,basic_auth.ids(doc)))==r.config
        except ValueError:matches=False
        if matches:
            document=dict(i.document);document['imported_active_hash']=result['hash']
            if result.get('sources') is not None:
                document['imported_sources']=result['sources'];document['imported_map_hashes']=result['map_hashes']
            i.document=document;i.document_version+=1
    deployment=db.get(BasicAuthDeployment,id)
    if auth_metadata or deployment:
        if not deployment:deployment=BasicAuthDeployment(instance_id=id);db.add(deployment)
        deployment.metadata_json=auth_metadata or {};deployment.applied_at=now()
    audit(db,user.username,'revision.applied',i.name,str(rev));db.commit()
    return result

@app.post('/api/instances/{id}/service/{action}')
def service(id:int,action:str,user=Depends(operator),db=Depends(get_db)):
    if action not in ('reload','start','restart','stop'): raise HTTPException(404)
    if action=='stop' and user.role!='admin': raise HTTPException(403,'Stoppen benötigt Administratorrechte.')
    i=instance(db,id)
    audit(db,user.username,'service.'+action+'.requested',i.name);db.commit()
    try: result=agent(i,'/service/'+action,'POST',{},timeout=50)
    except HTTPException as e:
        audit(db,user.username,'service.'+action+'.failed',i.name,str(e.detail));db.commit();raise
    audit(db,user.username,'service.'+action,i.name);db.commit();return result

@app.get('/api/instances/{id}/stats')
def stats(id:int,user=Depends(current_user),db=Depends(get_db)):
    i=instance(db,id);data=agent(i,'/stats')
    try:
        bundle=agent(i,'/config-bundle')
        data=enrich_stats(data,bundle['config'],bundle['maps'])
    except HTTPException:
        try:data=enrich_stats(data,agent(i,'/config')['config'])
        except HTTPException:data=enrich_stats(data)
    # Live detail responses never create history rows; only the collector writes.
    return data|{'history_policy':{'raw_hours':settings.metrics_raw_hours,'fine_days':settings.metrics_fine_days,'total_days':settings.metrics_retention_days}}

@app.get('/api/instances/{id}/topology')
def topology(id:int,user=Depends(current_user),db=Depends(get_db)):
    i=instance(db,id);data=agent(i,'/stats')
    captured_at=now().isoformat()+'Z'
    # Only routing inputs are cached, bounded in memory. No runtime or graph rows
    # enter MariaDB. Identity changes invalidate an existing instance's cache.
    identity=(i.agent_url,i.profile,i.token_cipher)
    with topology_cache_lock:cached=topology_cache.get(id)
    config=None;maps=[];warning=None
    if cached and cached[0]==identity and time.monotonic()-cached[1]<30:
        config,maps=cached[2:]
    else:
        try:
            bundle=agent(i,'/config-bundle');config=bundle['config'];maps=bundle.get('maps',[])
        except HTTPException:
            try:config=agent(i,'/config')['config']
            except HTTPException:warning='Aktive Konfiguration konnte nicht gelesen werden.'
        if config is not None:
            with topology_cache_lock:
                topology_cache.pop(id,None)
                if len(topology_cache)>=128:topology_cache.pop(next(iter(topology_cache)))
                topology_cache[id]=(identity,time.monotonic(),config,maps)
    graph=topology_store.build(config,maps,data)
    graph['captured_at']=captured_at
    if warning:graph['warnings'].append(warning)
    return graph

@app.get('/api/instances/{id}/metrics')
def metrics(id:int,hours:int=1,user=Depends(current_user),db=Depends(get_db)):
    instance(db,id)
    if hours not in (1,6,24,168): raise HTTPException(422,'Zeitraum: 1, 6, 24 oder 168 Stunden.')
    return metric_store.history(db,id,hours,settings)

@app.get('/api/metrics/storage')
def metric_storage(user=Depends(admin),db=Depends(get_db)):
    return metric_store.storage(db,settings)

@app.post('/api/metrics/storage/compact')
def compact_metric_storage(user=Depends(admin),db=Depends(get_db)):
    if not collection_lock.acquire(blocking=False):raise HTTPException(409,'Metriksammlung läuft gerade. Erneut versuchen.')
    try:
        try:metric_store.optimize_legacy(db)
        except ValueError as error:raise HTTPException(409,str(error))
        except (SQLAlchemyError,RuntimeError) as error:
            db.rollback();logger.exception('Legacy metric table optimization failed')
            raise HTTPException(422,'MariaDB konnte die alte Metriktabelle nicht optimieren. Datenbankrechte und Serverlog prüfen.') from error
        audit(db,user.username,'metrics.storage.compacted');db.commit()
        return metric_store.storage(db,settings)
    finally:collection_lock.release()

@app.get('/api/instances/{id}/certificates')
def certificates(id:int,user=Depends(current_user),db=Depends(get_db)): return agent(instance(db,id),'/certificates')

@app.post('/api/instances/{id}/certificates/issue')
def issue(id:int,body:CertificateIn,user=Depends(operator),db=Depends(get_db)):
    i=instance(db,id);audit(db,user.username,'certificate.issue.requested',i.name,','.join(body.domains));db.commit()
    try: result=agent(i,'/certificates/issue','POST',body.model_dump(),timeout=300)
    except HTTPException as e:
        audit(db,user.username,'certificate.issue.failed',i.name,str(e.detail));db.commit();raise
    audit(db,user.username,'certificate.issued',i.name,body.name);db.commit();return result

@app.post('/api/instances/{id}/certificates/import')
def import_cert(id:int,body:dict,user=Depends(operator),db=Depends(get_db)):
    from agent.main import PemIn
    try: payload=PemIn.model_validate(body)
    except ValueError: raise HTTPException(422,'Name und gültiges PEM erforderlich.')
    i=instance(db,id);result=agent(i,'/certificates/import','POST',payload.model_dump(),timeout=65)
    audit(db,user.username,'certificate.imported',i.name,payload.name);db.commit();return result

@app.post('/api/instances/{id}/certificates/renew')
def renew(id:int,user=Depends(operator),db=Depends(get_db)):
    i=instance(db,id);result=agent(i,'/certificates/renew','POST',{},timeout=300)
    audit(db,user.username,'certificates.renewed',i.name);db.commit();return result

@app.get('/api/audit')
def audit_log(user=Depends(admin),db=Depends(get_db)):
    return [{'id':a.id,'actor':a.actor,'action':a.action,'target':a.target,'detail':a.detail,'created_at':a.created_at.isoformat()+'Z'} for a in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(200))]

@app.get('/api/agent-guide')
def agent_guide(user=Depends(admin)):
    return FileResponse(Path(__file__).resolve().parent.parent/'docs'/'AGENT.md',filename='HAProxy-Agent-Anleitung.md',media_type='text/markdown')

@app.get('/api/agent-config')
def agent_config(user=Depends(admin)):
    import json
    path=Path(__file__).resolve().parent.parent/'agent'/'config.example.json'
    content=json.loads(path.read_text())
    for profile in content['profiles'].values(): profile['token']=secrets.token_urlsafe(48)
    return Response(json.dumps(content,indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="agent.json"','Cache-Control':'no-store'})

@app.get('/api/users')
def users(user=Depends(admin),db=Depends(get_db)): return [user_data(u) for u in db.scalars(select(User).order_by(User.id))]

@app.post('/api/users')
def add_user(body:UserIn,user=Depends(admin),db=Depends(get_db)):
    if db.scalar(select(User.id).where(User.username==body.username)): raise HTTPException(409,'Benutzername bereits vergeben.')
    u=User(username=body.username,password_hash=ph.hash(body.password),role=body.role,must_change_password=True)
    db.add(u);audit(db,user.username,'user.created',body.username,body.role);db.commit();return user_data(u)

@app.delete('/api/users/{id}')
def delete_user(id:int,user=Depends(admin),db=Depends(get_db)):
    if id==user.id: raise HTTPException(422,'Eigenes Konto kann hier nicht gelöscht werden.')
    u=db.get(User,id)
    if not u: raise HTTPException(404)
    if u.role=='admin' and db.scalar(select(func.count()).select_from(User).where(User.role=='admin'))<=1:
        raise HTTPException(422,'Der letzte Administrator darf nicht gelöscht werden.')
    audit(db,user.username,'user.deleted',u.username);db.delete(u);db.commit();return {'ok':True}

DIST=Path(__file__).resolve().parent.parent/'frontend'/'dist'
if DIST.exists():
    app.mount('/assets',StaticFiles(directory=DIST/'assets'),name='assets')
    @app.get('/{path:path}')
    def frontend(path:str):
        if path.startswith('api/'): raise HTTPException(404)
        candidate=(DIST/path).resolve()
        if candidate.is_relative_to(DIST) and candidate.is_file(): return FileResponse(candidate)
        return FileResponse(DIST/'index.html')
