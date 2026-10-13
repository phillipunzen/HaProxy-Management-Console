"""Reusable IP profiles must preserve restrictions, identity, scope and draft guards."""
import base64,json
import pytest
from backend import access_control as access
from backend.generator import generate
from backend.haproxy_config import import_config,strip_document_metadata
from backend.schemas import AccessProfile,AccessPolicy,Document,Host,ImportedRoute,ManagedFrontend
from tests.test_access_control import host,POLICY
from tests.test_backend_delete import CAP,imported,plan
from tests.test_import_api import api

PROFILE={'id':'lan','name':'Nur lokaler Zugriff','description':'LAN','networks':['192.168.10.7/24','fd12:3456::/48']}

def profiled(import_existing=False):
    doc=imported() if import_existing else Document(hosts=[host()])
    doc.access_profiles=[AccessProfile.model_validate(PROFILE)]
    item=doc.imported_routes[0] if import_existing else doc.hosts[0]
    item.access_profile='lan';item.access_paths=['/admin']
    return Document.model_validate(doc.model_dump())

@pytest.mark.parametrize('source',[False,True])
@pytest.mark.parametrize('fallback',[False,True])
def test_assignment_catalog_paths_aliases_and_stable_regeneration(source,fallback):
    doc=profiled(source);config=generate(doc,CAP);meta=access.read(config)
    assert meta['version']==2 and meta['profiles'][0]['networks']==['192.168.10.0/24','fd12:3456::/48']
    assert all(s['profile']=='lan' and s['policy']['paths']==['/admin'] for s in meta['sites'])
    raw=strip_document_metadata(config) if fallback else config
    restored=Document.model_validate(import_config(raw,'b'*64)['document'])
    entries=restored.imported_routes if source else restored.hosts
    assert restored.access_profiles==doc.access_profiles
    assert entries[0].access_profile=='lan' and entries[0].access_paths==['/admin'] and entries[0].access_policy is None
    assert access.read(generate(restored,CAP))['sites']==meta['sites']
    if not fallback:assert generate(restored,CAP)==config


def test_profile_update_changes_all_assignments_without_mutating_individual_paths():
    doc=profiled();doc.hosts.append(Host(id='second',domain='other.example.com',servers=[{'address':'127.0.0.1'}],access_profile='lan'))
    doc.access_profiles[0].networks=['10.8.0.0/24']
    sites=access.read(generate(doc,CAP))['sites']
    assert len(sites)==3 and all(s['policy']['networks']==['10.8.0.0/24'] for s in sites)
    assert next(s for s in sites if s['domain']=='other.example.com')['policy']['paths']==[]
    assert next(s for s in sites if s['domain']=='app.example.com')['policy']['paths']==['/admin']
    assert doc.hosts[0].access_profile=='lan'


def test_unused_catalog_and_disabled_assignments_survive_normal_reimport():
    doc=profiled();doc.hosts[0].enabled=False;output=generate(doc,CAP)
    assert access.read(output)['sites']==[] and 'deny_status 403' not in output
    restored=imported(output);assert restored.hosts[0].access_profile=='lan' and restored.access_profiles==doc.access_profiles
    doc=Document(access_profiles=[PROFILE]);output=generate(doc,CAP)
    assert imported(strip_document_metadata(output)).access_profiles==doc.access_profiles


@pytest.mark.parametrize('change',[{'access_profile':'missing'},{'access_profile':'lan','access_policy':POLICY},{'access_profile':None,'access_paths':['/admin']}])
def test_missing_conflicting_or_detached_profile_paths_rejected(change):
    doc=profiled().model_dump();doc['hosts'][0].update(change)
    with pytest.raises(ValueError):Document.model_validate(doc)


def test_deleting_used_profile_rejected_even_for_disabled_hosts():
    doc=profiled();doc.hosts[0].enabled=False;value=doc.model_dump();value['access_profiles']=[]
    with pytest.raises(ValueError,match='Zuordnung'):Document.model_validate(value)


@pytest.mark.parametrize('profiles',[[PROFILE,PROFILE],[PROFILE,{**PROFILE,'id':'other','name':'nur lokaler zugriff'}]])
def test_duplicate_ids_and_case_insensitive_names_rejected(profiles):
    with pytest.raises(ValueError,match='eindeutig'):Document(access_profiles=profiles)


