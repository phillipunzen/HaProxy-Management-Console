"""Profile-scoped ACME jobs and renewal schedules. Credentials stay on the host."""
import json
import re
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from backend.schemas import CertificateIn,RenewalSettingsIn


def services():
    from agent import main
    return main


def entries(p):
    path=services().cert_state(p)
    return json.loads(path.read_text()) if path.exists() else {}


def save_entries(p,value):
    services().atomic(services().cert_state(p),json.dumps(value).encode(),0o600)


def settings_path(p):
    a=services()
    return a.STATE_DIR/(a.sha(p['config_path'])+'-renewal.json')


def schedule_state(p):
    path=settings_path(p)
    return json.loads(path.read_text()) if path.exists() else {}


def schedule(p):
    state=schedule_state(p)
    return RenewalSettingsIn.model_validate(state.get('settings',{}))


def save_schedule(p,body):
    a=services();state=schedule_state(p)
    state['settings']=body.model_dump()
    state.pop('last_attempt',None)
    a.atomic(settings_path(p),json.dumps(state).encode(),0o600)
    return schedule_public(p)


def next_check(settings,last=None,now=None):
    now=now or datetime.now(timezone.utc)
    if not settings.enabled:return None
    if settings.schedule=='interval':
        return datetime.fromisoformat(last)+timedelta(hours=settings.interval_hours) if last else now
    local=now.astimezone(ZoneInfo(settings.timezone))
    hour,minute=map(int,settings.daily_time.split(':'))
    candidate=local.replace(hour=hour,minute=minute,second=0,microsecond=0)
    # A missed daily check is run on restart; remember the local calendar day
    # to avoid repeating it after downtime or a daylight-saving clock change.
    if last and datetime.fromisoformat(last).astimezone(local.tzinfo).date()>=local.date():
        candidate+=timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def schedule_public(p):
    state=schedule_state(p);settings=schedule(p)
    due=next_check(settings,state.get('last_attempt'))
    return settings.model_dump() | {'next_check':due.isoformat() if due else None,
        'last_attempt':state.get('last_attempt'),'last_success':state.get('last_success'),
        'last_error':state.get('last_error'),'last_result':state.get('last_result')}


def scheduled_check(p,now=None):
    a=services();now=now or datetime.now(timezone.utc)
    with a.lock(p):
        state=schedule_state(p);settings=schedule(p)
        due=next_check(settings,state.get('last_attempt'),now)
        if due is None or due>now:return
        state['last_attempt']=now.isoformat()
        try:
            result=renew(p,automatic=True)
            state.update(last_result=result,last_error='Einzelne Zertifikate konnten nicht erneuert werden.' if result['errors'] else None)
            if not result['errors']:state['last_success']=datetime.now(timezone.utc).isoformat()
        except Exception as error:
            # Keep a short actionable failure, never command stdout or secrets.
            state['last_error']=f'Erneuerung fehlgeschlagen (HTTP {error.status_code}). Agent-Log prüfen.' if isinstance(error,HTTPException) else 'Erneuerung fehlgeschlagen. Agent-Log prüfen.'
            raise
        finally:a.atomic(settings_path(p),json.dumps(state).encode(),0o600)


def lego_config(p):
    conf=p.get('lego')
    if not conf:raise HTTPException(422,'LEGO v5 zuerst im Agent-Profil einrichten. Anleitung unter Zertifikate.')
    for key in ('path','env_file'):
        value=conf.get(key,'')
        if not value.startswith('/') or '..' in value.split('/') or any(c.isspace() for c in value):
            raise HTTPException(422,'LEGO benötigt absolute lokale Pfade ohne Leerzeichen oder übergeordnete Verzeichnisse.')
    return conf


