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

from backend.schemas import ManagedFrontend,ManagedBackend
from backend.haproxy_config import import_config,inventory


def test_tcp_frontend_and_backend_are_generated_as_a_pair():
    doc=Document(frontends=[ManagedFrontend(name='fe_mysql',mode='tcp',port=3306,backend='be_mysql')],backends=[ManagedBackend(name='be_mysql',mode='tcp',servers=[BackendServer(address='192.0.2.235',port=3306)])])
    config=generate(doc,CAP)
    assert 'frontend fe_mysql\n    mode tcp\n    option tcplog\n    bind 0.0.0.0:3306\n    default_backend be_mysql' in config
    assert 'backend be_mysql\n    mode tcp' in config and '192.0.2.235:3306' in config
    meta={p['name']:p for p in inventory(config)}
    assert meta['fe_mysql']['routes']==[{'backend':'be_mysql','default':True}]


def test_custom_http_frontend_automatically_routes_host_to_its_pool():
    doc=Document(frontends=[ManagedFrontend(name='fe_apps',mode='http',port=8081)],hosts=[host(frontend='fe_apps')])
    config=generate(doc,CAP)
    section=config.split('frontend fe_apps')[1]
    assert 'use_backend backend_app if host_app path_app' in section
    assert config.count('backend backend_app\n')==1
    shared=config.split('frontend public_http')[1].split('backend unknown_host')[0]
    assert 'host_app' not in shared


def imported_with_tls():
    config='global\n    log /dev/log local0\ndefaults\n    mode http\n    timeout connect 5s\n    timeout client 30s\n    timeout server 30s\nfrontend fe_https\n    bind *:443 ssl crt /etc/haproxy/certs/cert.pem alpn h2,http/1.1\n    http-request set-header X-Forwarded-Proto https\n    default_backend be_old\nbackend be_old\n    server old 192.0.2.1:80 check\nfrontend mysql\n    mode tcp\n    bind *:3306\n    default_backend be_mysql\nbackend be_mysql\n    mode tcp\n    server db 192.0.2.235:3306 check\n'
    return Document.model_validate(import_config(config,'a'*64)['document'])


def test_imported_reverseproxy_addition_retains_existing_tls_and_tcp():
    doc=imported_with_tls();doc.hosts=[host(frontend='fe_https',certificate='app-cert',basic_auth_group=1)]
    config=generate(doc,CAP,{1:{'id':1,'realm':'Private','users':[]}})
    from backend.tls_bindings import read
    plan=read(config)[0]
    assert plan['fallback']==['/etc/haproxy/certs/cert.pem']
    assert plan['sites']==[{'domain':'app.example.com','certificate':'app-cert'}]
    assert 'ssl alpn h2,http/1.1 crt-list '+plan['path'] in config
    assert 'http-request set-header X-Forwarded-Proto https' in config
    assert 'server db 192.0.2.235:3306 check' in config
    assert config.index('use_backend backend_app')<config.index('default_backend be_old')
    assert 'backend backend_app' in config and "http-request auth realm 'Private'" in config


def test_explicit_frontend_certificate_selection_replaces_old_binding():
    doc=imported_with_tls();doc.frontend_certificates={'fe_https':['app-cert','other-cert']}
    config=generate(doc,CAP)
    assert 'crt /etc/haproxy/certs/cert.pem' not in config
    assert 'crt /etc/haproxy/certs/app-cert.pem crt /etc/haproxy/certs/other-cert.pem' in config
    assert 'alpn h2,http/1.1' in config


def test_directory_bind_does_not_load_site_certificate_twice():
    config=generate(Document(tls_enabled=True,hosts=[host(certificate='app-cert')]),CAP)
    from backend.tls_bindings import read
    assert 'crt-list /etc/haproxy/certs/.control-tls/' in config
    assert read(config)[0]['fallback']==['/etc/haproxy/certs/']


def test_listener_port_conflicts_and_backend_mode_mismatch_are_rejected():
    with pytest.raises(ValueError,match='Port 80'):
        generate(Document(frontends=[ManagedFrontend(name='duplicate',mode='http',port=80)]),CAP)
    with pytest.raises(ValueError,match='denselben'):
        generate(Document(frontends=[ManagedFrontend(name='tcp',mode='tcp',port=3306,backend='http')],backends=[ManagedBackend(name='http',servers=[BackendServer(address='192.0.2.1')])]),CAP)
    doc=imported_with_tls();doc.frontends=[ManagedFrontend(name='duplicate_mysql',mode='tcp',port=3306,backend='be_mysql')]
    with pytest.raises(ValueError,match='Port 3306'):generate(doc,CAP)


def test_imported_configs_can_add_pool_and_tcp_listener():
    doc=imported_with_tls();doc.backends=[ManagedBackend(name='be_redis',mode='tcp',servers=[BackendServer(address='192.0.2.11',port=6379)])];doc.frontends=[ManagedFrontend(name='fe_redis',mode='tcp',port=6379,backend='be_redis')]
    config=generate(doc,CAP)
    assert 'frontend fe_redis' in config and 'backend be_redis' in config
    assert 'bind *:443 ssl crt /etc/haproxy/certs/cert.pem' in config and 'server db 192.0.2.235:3306 check' in config


def test_imported_names_and_duplicate_site_routes_are_rejected():
    doc=imported_with_tls().model_dump();doc['frontends']=[{'name':'mysql','port':12345,'mode':'http'}]
    with pytest.raises(ValidationError,match='existiert bereits'):Document.model_validate(doc)
    with pytest.raises(ValidationError,match='einmal'):Document(hosts=[host(),Host(id='duplicate',domain='app.example.com',servers=[BackendServer(address='192.0.2.1')])])


def test_existing_crt_list_is_not_silently_discarded():
    doc=imported_with_tls();doc.imported_config=doc.imported_config.replace('crt /etc/haproxy/certs/cert.pem','crt-list /etc/haproxy/crt-list.txt');doc.frontend_certificates={'fe_https':['app-cert']}
    with pytest.raises(ValueError,match='crt-list'):generate(doc,CAP)
