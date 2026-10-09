import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from agent import main as a
from agent import challenge as c
from backend.schemas import CertificateIn


def test_cloudflare_multidomain_certbot_arguments_are_separate():
    p={'config_path':'/etc/haproxy/haproxy.cfg','dns_providers':{'cloudflare':{'credentials_file':'/etc/haproxy-control/cloudflare.ini','propagation_seconds':65}}}
    body=CertificateIn(name='multi',domains=['example.com','*.example.com','example.net'],email='admin@example.com',challenge='dns',provider='cloudflare',staging=True)
    name,args=a.certbot_args(p,body)
    assert name.endswith('-multi-staging')
    assert '--dns-cloudflare' in args
    assert args[args.index('--dns-cloudflare-credentials')+1]=='/etc/haproxy-control/cloudflare.ini'
    assert args.count('-d')==3 and '*.example.com' in args and '--staging' in args
    assert not any(' ' in part for part in args)


def test_unconfigured_provider_cannot_supply_file_paths():
    body=CertificateIn(name='x',domains=['example.com'],email='admin@example.com',challenge='dns',provider='cloudflare')
    with pytest.raises(HTTPException): a.certbot_args({'config_path':'/etc/haproxy/haproxy.cfg'},body)


def test_http_uses_only_configured_webroot():
    body=CertificateIn(name='x',domains=['example.com'],email='admin@example.com',challenge='http',staging=False)
    p={'config_path':'/etc/haproxy/haproxy.cfg','acme_webroot':'/var/lib/haproxy-control/webroot'}
    name,args=a.certbot_args(p,body)
    assert '--webroot' in args and args[args.index('-w')+1]==p['acme_webroot']
    assert '--staging' not in args


def test_renewal_installs_only_changed_production_certificates(tmp_path,monkeypatch):
    p={'config_path':str(tmp_path/'haproxy.cfg'),'cert_dir':str(tmp_path/'certs'),'letsencrypt_dir':str(tmp_path/'letsencrypt')}
    monkeypatch.setattr(a,'STATE_DIR',tmp_path)
    entries={'cert-prod':{'name':'prod','staging':False},'cert-test':{'name':'test','staging':True}}
    a.cert_state(p).write_text(json.dumps(entries))
    directory=Path(p['letsencrypt_dir'])/'live'/'cert-prod';directory.mkdir(parents=True)
    (directory/'fullchain.pem').write_bytes(b'chain');(directory/'privkey.pem').write_bytes(b'key')
    target=Path(p['cert_dir']);target.mkdir();(target/'prod.pem').write_bytes(b'chainkey')
    calls=[];installed=[]
    monkeypatch.setattr(a,'run',lambda args,timeout:calls.append(args))
    monkeypatch.setattr(a,'install_pem',lambda p,name,pem:installed.append(name) or {'name':name})
    result=a.renew(p)
    assert result['renewed']==[] and len(calls)==1 and 'cert-prod' in calls[0]
    (directory/'fullchain.pem').write_bytes(b'newchain')
    result=a.renew(p)
    assert result['renewed']==[{'name':'prod'}] and installed==['prod']


def test_webroot_serves_only_challenge_tokens(tmp_path,monkeypatch):
    monkeypatch.setattr(c,'ROOT',tmp_path)
    directory=tmp_path/'.well-known/acme-challenge';directory.mkdir(parents=True)
    (directory/'valid_token-123').write_text('challenge-response')
    (tmp_path/'private.key').write_text('SECRET')
    with TestClient(c.app) as client:
        assert client.get('/.well-known/acme-challenge/valid_token-123').text=='challenge-response'
        assert client.get('/private.key').status_code==404
        assert client.get('/.well-known/acme-challenge/no-such-token').status_code==404
        assert client.get('/.well-known/acme-challenge/%2E%2E%2Fprivate.key').status_code==404

