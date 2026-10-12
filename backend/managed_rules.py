"""Scoped graphical HTTP rules with verifiable blocks and import recovery."""
import base64
import hashlib
import json
import re
from urllib.parse import urlsplit

from backend.schemas import Rule
from backend.haproxy_config import parse_sections

PREFIX='# haproxy-control-rules '
META=PREFIX+'v1 '


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def statements(rules,frontend,acme=False):
    result=[]
    for rule in rules:
        if not rule.enabled or (rule.frontend or 'public_http')!=frontend:continue
        criterion={'host':'hdr(host),field(1,:) -i','path_prefix':'path_beg','source_ip':'src','method':'method'}[rule.match]
        value=rule.value
        if rule.match=='host' and value.startswith('*.'):criterion='hdr(host),field(1,:) -m end -i';value=value[1:]
        result.append(f'    acl rule_{rule.id} {criterion} {value}')
        condition=f'rule_{rule.id}'
        if acme:condition+=' !acme_challenge'
        elif rule.action=='redirect':condition+=' !{ path_beg /.well-known/acme-challenge/ }'
        if rule.action=='redirect' and rule.match=='host' and rule.value.startswith('*.') and urlsplit(rule.target).hostname.lower().endswith(rule.value[1:]):
            condition+=' !{ hdr(host),field(1,:) -i '+urlsplit(rule.target).hostname.lower()+' }'
        if rule.action=='deny':result.append(f'    http-request deny if {condition}')
        elif rule.action=='redirect':
            target=rule.target.replace('%','%%')
            result.append(f'    http-request redirect {"prefix" if rule.preserve_path else "location"} {target} code {rule.code} if {condition}')
        else:
            name,value=rule.target.split(':',1)
            result.append(f'    http-request set-header {name} {value.strip()} if {condition}')
    return result


def read_and_strip(config):
    lines=config.splitlines(keepends=True)
    values=[line[len(META):].strip() for line in lines if line.startswith(META)]
    if not values:
        if any(line.startswith(PREFIX) for line in lines):raise ValueError('Regel-Metadaten fehlen. Markierte Regeln im Texteditor prüfen.')
        return None,config
    try:
        if len(values)!=1 or len(values[0])>1024*1024:raise ValueError()
        data=json.loads(base64.b64decode(values[0],validate=True))
        if not isinstance(data,dict) or set(data)!={'version','rules','acme','blocks','digest'} or data['version']!=1:raise ValueError()
        if not isinstance(data['rules'],list) or not 1<=len(data['rules'])<=200 or digest(data['rules'])!=data['digest']:raise ValueError()
        rules=[Rule.model_validate(value) for value in data['rules']]
        if [r.model_dump() for r in rules]!=data['rules'] or len({r.id for r in rules})!=len(rules):raise ValueError()
        if not isinstance(data['acme'],list) or data['acme'] not in ([],['public_http']):raise ValueError()
        if not isinstance(data['blocks'],dict) or len(data['blocks'])>200:raise ValueError()
        for name,value in data['blocks'].items():
            if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}',name) or not re.fullmatch(r'[a-f0-9]{64}',value):raise ValueError()
        expected={name:'\n'.join(statements(rules,name,name in data['acme']))+'\n' for name in {(r.frontend or 'public_http') for r in rules if r.enabled}}
        if set(expected)!=set(data['blocks']):raise ValueError()
        if any(hashlib.sha256(body.encode()).hexdigest()!=data['blocks'][name] for name,body in expected.items()):raise ValueError()
        clean=[];active=None;body=[];seen=set()
        _,sections=parse_sections(config)
        for index,line in enumerate(lines):
            if line.startswith(META):continue
            if line.startswith(PREFIX+'BEGIN '):
                name=line[len(PREFIX+'BEGIN '):].strip()
                front=next((s for s in sections if s.start<=index<s.end),None)
                if active is not None or name in seen or name not in expected or not front or front.kind not in ('frontend','listen') or front.name!=name or front.mode!='http':raise ValueError()
                active=name;body=[];seen.add(name);continue
            if line.startswith(PREFIX+'END '):
                if active is None or line[len(PREFIX+'END '):].strip()!=active or ''.join(body).replace('\r\n','\n')!=expected[active]:raise ValueError()
                active=None;continue
            if line.startswith(PREFIX):raise ValueError()
            if active is not None:body.append(line)
            else:clean.append(line)
        if active is not None or seen!=set(expected):raise ValueError()
        return rules,''.join(clean)
    except (ValueError,TypeError,KeyError,AttributeError) as error:
        raise ValueError('Verwaltete Regeln oder Redirects wurden im Text verändert. Markierte Regeln und Metadaten prüfen.') from error


def strip(config):return read_and_strip(config)[1]


def validate_document(doc):
    clean=strip(doc.imported_config or '')
    fronts={s.name:s.mode for s in parse_sections(clean)[1] if s.kind in ('frontend','listen') and s.name not in doc.removed_backends}
    if doc.imported_config is None:fronts['public_http']='http'
    fronts.update({f.name:f.mode for f in doc.frontends})
    for rule in doc.rules:
        if rule.enabled and fronts.get(rule.frontend or 'public_http')!='http':
            raise ValueError('Regel benötigt ein vorhandenes HTTP-/HTTPS-Frontend: '+(rule.frontend or 'public_http'))


def inject(config,doc):
    validate_document(doc)
    config=strip(config)
    if not doc.rules:return config
    lines,sections=parse_sections(config);insert={};blocks={}
    acme=['public_http'] if doc.acme_enabled else []
    groups={(r.frontend or 'public_http') for r in doc.rules if r.enabled}
    for name in groups:
        front=next((s for s in sections if s.kind in ('frontend','listen') and s.name==name and s.mode=='http'),None)
        if not front:raise ValueError('Regel benötigt ein vorhandenes HTTP-/HTTPS-Frontend: '+name)
        ids={'rule_'+r.id for r in doc.rules if r.enabled and (r.frontend or 'public_http')==name}
        if any(t[0]=='acl' and len(t)>1 and t[1] in ids for _,t in front.lines):raise ValueError('ACL-Namenskonflikt bei Regeln auf '+name)
        body=statements(doc.rules,name,name in acme)
        blocks[name]=hashlib.sha256(('\n'.join(body)+'\n').encode()).hexdigest()
        # Preserve preceding auth/deny/header actions, but beat old redirects and
        # terminal replies. Backend selection stays after the graphical rules.
        index=next((i for i,t in front.lines if t[0] in ('redirect','use_backend','default_backend') or t[0]=='http-request' and len(t)>1 and t[1] in ('redirect','return','use-service','allow')),front.end)
        ending='\r\n' if lines[front.start].endswith('\r\n') else '\n'
        insert[index]=ending.join([PREFIX+'BEGIN '+name,*body,PREFIX+'END '+name])+ending
    result=''.join(insert.get(index,'')+line for index,line in enumerate(lines))+insert.get(len(lines),'')
    rules=[r.model_dump() for r in doc.rules]
    meta={'version':1,'rules':rules,'acme':acme,'blocks':blocks,'digest':digest(rules)}
    return result.rstrip('\r\n')+'\n'+META+base64.b64encode(json.dumps(meta,sort_keys=True,separators=(',',':')).encode()).decode()+'\n'
