"""Runtime health must never certify unapplied drafts or grow metrics tables."""
from types import SimpleNamespace

import pytest
from backend.generator import generate
from backend.haproxy_config import import_config
from backend.proxy_health import build, pool_health, target
from backend.schemas import Document
from tests.test_config_import import CONFIG, EXTRA, MAPS

CAP={'runtime_socket_config':'/run/admin.sock','cert_dir_config':'/etc/certs'}

def document():
    return Document(hosts=[{'id':'app','domain':'app.example.com','aliases':['www.example.com','*.apps.example.com'],
                           'path':'/api','servers':[{'address':'127.0.0.1','port':8080},{'address':'web.internal','port':8443,'tls':True}]}])


def runtime(statuses=('UP','UP')):
    return {'online':True,'rows':[{'pxname':'public_http','svname':'FRONTEND','type':'0','status':'OPEN'}]+
            [{'pxname':'backend_app','svname':f'srv_{n+1}','type':'2','status':status,'weight':'1',
              'check_status':'L4OK' if status=='UP' else 'L4CON','check_desc':'Connection refused' if status=='DOWN' else 'Layer4 check passed',
              'check_duration':'2','check_code':'0'} for n,status in enumerate(statuses)]}


def health(doc=None, config=None, data=None):
    doc=doc or document()
    return build(doc,generate(document(),CAP) if config is None else config,[],runtime() if data is None else data)['hosts']['app']


@pytest.mark.parametrize('statuses,state,available',[(('UP','UP'),'up',2),(('UP','DOWN'),'partial',1),(('DOWN','DOWN'),'down',0),
                                                   (('UP 1/2','DOWN'),'partial',1),(('DOWN 1/2','DOWN'),'down',0)])
def test_native_alias_path_host_health(statuses,state,available):
    result=health(data=runtime(statuses))
    assert result['state']==state and result['available']==available and result['total']==2
    assert result['targets'][1]['check_duration_ms']==2


@pytest.mark.parametrize('change',['domain','alias','remove_alias','path','frontend','address','port','tls','verify','weight','target_count'])
def test_unapplied_changes_do_not_show_old_target_health(change):
    doc=document();host=doc.hosts[0]
    if change=='domain':host.domain='other.example.com'
    elif change=='alias':host.aliases.append('new.example.com')
    elif change=='remove_alias':host.aliases.pop()
    elif change=='path':host.path='/different'
    elif change=='frontend':host.frontend='other'
    elif change=='target_count':host.servers.pop()
    else:setattr(host.servers[1],{'verify':'tls_verify'}.get(change,change),{
        'address':'other.internal','port':443,'tls':False,'verify':False,'weight':5}[change])
    assert health(doc)['state']=='pending'


def test_disabled_and_pending_deactivation_are_distinct():
    doc=document();doc.hosts[0].enabled=False
    assert health(doc)['state']=='pending'
    doc.hosts[0].domain='changed.example.com';doc.hosts[0].path='/changed'
    assert health(doc)['state']=='pending'
    assert health(doc,generate(doc,CAP))['state']=='disabled'


def test_missing_offline_and_initial_checks_are_unknown_not_down():
    assert health(data={'online':False,'rows':[]})['state']=='unknown'
    assert health(data={'online':True,'rows':[]})['state']=='unknown'
    data=runtime();data['rows'].pop()
    assert health(data=data)['state']=='partial'
    data=runtime();data['rows'][1]['check_status']='INI';data['rows'][2]['check_status']='START'
    assert health(data=data)['state']=='unknown'
    assert build(document(),None,[],runtime())['hosts']['app']['state']=='unknown'


def test_no_check_is_not_a_successful_check():
    data=runtime()
    for row in data['rows'][1:]:row['check_status']='';row['check_duration']='';row['check_desc']=''
    assert health(data=data)['state']=='unchecked'


@pytest.mark.parametrize('status',['MAINT','MAINT (via be/other)','DRAIN','NOLB','UP'])
def test_maintenance_drain_and_zero_weight_are_not_reported_as_down(status):
    row={'status':status,'check_status':'L7OK','weight':'0'}
    result=pool_health([target(row,'web')])
    assert result['state']=='maintenance'
    assert result['available']==0


def test_stopped_frontend_is_not_reported_as_healthy_service():
    data=runtime();data['rows'][0]['status']='STOP'
    assert health(data=data)['state']=='frontend_down'