@pytest.mark.parametrize('alias',['same','nested','symlink'])
def test_shared_certificate_directory_blocks_writes(tmp_path,monkeypatch,alias):
    directory=tmp_path/'certs';directory.mkdir()
    other_directory=directory if alias=='same' else directory/'child'
    if alias=='symlink':
        other_directory=tmp_path/'alias';other_directory.symlink_to(directory,target_is_directory=True)
    p={'config_path':str(tmp_path/'a.cfg'),'cert_dir':str(directory)}
    q={'config_path':str(tmp_path/'b.cfg'),'cert_dir':str(other_directory)}
    monkeypatch.setattr(a,'PROFILES',{'a':p,'b':q})
    monkeypatch.setattr(a,'run',lambda *args,**kw:pytest.fail('Shared directory must not invoke Certbot or reload'))
    assert a.certificate_scope_error(p) and a.certificate_scope_error(q)
    body=CertificateIn(name='x',domains=['example.com'],email='admin@example.com',challenge='http')
    for fn in (lambda:a.issue(p,body),lambda:a.install_pem(p,'x',b'invalid'),lambda:a.renew(p)):
        with pytest.raises(HTTPException) as error:fn()
        assert error.value.status_code==409
    assert list(directory.iterdir())==[]


def test_separate_certificate_directories_allow_identical_names(tmp_path,monkeypatch):
    p={'config_path':str(tmp_path/'a.cfg'),'cert_dir':str(tmp_path/'certs-a')}
    q={'config_path':str(tmp_path/'b.cfg'),'cert_dir':str(tmp_path/'certs-b')}
    monkeypatch.setattr(a,'PROFILES',{'a':p,'b':q})
    assert a.certificate_scope_error(p) is None and a.certificate_scope_error(q) is None

from agent import certificates as jobs
from backend.schemas import CertificateAdoptIn,CertificateRenewIn,RenewalSettingsIn,CertificatePolicyIn
from datetime import datetime,timedelta,timezone
from pydantic import ValidationError

@pytest.fixture
def lego_profile(tmp_path,monkeypatch):
    p={'config_path':str(tmp_path/'haproxy.cfg'),'cert_dir':str(tmp_path/'certs'),
       'lego':{'binary':'/usr/local/bin/lego','path':str(tmp_path/'lego'),'env_file':str(tmp_path/'dns.env'),'resolvers':['1.1.1.1:53','8.8.8.8:53'],'propagation_wait':'5s'}}
    monkeypatch.setattr(a,'STATE_DIR',tmp_path);monkeypatch.setattr(a,'PROFILES',{'test':p})
    return p


def lego_body(**kw):
    return CertificateIn(name='all',domains=['example.com','*.example.com','example.net'],email='admin@example.com',challenge='dns',provider='cloudflare',staging=False,engine='lego',**kw)


def test_lego_v5_arguments_keep_domains_and_host_credentials(lego_profile):
    p=lego_profile;body=lego_body();args=jobs.lego_args(p,body,'/etc/lego','example.com')
    assert args[:2]==['/usr/local/bin/lego','run']
    assert args[args.index('--env-file')+1]==p['lego']['env_file']
    assert args[args.index('--cert.name')+1]=='example.com'
    assert args.count('--domains')==3 and '*.example.com' in args
    assert args.count('--dns.resolvers')==2 and args[args.index('--dns.propagation.wait')+1]=='5s'
    assert '--renew-force' not in args and '--renew-force' in jobs.lego_args(p,body,'/etc/lego','example.com',True)
    assert 'letsencrypt-staging' in jobs.lego_args(p,body.model_copy(update={'staging':True}),'/etc/lego','all')
    with pytest.raises(HTTPException):jobs.lego_args({},body,'/tmp','all')


def test_lego_http_uses_configured_webroot(lego_profile):
    p=lego_profile|{'acme_webroot':'/var/lib/webroot'}
    body=CertificateIn(name='site',domains=['example.com'],email='admin@example.com',challenge='http',engine='lego')
    args=jobs.lego_args(p,body,'/tmp/store','site')
    assert '--http.webroot' in args and '/var/lib/webroot' in args and '--dns' not in args


def make_lego_certificate(p,domains,source='example.com'):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key=rsa.generate_private_key(65537,2048);subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,domains[0])])
    cert=x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key()).serial_number(42).not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=1)).not_valid_after(datetime.now(timezone.utc)+timedelta(days=90)).add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in domains]),critical=False).sign(key,hashes.SHA256())
    directory=Path(p['lego']['path'])/'certificates';directory.mkdir(parents=True)
    (directory/(source+'.crt')).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (directory/(source+'.key')).write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    return jobs.lego_pem(p['lego']['path'],source)


