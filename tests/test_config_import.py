"""Migration preserves custom directives and protects all source files."""
import hashlib
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from backend.schemas import Document
from backend.generator import generate
from backend.haproxy_config import import_config, inventory, enrich_stats, migration_context, extract_backends
from agent import main as agent
from agent.config_bundle import read_bundle

sha=lambda value:hashlib.sha256(value.encode()).hexdigest()
CONFIG='''global
    log /dev/log local0
    stats socket /run/haproxy/admin.sock mode 660 level admin
defaults
    mode http
    timeout connect 5s
    timeout client 50s
    timeout server 50s
frontend fe_http
    bind :80
    http-request redirect scheme https code 301
frontend fe_https
    bind :443 ssl crt /etc/haproxy/certs/example.pem
    http-request set-header X-Forwarded-Proto https
    use_backend %[req.hdr(host),lower,map(/etc/haproxy/maps/vhosts.map,be_default)]
frontend fe_prometheus
    bind :8405
    http-request use-service prometheus-exporter
    no log
frontend mysql
    bind :3306
    mode tcp
    default_backend be_mysql
backend be_mysql
    balance roundrobin
    mode tcp
    server DB01 192.0.2.10:3306 check inter 2s rise 3 fall 2 # keep-check
backend galera
    balance roundrobin
    mode tcp
    server DB01 192.0.2.11:3306 check
    server DB02 192.0.2.12:3306 check
    server DB03 192.0.2.13:3306 check
listen stats
    bind :8080
    mode http
    stats enable
    stats uri /haproxy?stats
userlist example_users
    user demo insecure-password example-placeholder
'''
EXTRA='''backend be_app
    server WEB01 192.0.2.20:8080 check ssl verify none weight 20 backup
backend be_default
    http-request return status 404
'''
MAPS=[{'path':'/etc/haproxy/maps/vhosts.map','content':'app.example.com be_app\nwww.example.com be_app\n'}]

def doc(config=CONFIG,maps=None,**kw):
    return Document.model_validate(import_config(config,sha(config),maps,**kw)['document'])

def test_mixed_user_structure_is_classified_by_mode_and_service():
    values={p['name']:p for p in inventory(CONFIG)}
    assert values['mysql']['mode']=='tcp'
    assert values['galera']['service']=='TCP-Proxy (Layer 4)'
    assert values['fe_https']['service']=='HTTPS-Reverse-Proxy'
    assert values['fe_http']['service']=='HTTP-Weiterleitung'
    assert values['fe_prometheus']['service']=='Prometheus-Exporter'
    assert values['stats']['service']=='HAProxy-Statistikdienst'

@pytest.mark.parametrize('ending',['\n','\r\n'])
def test_no_edits_without_maps_preserve_entire_source(ending):
    original=CONFIG.replace('\n',ending)
    assert generate(doc(original),{})==original


def test_map_migration_preserves_rules_and_tcp_pools():
    document=doc(CONFIG+EXTRA,MAPS)
    assert document.imported_route_frontends==['fe_https']
    assert len(document.imported_routes)==2
    result=generate(document,{})
    assert 'map(/etc/haproxy/maps/vhosts.map' not in result
    assert 'hdr(host) -i app.example.com' in result
    assert 'use_backend be_default\n' in result
    assert 'http-request set-header X-Forwarded-Proto https' in result
    assert 'insecure-password example-placeholder' in result
    assert CONFIG[CONFIG.index('frontend mysql'):]==result[result.index('frontend mysql'):result.index('\nbackend be_app')+1]
    # The generated explicit routes are readable on a subsequent import.
    again=doc(result)
    assert {(r.domain,r.backend) for r in again.imported_routes}=={('app.example.com','be_app'),('www.example.com','be_app')}
    assert 'hdr(host) -i app.example.com' in generate(again,{})


def test_backend_edit_changes_only_address_weight_and_algorithm():
    document=doc(CONFIG+EXTRA)
    pool=next(b for b in document.imported_backends if b.name=='be_app')
    pool.servers[0].address='2001:db8::10';pool.servers[0].port=8443;pool.servers[0].weight=0;pool.balance='leastconn'
    result=generate(document,{})
    assert 'server WEB01 [2001:db8::10]:8443 check ssl verify none weight 0 backup' in result
    assert 'backend be_app\n    balance leastconn' in result
    assert CONFIG in result