@pytest.mark.parametrize('networks',[[''],['example.com'],['10.1.2.3\nhttp-request allow'],['::1%lo']])
def test_profile_cannot_inject_invalid_networks(networks):
    with pytest.raises(ValueError):AccessProfile(**{**PROFILE,'networks':networks})


def test_converting_to_custom_policy_and_clearing_does_not_restore_old_assignment():
    doc=imported(strip_document_metadata(generate(profiled(True),CAP)))
    entry=doc.imported_routes[0];entry.access_profile=None;entry.access_paths=[];entry.access_policy=AccessPolicy(networks=['192.0.2.1'])
    meta=access.read(generate(doc,CAP));assert all(s.get('profile') is None and s['policy']['networks']==['192.0.2.1'] for s in meta['sites'])
    entry.access_policy=None;doc.access_profiles=[];output=generate(doc,CAP)
    assert access.read(output) is None and 'deny_status 403' not in output


def test_backend_deletion_keeps_unused_profile_without_resurrecting_guard():
    doc=imported(strip_document_metadata(generate(profiled(True),CAP)))
    changed,_=plan(doc,'web');output=generate(changed,CAP)
    assert access.read(output)['sites']==[] and access.read(output)['profiles']
    assert 'deny_status 403' not in output


def test_profiles_with_equal_networks_remain_separate_on_alias_reimport():
    doc=profiled(True);doc.access_profiles.append(AccessProfile(**{**PROFILE,'id':'office','name':'Office'}));r=doc.imported_routes[0];r.aliases=[]
    doc.imported_routes.append(ImportedRoute(id='www',frontend=r.frontend,backend=r.backend,domain='www.example.com',access_profile='office',access_paths=['/admin']))
    config=strip_document_metadata(generate(doc,CAP));again=imported(config)
    assert {r.domain:r.access_profile for r in again.imported_routes if r.domain in ('app.example.com','www.example.com')}=={'app.example.com':'lan','www.example.com':'office'}


def test_forged_profile_networks_rejected_even_with_recomputed_metadata_digest():
    config=generate(profiled(),CAP);data=access.read(config);data['profiles'][0]['networks']=['0.0.0.0/0']
    data['digest']=access.digest({'profiles':data['profiles'],'sites':data['sites']})
    forged='\n'.join(l for l in config.splitlines() if not l.startswith(access.META))+'\n'+access.META+base64.b64encode(json.dumps(data).encode()).decode()+'\n'
    with pytest.raises(ValueError,match='metadaten'):imported(forged)


def test_profile_paths_stay_within_host_and_tcp_assignment_rejected():
    doc=profiled();doc.hosts[0].path='/api'
    with pytest.raises(ValueError,match='innerhalb'):access.validate_document(doc)
    doc.hosts[0].path='/';doc.hosts[0].frontend='stream';doc.frontends=[ManagedFrontend(name='stream',mode='tcp',port=9000)]
    doc=Document.model_validate(doc.model_dump())
    with pytest.raises(ValueError,match='HTTP-Frontend'):access.validate_document(doc)


def test_api_optimistic_version_scope_deletion_and_draft_only(api):
    from backend.db import Instance
    c,factory,state=api;value=profiled(True).model_dump()
    result=c.put('/api/instances/1/document',json=value);assert result.status_code==200,result.text
    value=result.json();assert value['imported_routes'][0]['access_profile']=='lan'
    assert c.put('/api/instances/1/document',json={**value,'access_profiles':[]}).status_code==422
    assert c.put('/api/instances/1/document',json={**value,'version':0}).status_code==409
    value['access_profiles'][0]['networks']=['10.8.0.0/24']
    result=c.put('/api/instances/1/document',json=value);assert result.status_code==200,result.text
    fetched=c.get('/api/instances/1/document').json();assert fetched['access_profiles'][0]['networks']==['10.8.0.0/24']
    with factory() as db:assert db.get(Instance,2).document_version==0 and db.get(Instance,2).document['access_profiles']==[]
    assert state['calls']==[]
