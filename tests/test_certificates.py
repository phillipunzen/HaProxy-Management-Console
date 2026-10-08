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