@pytest.mark.parametrize('content',['app.example.com be_app\napp.example.com be_default\n','*.example.com be_app\n','App.example.com be_app\n','app.example.com be_app extra\n'])
def test_unsupported_map_is_preserved(content):
    document=doc(CONFIG+EXTRA,[{'path':MAPS[0]['path'],'content':content}])
    assert not document.imported_routes
    assert generate(document,{})==CONFIG+EXTRA


def test_missing_map_and_backends_are_reported_without_inventing_targets():
    result=import_config(CONFIG,sha(CONFIG))
    assert any('fehlt' in warning for warning in result['warnings'])
    result=import_config(CONFIG,sha(CONFIG),MAPS)
    assert any('be_app fehlt' in warning for warning in result['warnings'])
    assert not any(b['name']=='be_app' for b in result['document']['imported_backends'])


def test_inherited_defaults_are_used_and_reset():
    config='''defaults shared
 mode http
 balance leastconn
 default-server weight 30 ssl verify none
backend inherited
 server web example.com:443 check
frontend next from shared
 bind :8443
 default_backend inherited
defaults other
 mode tcp
backend plain
 server db 192.0.2.1:1234 check
'''
    pools,_=extract_backends(config)
    assert pools[0].balance=='leastconn' and pools[0].servers[0].weight==30 and pools[0].servers[0].tls
    assert pools[1].mode=='tcp' and pools[1].servers[0].weight==1 and not pools[1].servers[0].tls
    assert generate(doc(config),{})==config


def test_simple_acl_routes_are_imported_complex_conditions_untouched():
    config='''defaults
 mode http
frontend incoming
 bind :8080
 acl app hdr(host) -i app.example.com
 use_backend be_app if app
 acl secure_path path_beg /private
 http-request deny if secure_path
 default_backend be_default
'''+EXTRA
    document=doc(config)
    assert len(document.imported_routes)==1
    document.imported_routes[0].domain='new.example.com'
    output=generate(document,{})
    assert 'hdr(host) -i new.example.com' in output
    assert 'http-request deny if secure_path' in output
    document=doc(config.replace('if app','if app secure_path'))
    assert not document.imported_routes
    assert generate(document,{})==config.replace('if app','if app secure_path')


def test_route_duplicate_ids_and_backend_structural_edits_are_rejected():
    document=doc(CONFIG+EXTRA,MAPS)
    value=document.model_dump();value['imported_routes'][1]['id']=value['imported_routes'][0]['id']
    with pytest.raises(ValidationError):Document.model_validate(value)
    document.imported_backends[0].servers[0].name='renamed'
    with pytest.raises(ValueError,match='Servernamen'):generate(document,{})


def test_stats_use_roles_and_modes_without_guessing_from_ports():
    config=CONFIG+'\nfrontend passthrough\n bind :4443\n mode tcp\n tcp-request content accept if { req.ssl_sni -m found }\n default_backend galera\n'
    rows=[{'type':kind,'pxname':name,'svname':sv} for kind,name,sv in [('0','fe_https','FRONTEND'),('1','be_app','BACKEND'),('2','be_app','WEB01'),('0','mysql','FRONTEND'),('0','passthrough','FRONTEND'),('0','stats','FRONTEND'),('1','stats','BACKEND')]]
    data=enrich_stats({'rows':rows,'sessions':10},config+EXTRA,MAPS)
    assert data['sessions']==10
    assert [r['role'] for r in data['rows']]==['frontend','backend','server','frontend','frontend','frontend','backend']
    assert data['rows'][0]['domains']==['app.example.com','www.example.com']
    assert data['rows'][1]['frontends']==['fe_https']
    assert data['rows'][4]['service']=='TLS-Passthrough (SNI)'
    assert data['rows'][5]['proxy_kind']=='listen'
    old=enrich_stats({'rows':[{'pxname':'db','svname':'FRONTEND','mode':'tcp','addr':'*:443'}]})
    assert old['rows'][0]['mode']=='tcp' and 'HTTPS' not in old['rows'][0]['service']

@pytest.fixture
def bundle(tmp_path,monkeypatch):
    primary=tmp_path/'haproxy.cfg';extra=tmp_path/'conf.d/10-backends.cfg';extra.parent.mkdir()
    mapping=tmp_path/'maps/vhosts.map';mapping.parent.mkdir();mapping.write_text(MAPS[0]['content'])
    primary.write_text(CONFIG.replace(MAPS[0]['path'],str(mapping)));extra.write_text(EXTRA)
    p={'kind':'native','config_path':str(primary),'config_sources':[str(primary),str(extra.parent)],'service':'test','runtime_socket':str(tmp_path/'admin.sock')}
    (tmp_path/'state').mkdir();monkeypatch.setattr(agent,'STATE_DIR',tmp_path/'state')
    monkeypatch.setattr(agent,'validate',lambda *args:'valid')
    monkeypatch.setattr(agent,'info',lambda *args:{'Pid':'1'})
    return p,primary,extra,mapping


