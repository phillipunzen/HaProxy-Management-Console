import pytest
from pydantic import ValidationError
from backend.schemas import Document,Host,Rule,CertificateIn,InstanceIn,BackendServer
from backend.generator import generate
CAP={'runtime_socket_config':'/run/haproxy/admin.sock','cert_dir_config':'/etc/haproxy/certs'}

def host(**kwargs):
    return Host(id='app',domain='app.example.com',servers=[BackendServer(address='10.0.0.5')],**kwargs)

def test_specific_paths_precede_catch_all_and_ipv6_is_bracketed():
    d=Document(hosts=[host(),Host(id='api',domain='app.example.com',path='/api',servers=[BackendServer(address='::1',port=9000)])])
    s=generate(d,CAP)
    assert s.index('use_backend backend_api')<s.index('use_backend backend_app')
    assert 'server srv_1 [::1]:9000' in s

@pytest.mark.parametrize('address',['host\nfrontend injected','10.0.0.1 #','$(id)','x;id'])
def test_backend_injection_rejected(address):
    with pytest.raises(ValidationError): BackendServer(address=address)

@pytest.mark.parametrize('value',['/foo\nhttp-request deny','/foo #comment','/foo;bar'])
def test_rule_injection_rejected(value):
    with pytest.raises(ValidationError): Rule(id='r',name='deny',value=value)

def test_acme_excluded_from_redirects_and_denies():
    s=generate(Document(acme_enabled=True,tls_enabled=True,hosts=[host(force_https=True)],rules=[Rule(id='deny',name='Block',value='/private')]),CAP)
    assert 'http-request deny if rule_deny !acme_challenge' in s
    assert '!{ ssl_fc } !acme_challenge' in s
    assert s.index('use_backend acme_webroot')<s.index('use_backend backend_app')

def test_http_wildcard_and_missing_tls_rejected():
    with pytest.raises(ValidationError): CertificateIn(name='test',domains=['*.example.com'],email='me@example.com',challenge='http')
    with pytest.raises(ValidationError): Document(hosts=[host(force_https=True)])

def test_http_agent_requires_explicit_opt_in():
    with pytest.raises(ValidationError): InstanceIn(name='x',agent_url='http://10.0.0.1:9101',profile='p',token='a'*40)
    assert InstanceIn(name='x',agent_url='http://10.0.0.1:9101',profile='p',token='a'*40,allow_http=True)

def test_request_header_and_tls_sni_generated():
    d=Document(hosts=[Host(id='h',domain='x.example.com',servers=[BackendServer(address='origin.example.com',port=443,tls=True)])],rules=[Rule(id='h',name='Header',value='/',action='set_header',target='X-Forwarded-Proto: https')])
    s=generate(d,CAP)
    assert 'set-header X-Forwarded-Proto https' in s
    assert 'sni str(origin.example.com) verifyhost origin.example.com' in s
