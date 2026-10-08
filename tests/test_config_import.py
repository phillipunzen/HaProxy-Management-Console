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