def test_bundle_reads_sorted_cfg_files_and_maps(bundle):
    p,primary,extra,mapping=bundle
    (extra.parent/'.control-check-hidden.cfg').write_text('invalid')
    data=read_bundle(p,lambda *args:'',sha)
    assert [s['path'] for s in data['sources']]==[str(primary),str(extra)]
    assert data['maps'][0]['content']==mapping.read_text()
    assert 'invalid' not in data['config']


def migration_body(p):
    data=read_bundle(p,lambda *args:'',sha)
    result=import_config(data['config'],data['hash'],data['maps'],[{'path':s['path'],'hash':s['hash']} for s in data['sources']],[{'path':m['host_path'],'hash':m['hash']} for m in data['maps']])
    generated=generate(Document.model_validate(result['document']),{})
    return agent.ConfigIn(config=generated,expected_hash=data['hash'])


def test_migration_consolidates_and_backs_up_every_source(bundle,monkeypatch):
    p,primary,extra,mapping=bundle;original=[primary.read_text(),extra.read_text()];body=migration_body(p)
    monkeypatch.setattr(agent,'reload_service',lambda *args:{'Pid':'2'})
    result=agent.apply_migration(p,body,migration_context(body.config))
    assert result['applied'] and len(result['sources'])==2
    assert 'backend be_app' in primary.read_text() and 'map(' not in primary.read_text()
    assert extra.read_text().startswith('# Consolidated')
    backup=json.loads(next(agent.STATE_DIR.rglob('bundle.json')).read_text())
    assert [s['content'] for s in backup['sources']]==original
    assert mapping.read_text()==MAPS[0]['content']


def test_failed_migration_restores_all_original_files(bundle,monkeypatch):
    p,primary,extra,mapping=bundle;old=[primary.read_bytes(),extra.read_bytes()];body=migration_body(p);calls=[]
    def reload(*args):
        calls.append([primary.read_bytes(),extra.read_bytes()])
        if len(calls)==1:raise HTTPException(502,'failure')
        return {'Pid':'2'}
    monkeypatch.setattr(agent,'reload_service',reload)
    with pytest.raises(HTTPException,match='Migration fehlgeschlagen'):agent.apply_migration(p,body,migration_context(body.config))
    assert [primary.read_bytes(),extra.read_bytes()]==old and len(calls)==2
    assert calls[-1]==old

@pytest.mark.parametrize('changed',['main','extra','map'])
def test_changed_any_file_aborts_migration_before_writes(bundle,monkeypatch,changed):
    p,primary,extra,mapping=bundle;body=migration_body(p)
    target={'main':primary,'extra':extra,'map':mapping}[changed];target.write_text(target.read_text()+'# external change\n')
    old=[primary.read_bytes(),extra.read_bytes()]
    with pytest.raises(HTTPException) as error:agent.apply_migration(p,body,migration_context(body.config))
    assert error.value.status_code==409 and [primary.read_bytes(),extra.read_bytes()]==old


def test_docker_bundle_maps_container_paths_and_command_order(bundle):
    p,primary,extra,mapping=bundle
    primary.write_text(CONFIG)
    p.update(kind='docker',container='test')
    def run(args):
        if args[3]=='{{json .Mounts}}':return json.dumps([{'Source':str(primary.parent),'Destination':'/etc/haproxy'}])
        return json.dumps(['haproxy','-W','-f','/etc/haproxy/haproxy.cfg','-f','/etc/haproxy/conf.d'])
    p.pop('config_sources')
    data=read_bundle(p,run,sha)
    assert data['sources'][1]['container_path']=='/etc/haproxy/conf.d/10-backends.cfg'
    assert data['maps'][0]['path']==MAPS[0]['path'] and data['maps'][0]['host_path']==str(mapping)


def test_validation_uses_all_loaded_files_and_merged_candidate_once(bundle,monkeypatch):
    p,primary,extra,mapping=bundle
    monkeypatch.undo()
    p['haproxy_binary']='/fake/haproxy'
    calls=[]
    monkeypatch.setattr(agent,'run',lambda args,**kw:calls.append(args) or 'valid')
    agent.validate(p,primary.read_text())
    assert calls[-1][-2:]==['-f',str(extra)] and calls[-1].count('-f')==2
    body=migration_body(p)
    agent.validate(p,body.config)
    assert calls[-1].count('-f')==1
    assert not list(primary.parent.glob('.control-check-*'))