def test_maps_share_health_and_edited_routes_targets_are_pending():
    doc=Document.model_validate(import_config(CONFIG+EXTRA,'a'*64,MAPS)['document'])
    data={'online':True,'rows':[{'pxname':'fe_https','svname':'FRONTEND','type':'0','status':'OPEN'},
                              {'pxname':'be_app','svname':'WEB01','type':'2','status':'UP','weight':'1','check_status':'L7OK','check_code':'200'}]}
    result=build(doc,CONFIG+EXTRA,MAPS,data)
    assert len(result['routes'])==2
    assert all(h['state']=='up' and h['targets'][0]['check_code']==200 for h in result['routes'].values())
    doc.imported_routes[0].domain='new.example.com'
    assert build(doc,CONFIG+EXTRA,MAPS,data)['routes'][doc.imported_routes[0].id]['state']=='pending'
    next(b for b in doc.imported_backends if b.name=='be_app').servers[0].port+=1
    assert all(h['state']=='pending' for h in build(doc,CONFIG+EXTRA,MAPS,data)['routes'].values())
    assert all(h['state']=='unknown' for h in build(doc,CONFIG+EXTRA,[],data)['routes'].values())


def test_dynamic_targets_backups_and_local_responses():
    config='''defaults
 mode http
frontend incoming
 bind :8080
 acl app hdr(host) -i app.example.com
 acl local hdr(host) -i local.example.com
 use_backend web if app
 use_backend local if local
backend web
 server-template dyn 1-2 web.internal:8080 check
 server backup 127.0.0.1:8080 check backup
backend local
 http-request return status 200
'''
    doc=Document(imported_config=config,imported_active_hash='a'*64,imported_routes=[{'id':'app','domain':'app.example.com','frontend':'incoming','backend':'web'},
                                                      {'id':'local','domain':'local.example.com','frontend':'incoming','backend':'local'}])
    data={'online':True,'rows':[{'pxname':'incoming','svname':'FRONTEND','type':'0','status':'OPEN'},
                              {'pxname':'web','svname':'dyn1','type':'2','status':'DOWN','check_status':'L4CON'},
                              {'pxname':'web','svname':'backup','type':'2','status':'UP','weight':'1','check_status':'L4OK'}]}
    result=build(doc,config,[],data)
    assert result['routes']['local']['state']=='local'
    app=result['routes']['app'];assert app['state']=='partial' and app['total']==2
    assert next(s for s in app['targets'] if s['name']=='backup')['backup']


def test_detail_whitelist_avoids_exposing_arbitrary_runtime_fields():
    row={'status':'UP','check_status':'L7OK','unrelated':'secret','last_chk':'sensitive custom debug payload'}
    assert 'secret' not in str(target(row,'web')) and 'sensitive' not in str(target(row,'web'))


def test_endpoint_is_authenticated_read_only_and_compatible_with_old_agent(monkeypatch):
    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine,select,func
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from backend import main
    from backend.db import Base,Instance,Metric,MetricBucket,MetricLatest,Audit
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine);factory=sessionmaker(engine,expire_on_commit=False)
    doc=document();original=doc.model_dump();calls=[];old_agent=False;offline=False;missing=False
    with factory() as db:
        i=Instance(name='Health',agent_url='http://192.0.2.1:9101',profile='lab',token_cipher='unused',document=original,document_version=7);db.add(i);db.commit();id=i.id
    def get_db():
        with factory() as db:yield db
    def fake_agent(i,path='',**kw):
        calls.append(path)
        if offline:raise HTTPException(502,'Unavailable')
        if path=='/stats':return runtime()
        if path=='/config-bundle':
            if old_agent:raise HTTPException(404,'Old agent')
            if missing:raise HTTPException(403,'Cannot read')
            return {'config':generate(doc,CAP),'maps':[]}
        if path=='/config':return {'config':generate(doc,CAP)}
        raise AssertionError(path)
    main.app.dependency_overrides[main.get_db]=get_db;monkeypatch.setattr(main,'agent',fake_agent)
    try:
        client=TestClient(main.app);path=f'/api/instances/{id}/proxy-health'
        assert client.get(path).status_code==401
        main.app.dependency_overrides[main.current_user]=lambda:SimpleNamespace(id=1,username='test',role='viewer')
        for _ in range(12):
            result=client.get(path);assert result.status_code==200
            assert result.json()['hosts']['app']['state']=='up' and result.json()['document_version']==7
        old_agent=True;assert client.get(path).json()['hosts']['app']['state']=='up';assert calls[-1]=='/config'
        old_agent=False;missing=True;assert client.get(path).json()['hosts']['app']['state']=='unknown'
        offline=True;assert client.get(path).json()['online'] is False
        assert client.get('/api/instances/9999/proxy-health').status_code==404
        with factory() as db:
            for model in (Metric,MetricBucket,MetricLatest,Audit):assert db.scalar(select(func.count()).select_from(model))==0
            i=db.get(Instance,id);assert i.document==original and i.document_version==7
    finally:main.app.dependency_overrides.clear();engine.dispose()
