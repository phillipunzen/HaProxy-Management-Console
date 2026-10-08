"""Active graph keeps routing truthful and never writes detail history."""
import json
from types import SimpleNamespace

import pytest
from backend.topology import build, number
from backend.schemas import Document
from backend.generator import generate
from tests.test_config_import import CONFIG, EXTRA, MAPS


def graph(config=CONFIG+EXTRA,maps=MAPS,rows=None,online=True):
    return build(config,maps,{'online':online,'info':{'Uptime_sec':'123'},'rows':rows or []})


def nodes(g,kind):return [n for n in g['nodes'] if n['kind']==kind]
def by_name(g,kind,name):return next(n for n in nodes(g,kind) if n['name']==name)
def targets(g,n):return [next(t for t in g['nodes'] if t['id']==e['target']) for e in g['edges'] if e['source']==n['id']]


def test_host_maps_share_backend_without_inventing_per_site_metrics():
    g=graph();sites=[n for n in nodes(g,'route') if n.get('domain')]
    assert {n['domain'] for n in sites}=={'app.example.com','www.example.com'}
    assert all(n['metrics']=={} and not n['runtime'] for n in sites)
    assert {targets(g,n)[0]['name'] for n in sites}=={'be_app'}
    assert len([n for n in nodes(g,'backend') if n['name']=='be_app'])==1
    assert by_name(g,'backend','be_default')['local_response']
    assert by_name(g,'frontend','fe_https')['address']==':443'
    assert all(not e['measured'] for e in g['edges'] if e['source'] in {n['id'] for n in sites})


def test_tcp_targets_orphan_pools_and_terminal_services_are_distinct():
    g=graph()
    mysql=by_name(g,'frontend','mysql');tcp_route=targets(g,mysql)[0]
    pool=targets(g,tcp_route)[0];server=targets(g,pool)[0]
    assert pool['name']=='be_mysql' and server['address']=='192.0.2.10:3306' and server['mode']=='tcp'
    assert len(targets(g,by_name(g,'backend','galera')))==3
    for name in ('fe_http','fe_prometheus','stats'):
        terminal=targets(g,by_name(g,'frontend',name))[0]
        assert terminal['terminal'] and not targets(g,terminal)
    assert all(e['measured'] for e in g['edges'] if e['target'] in {n['id'] for n in nodes(g,'server')})


def test_generated_hosts_include_paths_wildcards_and_separate_targets():
    doc=Document(hosts=[{'id':'app','domain':'app.example.com','path':'/api','servers':[{'address':'2001:db8::1','port':8443,'tls':True}]},
                        {'id':'wild','domain':'*.example.com','servers':[{'address':'web.internal','port':8080}]}])
    config=generate(doc,{'runtime_socket_config':'/run/admin.sock','cert_dir_config':'/etc/certs'})
    g=graph(config,[])
    routes=[n for n in nodes(g,'route') if n.get('domain')]
    assert {(n['domain'],n['path']) for n in routes}=={('app.example.com','/api'),('*.example.com','/')}
    assert any(n['address']=='[2001:db8::1]:8443' and n['tls'] for n in nodes(g,'server'))


def test_complex_acl_does_not_misrepresent_negation_as_matching_site():
    config='''defaults
 mode http
frontend incoming
 bind :8080
 acl app hdr(host) -i app.example.com
 use_backend be_app unless app
 use_backend %[req.hdr(x-backend)]
'''+EXTRA
    g=graph(config,[])
    assert not any(n.get('domain') for n in nodes(g,'route'))
    assert any(n.get('dynamic') and not targets(g,n) for n in nodes(g,'route'))
    assert any('Dynamische Backend' in w for w in g['warnings'])


def test_partial_maps_keep_other_static_routes_and_report_missing_data():
    config=CONFIG.replace('    use_backend %[','    use_backend be_app if { path_beg /api }\n    use_backend %[')+EXTRA
    g=graph(config,[])
    assert any(n['condition'].startswith('Inline-Bedingung') for n in nodes(g,'route'))
    assert any(n.get('default') and targets(g,n)[0]['name']=='be_default' for n in nodes(g,'route'))
    assert any('Host-Map' in w for w in g['warnings'])


def test_runtime_fallback_preserves_roles_and_never_guesses_frontend_routes():
    rows=[{'pxname':'in','svname':'FRONTEND','type':'0','mode':'http','req_rate':'12','status':'OPEN'},
          {'pxname':'out','svname':'BACKEND','type':'1','mode':'http','rate':'5','status':'UP'},
          {'pxname':'out','svname':'web','type':'2','mode':'http','addr':'192.0.2.9:8080','rate':'3','status':'UP'}]
    g=graph(None,[],rows)
    assert len(g['edges'])==1 and not nodes(g,'route') and g['warnings']
    assert by_name(g,'frontend','in')['metrics']['http_rate']==12
    assert by_name(g,'server','web')['metrics']['session_rate']==3