def test_empty_map_can_accept_first_domain_and_unused_acl_is_preserved():
    from backend.schemas import ImportedRoute
    document=doc(CONFIG+EXTRA,[{'path':MAPS[0]['path'],'content':'# empty\n'}])
    assert document.imported_route_frontends==['fe_https'] and not document.imported_routes
    document.imported_routes.append(ImportedRoute(id='first',frontend='fe_https',domain='first.example.com',backend='be_app'))
    assert 'hdr(host) -i first.example.com' in generate(document,{})
    original='defaults\n mode http\nfrontend f\n bind :80\n acl app hdr(host) -i app.example.com\n http-request deny if app\n use_backend be_app if app\n'+EXTRA
    assert not doc(original).imported_routes
    assert generate(doc(original),{})==original


def test_multi_name_acl_imports_and_roundtrips_as_one_editable_route():
    original='defaults\n mode http\nfrontend incoming\n bind :80\n acl wiki hdr(host) -i pc-wiki.de www.pc-wiki.de\n use_backend be_app if wiki\n'+EXTRA
    document=doc(original)
    assert len(document.imported_routes)==1
    assert document.imported_routes[0].hostnames==['pc-wiki.de','www.pc-wiki.de']
    again=doc(generate(document,{}))
    assert again.imported_routes[0].hostnames==['pc-wiki.de','www.pc-wiki.de']
    assert again.imported_routes[0].backend=='be_app'


def test_multi_name_import_does_not_merge_differently_certified_domains():
    from backend import tls_bindings
    from backend.schemas import ImportedRoute
    original='defaults\n mode http\nfrontend incoming\n bind :443 ssl crt /etc/certs/default.pem\n acl wiki hdr(host) -i pc-wiki.de www.pc-wiki.de\n use_backend be_app if wiki\n'+EXTRA
    document=doc(original)
    document.imported_routes=[ImportedRoute(id='one',frontend='incoming',domain='pc-wiki.de',backend='be_app',certificate='one'),ImportedRoute(id='two',frontend='incoming',domain='www.pc-wiki.de',backend='be_app',certificate='two')]
    generated=generate(document,{'cert_dir_config':'/etc/certs'})
    # Restore one source ACL while retaining independent certificate metadata.
    import re
    generated=re.sub(r' acl mgmt_one[^\n]+\n use_backend be_app if mgmt_one\n acl mgmt_two[^\n]+\n use_backend be_app if mgmt_two', ' acl wiki hdr(host) -i pc-wiki.de www.pc-wiki.de\n use_backend be_app if wiki',generated)
    again=doc(generated)
    assert {(r.domain,r.certificate) for r in again.imported_routes}=={('pc-wiki.de','one'),('www.pc-wiki.de','two')}
    assert all(not r.aliases for r in again.imported_routes)


@pytest.mark.parametrize('global_value',['','global\n ssl-server-verify none\n'])
@pytest.mark.parametrize('default_value',['',' default-server ssl verify none\n',' default-server ssl verify required ca-file /custom/root.pem\n'])
def test_imported_verification_inheritance_and_per_server_overrides(global_value,default_value):
    original=global_value+'defaults named\n mode http\n'+default_value+'backend app from named\n server inherited 192.0.2.1:443 ssl\n server insecure 192.0.2.2:443 ssl verify none\n server secure 192.0.2.3:443 ssl verify required ca-file /custom/other.pem\n'
    document=doc(original);servers=document.imported_backends[0].servers
    inherited='verify required' in default_value or not default_value and not global_value
    assert [s.tls_verify for s in servers]==[bool(inherited),False,True]
    # Old saved JSON must inherit policy, without silently enabling verification.
    old=document.model_dump()
    for server in old['imported_backends'][0]['servers']:server.pop('tls_verify')
    restored=Document.model_validate(old)
    assert [s.tls_verify for s in restored.imported_backends[0].servers]==[bool(inherited),False,True]
    assert generate(restored,{})==original
    servers[0].tls_verify=not inherited
    updated=generate(document,{})
    line=next(line for line in updated.splitlines() if 'server inherited' in line)
    assert 'verify '+('none' if inherited else 'required') in line
    if not inherited and 'ca-file' not in default_value:assert 'ca-file /etc/ssl/certs/ca-certificates.crt' in line
    assert 'server insecure 192.0.2.2:443 ssl verify none' in updated
    assert 'server secure 192.0.2.3:443 ssl verify required ca-file /custom/other.pem' in updated
    assert doc(updated).imported_backends[0].servers[0].tls_verify is (not inherited)