def test_adopt_lego_keeps_full_san_list_and_does_not_issue(lego_profile,monkeypatch):
    p=lego_profile;body=CertificateAdoptIn(**(lego_body().model_dump()|{'source_name':'example.com','name':'cert'}))
    pem=make_lego_certificate(p,body.domains);calls=[]
    monkeypatch.setattr(a,'run',lambda *args,**kw:pytest.fail('Adoption must not issue a certificate'))
    monkeypatch.setattr(a,'install_pem',lambda profile,name,data: calls.append((name,data)) or {'name':name})
    result=jobs.adopt_lego(p,body)
    assert result=={'name':'cert'} and calls==[('cert',pem)]
    entry=next(iter(jobs.entries(p).values()))
    assert entry['request']['domains']==body.domains and entry['source_name']=='example.com' and entry['directory']==p['lego']['path']
    assert jobs.status(p,'cert',False)=={'managed':True,'engine':'lego','automatic':True}
    with pytest.raises(HTTPException):jobs.adopt_lego(p,body.model_copy(update={'domains':['example.com']}))
    assert len(calls)==1


def test_adoption_reload_failure_does_not_register_job(lego_profile,monkeypatch):
    p=lego_profile;body=CertificateAdoptIn(**(lego_body().model_dump()|{'source_name':'example.com'}));make_lego_certificate(p,body.domains)
    def fail(*args):raise HTTPException(502,'reload failed')
    monkeypatch.setattr(a,'install_pem',fail)
    with pytest.raises(HTTPException):jobs.adopt_lego(p,body)
    assert jobs.entries(p)=={}


def test_lego_source_cannot_be_assigned_to_second_profile(lego_profile,monkeypatch):
    p=lego_profile;body=CertificateAdoptIn(**(lego_body().model_dump()|{'source_name':'example.com'}));make_lego_certificate(p,body.domains)
    monkeypatch.setattr(a,'install_pem',lambda p,name,pem:{'name':name})
    jobs.adopt_lego(p,body)
    q=p|{'config_path':p['config_path']+'.other','cert_dir':p['cert_dir']+'-other'};a.PROFILES['other']=q
    with pytest.raises(HTTPException) as error:jobs.adopt_lego(q,body)
    assert error.value.status_code==409 and jobs.entries(q)=={}


def test_new_lego_jobs_use_separate_profile_and_environment_directories(lego_profile,monkeypatch):
    p=lego_profile;calls=[]
    monkeypatch.setattr(a,'run',lambda args,timeout:calls.append(args))
    monkeypatch.setattr(jobs,'lego_pem',lambda *args:b'pem')
    monkeypatch.setattr(a,'install_pem',lambda p,name,pem,staging=False:{'name':name})
    jobs.issue(p,lego_body())
    jobs.issue(p,lego_body().model_copy(update={'staging':True}))
    directories=[args[args.index('--path')+1] for args in calls]
    assert directories[0]!=directories[1] and all('/control/' in v for v in directories)
    assert len(jobs.entries(p))==2


def test_individual_lego_renewal_is_selected_and_manual_ignores_pause(lego_profile,monkeypatch):
    p=lego_profile;body=lego_body();request=body.model_dump();values={}
    for name in ('first','second'):
        values[name]={'name':name,'staging':False,'engine':'lego','automatic':False,'directory':p['lego']['path']+'/'+name,'source_name':name,'request':request|{'name':name}}
    values['test']={'name':'test','staging':True}
    jobs.save_entries(p,values);calls=[]
    monkeypatch.setattr(a,'run',lambda args,timeout:calls.append(args))
    monkeypatch.setattr(jobs,'lego_pem',lambda *args:b'pem')
    monkeypatch.setattr(a,'install_pem',lambda p,name,pem:{'name':name})
    assert jobs.renew(p,automatic=True)['checked']==0
    result=jobs.renew(p,'first',force=True)
    assert result['checked']==1 and result['renewed']==[{'name':'first'}] and len(calls)==1
    assert '--renew-force' in calls[0] and calls[0][calls[0].index('--cert.name')+1]=='first'
    assert jobs.renew(p)['checked']==2
    with pytest.raises(HTTPException):jobs.renew(p,'not-managed')
    with pytest.raises(ValidationError):CertificateRenewIn(force=True)