def lego_args(p,body,directory,source_name,force=False):
    conf=lego_config(p)
    args=[conf.get('binary','lego'),'run','--accept-tos','--env-file',conf['env_file'],
          '--email',body.email,'--server','letsencrypt-staging' if body.staging else 'letsencrypt',
          '--pem','--path',str(directory),'--cert.name',source_name,'--force-cert-domains']
    if force:args+=['--renew-force']
    if body.challenge=='dns':
        if body.provider!='cloudflare':raise HTTPException(422,'LEGO-Anbindung unterstützt Cloudflare DNS-01.')
        args+=['--dns','cloudflare']
        for resolver in conf.get('resolvers',[]):args+=['--dns.resolvers',resolver]
        wait=conf.get('propagation_wait')
        if wait:
            if not re.fullmatch(r'\d+(?:s|m)',str(wait)):raise HTTPException(422,'LEGO propagation_wait z. B. 5s oder 1m.')
            args+=['--dns.propagation.wait',str(wait)]
    else:
        if not p.get('acme_webroot'):raise HTTPException(422,'HTTP-Webroot ist im Agent-Profil nicht eingerichtet.')
        args+=['--http','--http.webroot',p['acme_webroot']]
    for domain in body.domains:args+=['--domains',domain]
    return args


def lego_pem(directory,source_name):
    directory=Path(directory)/'certificates'
    try:return (directory/(source_name+'.crt')).read_bytes()+(directory/(source_name+'.key')).read_bytes()
    except OSError:raise HTTPException(422,'LEGO-Zertifikat oder Schlüssel fehlt im konfigurierten Verzeichnis. Zertifikatsname und LEGO-Pfad prüfen.')


def ensure_target(p,key,body,values):
    if any(k!=key and e['name']==body.name and e['staging']==body.staging for k,e in values.items()):
        raise HTTPException(409,'Dieser PEM-Name wird bereits von einem anderen ACME-Auftrag verwaltet. Einen anderen Namen verwenden.')


def ensure_lego_lineage(p,directory,source_name,body):
    # An existing shared LEGO store must not cause two profiles to renew the
    # same lineage concurrently. Newly issued jobs use a dedicated directory.
    a=services()
    for other in list(a.PROFILES.values())+[p]:
        for e in entries(other).values():
            if e.get('engine')!='lego':continue
            if Path(e['directory']).resolve()==Path(directory).resolve() and e['source_name']==source_name:
                if other['config_path']!=p['config_path'] or (e['name'],e['staging'])!=(body.name,body.staging):
                    raise HTTPException(409,'Dieser LEGO-Auftrag ist bereits einem anderen Zertifikat oder Profil zugewiesen.')


def issue(p,body):
    if body.engine=='lego':
        a=services();conf=lego_config(p)
        directory=Path(conf['path'])/'control'/a.sha(p['config_path'])[:16]/(body.name+('-staging' if body.staging else '-production'))
        with a.lock({'config_path':'lego:'+str(directory.resolve())+':'+body.name}):return _issue(p,body)
    return _issue(p,body)


def _issue(p,body):
    a=services();a.require_certificate_scope(p);values=entries(p)
    if body.engine=='lego':
        conf=lego_config(p)
        directory=Path(conf['path'])/'control'/a.sha(p['config_path'])[:16]/(body.name+('-staging' if body.staging else '-production'))
        source_name=body.name
        key='lego:'+a.sha(str(directory)+':'+source_name)
        ensure_target(p,key,body,values);ensure_lego_lineage(p,directory,source_name,body)
        a.run(lego_args(p,body,directory,source_name),timeout=240)
        pem=lego_pem(directory,source_name)
        entry={'engine':'lego','directory':str(directory),'source_name':source_name,'request':body.model_dump()}
    else:
        key,args=a.certbot_args(p,body)
        ensure_target(p,key,body,values)
        a.run(args,timeout=240)
        directory=Path(p.get('letsencrypt_dir','/etc/letsencrypt'))/'live'/key
        pem=(directory/'fullchain.pem').read_bytes()+(directory/'privkey.pem').read_bytes()
        entry={'engine':'certbot'}
    result=a.install_pem(p,body.name,pem,body.staging)
    values[key]=entry | {'name':body.name,'staging':body.staging,'automatic':body.automatic}
    save_entries(p,values)
    return result


def adopt_lego(p,body):
    a=services();conf=lego_config(p)
    with a.lock({'config_path':'lego:'+str(Path(conf['path']).resolve())+':'+body.source_name}):return _adopt_lego(p,body)