def test_imported_verification_edit_preserves_ca_sni_healthchecks_comments_and_line_endings():
    original='defaults\r\n mode http\r\nbackend app\r\n server web origin.example.com:443 check ssl verify required ca-file /custom/root.pem sni str(origin.example.com) verifyhost origin.example.com inter 3s backup # preserve\r\n'
    document=doc(original);document.imported_backends[0].servers[0].tls_verify=False
    updated=generate(document,{})
    assert updated==original.replace('verify required','verify none')
    assert generate(doc(updated),{})==updated
    document=doc(updated);document.imported_backends[0].servers[0].tls_verify=True
    assert generate(document,{})==original


def test_imported_plaintext_verification_change_is_rejected():
    document=doc('defaults\n mode http\nbackend app\n server web 192.0.2.1:80 check\n')
    document.imported_backends[0].servers[0].tls_verify=False
    with pytest.raises(ValueError,match='TLS-Verbindung'):generate(document,{})


@pytest.mark.parametrize('legacy',[False,True])
@pytest.mark.parametrize('custom_frontend',[False,True])
def test_tool_hosts_keep_identity_auth_tls_aliases_and_are_editable_after_import(legacy,custom_frontend):
    from backend.schemas import Host,ManagedFrontend,ManagedBackend,Rule
    from backend.haproxy_config import strip_document_metadata,document_fingerprint,GRAPH_PREFIX
    from backend.basic_auth import hash_password,read_metadata
    from backend.tls_bindings import read as tls_plans
    cap={'runtime_socket_config':'/run/haproxy/admin.sock','cert_dir_config':'/etc/haproxy/certs'}
    groups={1:{'id':1,'realm':'Private','users':[{'username':'alice','hash':hash_password('secret')}]}}
    base=doc(CONFIG+EXTRA,MAPS) if custom_frontend else Document(tls_enabled=True,acme_enabled=True)
    frontend='fe_https' if custom_frontend else 'public_http'
    base.hosts=[Host(id='wiki',frontend=frontend,domain='pc-wiki.de',aliases=['www.pc-wiki.de','*.wiki.example.com'],path='/wiki',certificate='wiki',basic_auth_group=1,force_https=True,servers=[{'address':'origin.example.com','port':443,'tls':True,'tls_verify':False}]),Host(id='disabled',frontend=frontend,domain='disabled.example.com',enabled=False,servers=[{'address':'192.0.2.1'}])]
    base.frontends=[ManagedFrontend(name='new_mysql',mode='tcp',port=3307,backend='new_database')]
    base.backends=[ManagedBackend(name='new_database',mode='tcp',servers=[{'address':'192.0.2.50','port':3306}])]
    if not custom_frontend:base.rules=[Rule(id='deny',name='Deny',value='/blocked')]
    config=generate(base,cap,groups);value=strip_document_metadata(config) if legacy else config
    result=import_config(value,hashlib.sha256(value.encode()).hexdigest(),MAPS if custom_frontend else [])
    again=Document.model_validate(result['document'])
    host=next(h for h in again.hosts if h.id=='wiki')
    assert host.model_dump()==base.hosts[0].model_dump()
    assert result['summary']['managed_hosts']==(1 if legacy else 2)
    assert not any(b.name=='backend_wiki' for b in again.imported_backends)
    regenerated=generate(again,cap,groups)
    assert regenerated.splitlines().count('backend backend_wiki')==1
    assert regenerated.count('use_backend backend_wiki if host_wiki path_wiki')==1
    assert read_metadata(regenerated)['sites']==read_metadata(config)['sites']
    assert {s['domain'] for p in tls_plans(regenerated) for s in p['sites']}==set(host.hostnames)
    if not legacy:
        assert again.hosts==base.hosts and again.frontends==base.frontends and again.backends==base.backends and again.rules==base.rules
        assert document_fingerprint(regenerated)==document_fingerprint(config)
    host.servers[0].port=8443;host.aliases=['wiki.phlene.de'];host.basic_auth_group=None;host.certificate=None
    modified=generate(again,cap,groups)
    assert 'origin.example.com:8443' in modified and 'hdr(host),field(1,:) -i pc-wiki.de wiki.phlene.de' in modified
    assert not read_metadata(modified) and not tls_plans(modified)
    again.hosts=[]
    cleared=generate(again,cap,groups)
    assert 'backend backend_wiki' not in cleared and not read_metadata(cleared) and not tls_plans(cleared)
    assert len([line for line in modified.splitlines() if line.startswith(GRAPH_PREFIX)])==1
    assert len(modified)<len(config)*2


