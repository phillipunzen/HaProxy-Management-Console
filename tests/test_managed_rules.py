"""Graphical redirects and HTTP rules preserve imported configuration and drafts."""
import base64
import hashlib
import json

import pytest
from pydantic import ValidationError
from backend import managed_rules
from backend.backend_delete import plan
from backend.generator import generate
from backend.haproxy_config import import_config,strip_document_metadata
from backend.schemas import Document,Rule,Host,ManagedFrontend
from tests.test_import_api import api

CAP={'runtime_socket_config':'/tmp/admin.sock','cert_dir_config':'/etc/certs'}
CONFIG='''defaults
 mode http
 timeout connect 2s
 timeout client 5s
 timeout server 5s
frontend incoming
 bind :8080
 http-request deny if { path_beg /private }
 http-request set-header X-Original keep
 http-request redirect scheme https code 301 if !{ ssl_fc }
 default_backend web
frontend other
 bind :8081
 default_backend web
frontend database
 mode tcp
 bind :3306
 default_backend db
backend web
 server origin 192.0.2.1:80 check inter 2s
backend db
 mode tcp
 server origin 192.0.2.2:3306 check
'''


def redirect(**values):
    return Rule.model_validate({'id':'move','name':'Move domain','match':'host','value':'a.example.com','action':'redirect','target':'https://b.example.com','preserve_path':True}|values)


def imported(config=CONFIG):return Document.model_validate(import_config(config,hashlib.sha256(config.encode()).hexdigest())['document'])


@pytest.mark.parametrize('target', ['javascript:alert(1)','https://user:password@b.example.com','https://b.example.com:invalid',
    'https://b.example.com/\nhttp-request deny','https://b.example.com/"','https://b.example.com/a;deny',
    'https://b.example.com/%[env(SECRET)]','https://b.example.com/a#fragment','https://b.example.com/$ENV'])
def test_target_rejects_invalid_urls_and_config_interpolation(target):
    with pytest.raises(ValidationError):redirect(target=target,preserve_path=False)


@pytest.mark.parametrize('target',['https://b.example.com/path','https://b.example.com/?q=1'])
def test_path_preservation_requires_an_origin_target(target):
    with pytest.raises(ValidationError,match='Pfadübernahme'):redirect(target=target)


def test_canonical_host_and_root_url_and_legacy_defaults():
    r=redirect(value='A.EXAMPLE.COM',target='https://b.example.com/')
    assert r.value=='a.example.com' and r.target=='https://b.example.com'
    assert Rule(id='legacy',name='Legacy',value='/').frontend is None
    assert Rule(id='legacy',name='Legacy',value='/').preserve_path is False
    with pytest.raises(ValidationError,match='schleife'):redirect(target='https://a.example.com')
    with pytest.raises(ValidationError):redirect(action='deny')


@pytest.mark.parametrize('source',['native','imported','custom'])
@pytest.mark.parametrize('code',[301,302,307,308])
def test_domain_redirect_generates_without_backend_and_retains_request_uri(source,code):
    doc=imported() if source=='imported' else Document()
    name='incoming' if source=='imported' else 'custom' if source=='custom' else 'public_http'
    if source=='custom':doc.frontends=[ManagedFrontend(name=name,port=8081)]
    doc.rules=[redirect(frontend=name,code=code)]
    output=generate(doc,CAP)
    assert f'http-request redirect prefix https://b.example.com code {code} if rule_move' in output
    section=output.split('frontend '+name+'\n')[1].split('\nfrontend ')[0].split('\nbackend ')[0]
    assert 'acl rule_move hdr(host),field(1,:) -i a.example.com' in section
    assert 'path_beg /.well-known/acme-challenge/' in section
    assert 'backend_move' not in output
    if source=='imported':
        assert section.index('http-request deny')<section.index('http-request redirect prefix')<section.index('http-request redirect scheme')
        other=output.split('frontend other')[1].split('frontend database')[0]
        assert 'rule_move' not in other
        assert CONFIG[CONFIG.index('backend web'):].strip() in output
    again=imported(output)
    assert again.rules==doc.rules
    assert generate(again,CAP).count('http-request redirect prefix https://b.example.com')==1


@pytest.mark.parametrize('code',[301,302,307,308])
def test_fixed_url_is_literal_including_percent_encoding(code):
    doc=Document(rules=[redirect(target='https://b.example.com/a%20b?q=%2F',preserve_path=False,code=code)])
    output=generate(doc,CAP)
    assert f'redirect location https://b.example.com/a%%20b?q=%%2F code {code}' in output
    assert imported(output).rules==doc.rules


@pytest.mark.parametrize('change',['comment','target'])
def test_changed_external_config_recovers_editable_rules_without_duplicate_blocks(change):
    doc=imported();doc.rules=[redirect(frontend='incoming')]
    output=generate(doc,CAP)
    changed=output+'# external comment\n' if change=='comment' else output.replace('192.0.2.1:80','192.0.2.3:8080')
    restored=imported(changed)
    assert restored.rules==doc.rules
    regenerated=generate(restored,CAP)
    assert regenerated.count('http-request redirect prefix https://b.example.com')==1
    assert ('# external comment' in regenerated) if change=='comment' else ('192.0.2.3:8080' in regenerated)
    restored.rules=[]
    cleared=generate(restored,CAP)
    assert managed_rules.PREFIX not in cleared and 'rule_move' not in cleared
    assert 'http-request redirect scheme https' in cleared