def test_runtime_adds_dynamic_servers_and_current_addresses():
    config='''defaults
 mode tcp
listen db
 bind :3306
 server-template db 1-3 _mysql._tcp.example.com resolvers dns check
 server fixed db.example.com:3306 check
'''
    rows=[{'pxname':'db','svname':'db1','type':'2','addr':'192.0.2.1:3306','status':'DOWN','rate':'0','scur':'0'},
          {'pxname':'db','svname':'fixed','type':'2','addr':'192.0.2.2:3306','status':'UP','rate':'1'}]
    g=graph(config,[],rows)
    assert len(targets(g,by_name(g,'backend','db')))==2
    assert by_name(g,'server','fixed')['configured_address']=='db.example.com:3306'
    assert by_name(g,'server','fixed')['address']=='192.0.2.2:3306'
    assert by_name(g,'server','db1')['status']=='DOWN'
    assert targets(g,targets(g,by_name(g,'frontend','db'))[0])[0]['kind']=='backend'


def test_graph_does_not_expose_config_secrets_or_arbitrary_runtime_fields():
    config=(CONFIG+EXTRA).replace('    use_backend %[','    use_backend be_app if { hdr(x-api-key) -m str secret-inline-token }\n    use_backend %[')
    g=graph(config,rows=[{'pxname':'be_app','svname':'WEB01','type':'2','status':'UP','bin':str(2**63+7),'unused':'secret-runtime-field'}])
    encoded=json.dumps(g)
    assert 'example-placeholder' not in encoded and 'certs/example.pem' not in encoded and 'secret-runtime-field' not in encoded and 'secret-inline-token' not in encoded
    assert by_name(g,'server','WEB01')['metrics']['bytes_in']==2**63+7
    assert g['uptime_seconds']==123
    assert graph(online=False)['online'] is False


@pytest.mark.parametrize('invalid',[None,'',-1,'invalid',float('nan'),float('inf'),True])
def test_invalid_runtime_numbers_are_unknown(invalid):assert number(invalid) is None


def test_missing_backend_is_visible_without_fake_runtime():
    g=graph(CONFIG,MAPS)
    assert by_name(g,'backend','be_app')['missing'] and not by_name(g,'backend','be_app')['runtime']


def test_topology_endpoint_is_read_only_viewer_accessible_and_config_cache_bounded(monkeypatch):
    # Share the isolated SQLite fixture setup; never use the deployment DB.
    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine,select,func
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from backend import main
    from backend.db import Base,Instance,Metric,MetricBucket,MetricLatest
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine);factory=sessionmaker(engine,expire_on_commit=False)
    with factory() as db:
        i=Instance(name='Test',agent_url='http://192.0.2.1:9101',profile='lab',token_cipher='unused',document={});db.add(i);db.commit();id=i.id
    def get_db():
        with factory() as db:yield db
    main.app.dependency_overrides[main.get_db]=get_db
    calls=[]
    def fake_agent(i,path='',**kw):
        calls.append(path)
        if path=='/stats':return {'online':True,'rows':[]}
        if path=='/config-bundle':return {'config':CONFIG+EXTRA,'maps':MAPS}
        raise HTTPException(502,'unused')
    monkeypatch.setattr(main,'agent',fake_agent);monkeypatch.setattr(main,'topology_cache',{})
    try:
        client=TestClient(main.app)
        assert client.get(f'/api/instances/{id}/topology').status_code==401
        main.app.dependency_overrides[main.current_user]=lambda:SimpleNamespace(id=1,username='test',role='viewer')
        for _ in range(12):assert client.get(f'/api/instances/{id}/topology').status_code==200
        assert calls.count('/stats')==12 and calls.count('/config-bundle')==1
        with factory() as db:
            for model in (Metric,MetricBucket,MetricLatest):assert db.scalar(select(func.count()).select_from(model))==0
            db.get(Instance,id).profile='changed';db.commit()
        assert client.get(f'/api/instances/{id}/topology').status_code==200
        assert calls.count('/config-bundle')==2
        main.topology_cache.update({k:(None,0,None,[]) for k in range(2,129)})
        main.topology_cache[id]=(None,0,None,[])
        assert client.get(f'/api/instances/{id}/topology').status_code==200 and len(main.topology_cache)<=128
        assert client.get('/api/instances/9999/topology').status_code==404
    finally:main.app.dependency_overrides.clear();engine.dispose()