def test_renewal_failures_do_not_skip_other_jobs(lego_profile,monkeypatch):
    p=lego_profile;values={name:{'name':name,'staging':False,'engine':'lego','directory':p['lego']['path']+'/'+name,'source_name':name,'request':lego_body().model_dump()|{'name':name}} for name in ('failed','working')};jobs.save_entries(p,values)
    def run(args,timeout):
        if args[args.index('--cert.name')+1]=='failed':raise HTTPException(422,'secret-like provider output must not reach the API')
    monkeypatch.setattr(a,'run',run);monkeypatch.setattr(jobs,'lego_pem',lambda *args:b'pem');monkeypatch.setattr(a,'install_pem',lambda p,name,pem:{'name':name})
    result=jobs.renew(p)
    assert result['checked']==2 and result['renewed']==[{'name':'working'}]
    assert result['errors'][0]['name']=='failed' and 'secret-like' not in result['errors'][0]['error']
    jobs.scheduled_check(p)
    state=jobs.schedule_public(p)
    assert state['last_error'] and state['last_attempt'] and state['last_success'] is None


def test_renewal_policy_and_manual_pem_cancel_previous_job(lego_profile):
    p=lego_profile;jobs.save_entries(p,{'prod':{'name':'cert','staging':False},'staging':{'name':'cert','staging':True}})
    jobs.policy(p,'cert',CertificatePolicyIn(automatic=False))
    assert not jobs.status(p,'cert',False)['automatic']
    jobs.forget(p,'cert')
    assert jobs.status(p,'cert',False)['engine']=='manual' and list(jobs.entries(p))==['staging']


def test_daily_schedule_uses_local_time_and_survives_restart_and_dst():
    settings=RenewalSettingsIn(schedule='daily',daily_time='03:15',timezone='Europe/Berlin')
    before=datetime(2026,10,9,0,0,tzinfo=timezone.utc)
    assert jobs.next_check(settings,now=before)==datetime(2026,10,9,1,15,tzinfo=timezone.utc)
    after=datetime(2026,10,9,7,0,tzinfo=timezone.utc)
    assert jobs.next_check(settings,now=after)<after
    checked=datetime(2026,10,9,1,16,tzinfo=timezone.utc)
    assert jobs.next_check(settings,checked.isoformat(),after)==datetime(2026,10,10,1,15,tzinfo=timezone.utc)
    assert jobs.next_check(settings,datetime(2026,10,24,1,15,tzinfo=timezone.utc).isoformat(),datetime(2026,10,25,5,tzinfo=timezone.utc))==datetime(2026,10,25,2,15,tzinfo=timezone.utc)
    assert jobs.next_check(RenewalSettingsIn(enabled=False),now=after) is None


def test_interval_schedule_and_state_survive_agent_restart(lego_profile,monkeypatch):
    p=lego_profile;calls=[];jobs.save_schedule(p,RenewalSettingsIn(interval_hours=24))
    monkeypatch.setattr(jobs,'renew',lambda p,automatic:calls.append(automatic) or {'checked':0,'renewed':[],'errors':[]})
    now=datetime.now(timezone.utc);jobs.scheduled_check(p,now);jobs.scheduled_check(p,now+timedelta(hours=1))
    assert calls==[True]
    state=jobs.schedule_public(p);assert state['last_success'] and state['interval_hours']==24
    jobs.scheduled_check(p,now+timedelta(hours=24));assert calls==[True,True]
    jobs.save_schedule(p,RenewalSettingsIn(enabled=False));jobs.scheduled_check(p,now+timedelta(days=2));assert calls==[True,True]

@pytest.mark.parametrize('value',[{'daily_time':'25:00'},{'interval_hours':0},{'timezone':'no/such/zone'}])
def test_invalid_renewal_settings(value):
    with pytest.raises(ValidationError):RenewalSettingsIn(**value)