@pytest.mark.parametrize('enabled',[False,True])
def test_disabled_rule_and_legacy_default_frontend_survive_conservative_import(enabled):
    original=Document(rules=[redirect(enabled=enabled)])
    output=strip_document_metadata(generate(original,CAP))
    restored=imported(output)
    assert restored.rules==original.rules
    assert ('rule_move' in output)==enabled
    assert generate(restored,CAP).count('http-request redirect prefix')==int(enabled)


def test_wildcard_excludes_its_own_destination_to_prevent_loops():
    doc=Document(rules=[redirect(value='*.example.com',target='https://b.example.com')])
    output=generate(doc,CAP)
    assert 'hdr(host),field(1,:) -m end -i .example.com' in output
    assert '!{ hdr(host),field(1,:) -i b.example.com }' in output


@pytest.mark.parametrize('frontend',['database','missing'])
def test_enabled_rules_need_an_existing_http_frontend(frontend):
    doc=imported();doc.rules=[redirect(frontend=frontend)]
    with pytest.raises(ValueError,match='HTTP-/HTTPS-Frontend'):generate(doc,CAP)


def test_original_acl_name_collision_is_rejected():
    doc=imported(CONFIG.replace(' bind :8080',' bind :8080\n acl rule_move path_beg /legacy'))
    doc.rules=[redirect(frontend='incoming')]
    with pytest.raises(ValueError,match='ACL-Namenskonflikt'):generate(doc,CAP)


@pytest.mark.parametrize('change',['directive','missing_meta','metadata','scope'])
def test_altered_managed_blocks_are_rejected_before_import_and_recall(change):
    doc=imported();doc.rules=[redirect(frontend='incoming')]
    output=generate(doc,CAP)
    if change=='directive':output=output.replace('redirect prefix https://b.example.com','redirect prefix https://changed.example.com')
    elif change=='missing_meta':output=''.join(line for line in output.splitlines(keepends=True) if not line.startswith(managed_rules.META))
    elif change=='scope':output=output.replace('bind :8080','bind :8080\nfrontend misplaced\n mode http')
    else:
        value=next(line[len(managed_rules.META):] for line in output.splitlines() if line.startswith(managed_rules.META))
        data=json.loads(base64.b64decode(value));data['rules'][0]['value']='a.example.com\nhttp-request deny';data['digest']=managed_rules.digest(data['rules'])
        output=output.replace(value,base64.b64encode(json.dumps(data).encode()).decode())
    with pytest.raises(ValueError,match='Regel'):imported(output)


def test_deleting_unrelated_pool_preserves_rules_but_deleted_listen_removes_its_rules():
    doc=imported();doc.rules=[redirect(frontend='incoming',target='https://b.example.com/db',preserve_path=False)]
    doc=imported(strip_document_metadata(generate(doc,CAP)))
    changed,_=plan(doc,'db')
    output=generate(changed,CAP)
    assert 'backend db\n' not in output and 'redirect location https://b.example.com/db' in output
    listen=CONFIG.replace('frontend incoming','listen incoming').replace(' bind :8080',' bind :8080\n server local 192.0.2.7:80')
    doc=imported(listen);doc.rules=[redirect(frontend='incoming'),redirect(id='second',frontend='other')]
    doc=imported(strip_document_metadata(generate(doc,CAP)))
    changed,_=plan(doc,'incoming')
    assert [r.frontend for r in changed.rules]==['other']
    assert generate(changed,CAP).count('http-request redirect prefix')==1


def test_existing_acme_and_ip_guards_take_precedence():
    doc=Document(acme_enabled=True,rules=[redirect()],hosts=[Host(id='app',domain='a.example.com',access_policy={'networks':['192.0.2.0/24']},servers=[{'address':'192.0.2.1'}])])
    output=generate(doc,CAP)
    assert output.index('http-request deny deny_status 403')<output.index('http-request redirect prefix')
    assert 'if rule_move !acme_challenge' in output


def test_api_saves_imported_redirects_versioned_without_touching_active_config(api,monkeypatch):
    from backend.db import Instance
    from backend import main
    c,factory,state=api
    doc=imported()
    with factory() as db:db.get(Instance,1).document=doc.model_dump();db.commit()
    doc.rules=[redirect(frontend='incoming')]
    saved=c.put('/api/instances/1/document',json=doc.model_dump())
    assert saved.status_code==200,saved.text
    assert saved.json()['version']==1 and saved.json()['rules'][0]['preserve_path'] is True
    stale=c.put('/api/instances/1/document',json=doc.model_dump());assert stale.status_code==409
    with factory() as db:
        assert db.get(Instance,1).document['imported_config']==CONFIG
        assert db.get(Instance,2).document_version==0
    assert state['calls']==[]
    calls=[]
    def agent(instance,path='',**kwargs):
        calls.append(path)
        return CAP if path=='' else {'config':CONFIG,'hash':doc.imported_active_hash}
    monkeypatch.setattr(main,'agent',agent)
    generated=c.post('/api/instances/1/generate',json={});assert generated.status_code==200,generated.text
    assert 'redirect prefix https://b.example.com' in generated.json()['config']
    assert generated.json()['document_version']==1 and calls==['','/config']
    with factory() as db:assert db.get(Instance,1).document_version==1