def _adopt_lego(p,body):
    a=services();a.require_certificate_scope(p);conf=lego_config(p)
    directory=Path(conf['path']);values=entries(p)
    key='lego:'+a.sha(str(directory)+':'+body.source_name)
    ensure_target(p,key,body,values);ensure_lego_lineage(p,directory,body.source_name,body)
    pem=lego_pem(directory,body.source_name)
    from cryptography import x509
    try:
        cert=x509.load_pem_x509_certificate(pem)
        domains=cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except (ValueError,x509.ExtensionNotFound):raise HTTPException(422,'LEGO-Zertifikat enthält keine gültigen Domains.')
    if set(domains)!=set(body.domains):raise HTTPException(422,'Alle Domains des vorhandenen LEGO-Zertifikats exakt übernehmen. Die erste Domain allein reicht bei einem Sammelzertifikat nicht aus.')
    result=a.install_pem(p,body.name,pem)
    values[key]={'name':body.name,'staging':False,'engine':'lego','directory':str(directory),
        'source_name':body.source_name,'request':body.model_dump(exclude={'source_name'}),'automatic':body.automatic}
    save_entries(p,values)
    return result


def policy(p,name,body):
    a=services();a.require_certificate_scope(p);values=entries(p)
    matches=[e for e in values.values() if e['name']==name and not e['staging']]
    if not matches:raise HTTPException(404,'Zertifikat wird nicht durch einen ACME-Auftrag verwaltet. LEGO übernehmen oder Zertifikat neu anfordern.')
    for e in matches:e['automatic']=body.automatic
    save_entries(p,values)
    return {'name':name,'automatic':body.automatic}


def forget(p,name):
    # Uploading a manually managed PEM cancels the previous ACME assignment so
    # a later scheduled check cannot silently overwrite it.
    values={k:e for k,e in entries(p).items() if e['name']!=name or e['staging']}
    save_entries(p,values)


def renew(p,name=None,force=False,automatic=False):
    a=services();a.require_certificate_scope(p);values=entries(p)
    candidates=[(k,e) for k,e in values.items() if not e['staging'] and (not name or e['name']==name) and (not automatic or e.get('automatic',True))]
    if name and not candidates:raise HTTPException(404,'Für dieses Zertifikat ist kein Produktions-ACME-Auftrag hinterlegt.')
    renewed=[];errors=[]
    for key,e in candidates:
        try:
            if e.get('engine')=='lego':
                body=CertificateIn.model_validate(e['request'])
                with a.lock({'config_path':'lego:'+str(Path(e['directory']).resolve())+':'+e['source_name']}):
                    a.run(lego_args(p,body,e['directory'],e['source_name'],force),timeout=240)
                    pem=lego_pem(e['directory'],e['source_name'])
            else:
                args=[p.get('certbot_binary','certbot'),'renew','--non-interactive','--cert-name',key]
                if force:args+=['--force-renewal']
                a.run(args,timeout=240)
                directory=Path(p.get('letsencrypt_dir','/etc/letsencrypt'))/'live'/key
                pem=(directory/'fullchain.pem').read_bytes()+(directory/'privkey.pem').read_bytes()
            target=Path(p['cert_dir'])/(e['name']+'.pem')
            if not target.exists() or target.read_bytes()!=pem:renewed.append(a.install_pem(p,e['name'],pem))
        except Exception as error:
            a.logger.exception('Certificate renewal failed for %s',e['name'])
            errors.append({'name':e['name'],'error':f'Erneuerung fehlgeschlagen (HTTP {error.status_code}). Agent-Log prüfen.' if isinstance(error,HTTPException) else 'Erneuerung fehlgeschlagen. Agent-Log prüfen.'})
    if errors and name:raise HTTPException(422,errors[0]['error'])
    return {'renewed':renewed,'checked':len(candidates),'errors':errors}


def status(p,name,staging):
    entry=next((e for e in entries(p).values() if (e['name'],e['staging'])==(name,staging)),None)
    return {'managed':entry is not None,'engine':entry.get('engine','certbot') if entry else 'manual',
            'automatic':bool(entry and not staging and entry.get('automatic',True))}