@pytest.mark.parametrize('change',['foreign_backend','own_backend','comment','rule'])
def test_changed_config_uses_live_values_instead_of_saved_document(change):
    from backend.schemas import Host
    cap={'runtime_socket_config':'/run/admin.sock','cert_dir_config':'/etc/haproxy/certs'}
    base=doc(CONFIG+EXTRA,MAPS);base.hosts=[Host(id='wiki',frontend='fe_https',domain='pc-wiki.de',servers=[{'address':'192.0.2.70'}])]
    original=generate(base,cap)
    if change=='foreign_backend':value=original.replace('192.0.2.20:8080','192.0.2.99:8443')
    elif change=='own_backend':value=original.replace('192.0.2.70:80','192.0.2.71:8081')
    elif change=='comment':value=original+'# user note\n'
    else:value=original.replace('    balance roundrobin\n    server srv_1 192.0.2.70','    http-request set-header X-Extra retained\n    balance roundrobin\n    server srv_1 192.0.2.70')
    result=import_config(value,hashlib.sha256(value.encode()).hexdigest(),MAPS)
    assert result['warnings'] and not result['summary']['restored_document']
    again=Document.model_validate(result['document']);regenerated=generate(again,cap)
    if change=='foreign_backend':assert '192.0.2.99:8443' in regenerated and again.hosts[0].id=='wiki'
    if change=='own_backend':assert again.hosts[0].servers[0].address=='192.0.2.71' and '192.0.2.71:8081' in regenerated
    if change=='comment':assert '# user note' in regenerated and again.hosts[0].id=='wiki'
    if change=='rule':assert not again.hosts and 'http-request set-header X-Extra retained' in regenerated


def test_document_metadata_handles_multifile_stubs_and_rejects_malformed_or_oversized_data():
    import base64,zlib
    from backend.schemas import Host
    from backend.haproxy_config import GRAPH_PREFIX,strip_document_metadata
    cap={'runtime_socket_config':'/run/admin.sock','cert_dir_config':'/etc/certs'}
    config=generate(Document(hosts=[Host(id='wiki',domain='pc-wiki.de',servers=[{'address':'192.0.2.1'}])]),cap)
    bundle=config.rstrip('\n')+'\n\n# Consolidated into /etc/haproxy/haproxy.cfg by HAProxy Control\n\n'
    result=import_config(bundle,hashlib.sha256(config.encode()).hexdigest())
    assert result['summary']['restored_document'] and result['document']['hosts'][0]['id']=='wiki'
    for payload in ['invalid',base64.b64encode(zlib.compress(b'['*2000+b'0'+b']'*2000)).decode(),base64.b64encode(zlib.compress(b'x'*(4*1024*1024+1))).decode()]:
        broken=strip_document_metadata(config)+GRAPH_PREFIX+payload+'\n'
        result=import_config(broken,hashlib.sha256(broken.encode()).hexdigest())
        assert result['warnings'] and result['document']['hosts'][0]['id']=='wiki'


def test_generated_names_with_extra_references_or_options_remain_imported():
    from backend.schemas import Host
    from backend.haproxy_config import strip_document_metadata
    cap={'runtime_socket_config':'/run/admin.sock','cert_dir_config':'/etc/certs'}
    config=strip_document_metadata(generate(Document(hosts=[Host(id='wiki',domain='pc-wiki.de',servers=[{'address':'192.0.2.1'}])]),cap))
    for modified in [config.replace('    use_backend backend_wiki','    http-request deny if host_wiki\n    use_backend backend_wiki'),config.replace(' weight 100 check',' weight 100 check inter 3s'),config.replace('    default_backend unknown_host','    use_backend %[req.hdr(host),lower,map(/etc/haproxy/other.map,backend_wiki)]\n    default_backend unknown_host')]:
        result=import_config(modified,hashlib.sha256(modified.encode()).hexdigest())
        assert not result['document']['hosts']
        assert any(b['name']=='backend_wiki' for b in result['document']['imported_backends'])
