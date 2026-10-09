"""Domain/path-scoped source-IP allowlists, before frontend redirects and rewrites."""
import base64
import hashlib
import json
import re
import shlex

from pydantic import BaseModel,Field,ConfigDict,field_validator
from backend.schemas import AccessPolicy,Host
from backend.haproxy_config import parse_sections,map_routes

PREFIX='# haproxy-control-access '
META=PREFIX+'v1 '

class Site(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:str=Field(pattern=r'^(host|route)$')
    frontend:str=Field(pattern=r'^[a-zA-Z0-9_.-]{1,100}$')
    backend:str=Field(pattern=r'^[a-zA-Z0-9_.-]{1,100}$')
    domain:str=Field(max_length=253)
    path:str=Field(default='/',max_length=300)
    policy:AccessPolicy

    @field_validator('domain')
    @classmethod
    def domain_ok(cls,value):return Host.domain_ok(value)

    @field_validator('path')
    @classmethod
    def path_ok(cls,value):
        paths=AccessPolicy.paths_ok([value])
        if not paths:raise ValueError('IP-Zugriff benötigt einen gültigen Proxy-Pfad.')
        return paths[0]


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def read(config):
    values=[line[len(META):] for line in config.splitlines() if line.startswith(META)]
    if not values:return None
    try:
        if len(values)!=1 or len(values[0])>2*1024*1024:raise ValueError()
        data=json.loads(base64.b64decode(values[0],validate=True))
        if not isinstance(data,dict) or set(data)!={'version','sites','blocks','digest'} or data['version']!=1:raise ValueError()
        if not isinstance(data['sites'],list) or not 1<=len(data['sites'])<=2200 or digest(data['sites'])!=data['digest']:raise ValueError()
        if not isinstance(data['blocks'],dict) or not 1<=len(data['blocks'])<=500:raise ValueError()
        if any(not re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}',k) or not isinstance(v,str) or not re.fullmatch(r'[a-f0-9]{64}',v) for k,v in data['blocks'].items()):raise ValueError()
        sites=[Site.model_validate(value).model_dump() for value in data['sites']]
        if sites!=data['sites']:raise ValueError()
        if len({(s['kind'],s['frontend'],s['backend'],s['domain'],s['path']) for s in sites})!=len(sites):raise ValueError()
        if {s['frontend'] for s in sites}!=set(data['blocks']):raise ValueError()
        return data
    except (ValueError,TypeError,KeyError,AttributeError) as error:
        raise ValueError('Ungültige IP-Zugriffsmetadaten. Konfiguration und markierte Zugriffsregeln prüfen.') from error


def strip_managed(config):
    metadata=read(config);output=[];block=None;content=[];found={}
    for line in config.splitlines(keepends=True):
        text=line.strip()
        if line.startswith(META):continue
        if text.startswith(PREFIX+'BEGIN '):
            if block is not None:raise ValueError('Verschachtelte IP-Zugriffsregeln.')
            block=text[len(PREFIX+'BEGIN '):];content=[line.rstrip('\r\n')];continue
        if text.startswith(PREFIX+'END '):
            if block is None or text!=PREFIX+'END '+block or block in found:raise ValueError('Unvollständige IP-Zugriffsregeln.')
            content.append(line.rstrip('\r\n'));found[block]=hashlib.sha256(('\n'.join(content)+'\n').encode()).hexdigest();block=None;continue
        if block is not None:content.append(line.rstrip('\r\n'))
        else:output.append(line)
    if block is not None or found!=(metadata or {}).get('blocks',{}):
        raise ValueError('Verwaltete IP-Zugriffsregeln wurden im Text verändert. Markierte Regeln und Metadaten müssen zusammenpassen.')
    return ''.join(output)


def bindings(doc):
    result=[]
    for kind,entries in (('host',doc.hosts),('route',doc.imported_routes)):
        for item in entries:
            if not item.access_policy or kind=='host' and not item.enabled:continue
            for domain in item.hostnames:
                result.append(Site(kind=kind,frontend=item.frontend,backend='backend_'+item.id if kind=='host' else item.backend,
                                   domain=domain,path=item.path if kind=='host' else '/',policy=item.access_policy).model_dump())
    old=read(doc.imported_config or '')
    if old:
        # Raw/uneditable pre-existing sites stay restricted. Editable sites can
        # explicitly clear/change their policy, without resurrecting old rules.
        _,routes,_=map_routes(doc.imported_config,[m.model_dump() for m in doc.imported_maps])
        editable={(r.frontend,name,'/') for r in routes for name in r.hostnames}
        editable.update((h.frontend,name,h.path) for h in doc.hosts+doc.imported_managed_hosts for name in h.hostnames)
        owned={'backend_'+h.id for h in doc.imported_managed_hosts}|set(doc.removed_backends)
        result += [site for site in old['sites'] if site['backend'] not in owned and site['frontend'] not in doc.removed_backends
                   and (site['frontend'],site['domain'],site['path']) not in editable]
    if len(result)>2200 or sum(len(s['policy']['networks']) for s in result)>20000:raise ValueError('IP-Zugriffslisten überschreiten das Server-Limit.')
    return result


def restore_document(doc):
    old=read(doc.imported_config or '')
    if old:
        for kind,items in (('host',doc.hosts),('route',doc.imported_routes)):
            for item in items:
                sites=[s for s in old['sites'] if s['kind']==kind and s['frontend']==item.frontend and s['backend']==('backend_'+item.id if kind=='host' else item.backend) and s['path']==(item.path if kind=='host' else '/') and s['domain'] in item.hostnames]
                if sites:
                    if len(sites)!=len(item.hostnames) or len({digest(s['policy']) for s in sites})!=1:
                        raise ValueError('Hostnamen mit unterschiedlichen IP-Regeln müssen getrennt bearbeitet werden.')
                    item.access_policy=AccessPolicy.model_validate(sites[0]['policy'])
    return doc


def inherited_http_rules(lines,sections,front):
    defaults=[s for s in sections if s.kind=='defaults' and s.start<front.start]
    tokens=shlex.split(lines[front.start],comments=True)
    selected=next((s for s in sections if s.kind=='defaults' and s.name==tokens[tokens.index('from')+1]),None) if 'from' in tokens else defaults[-1] if defaults else None
    seen=set()
    while selected and selected.name not in seen:
        seen.add(selected.name)
        if any(t[0]=='http-request' or t[0]=='tcp-request' and 'set-src' in t for _,t in selected.lines):
            raise ValueError('IP-Zugriff: HTTP-Regeln bzw. set-src in den geerbten defaults zuerst in den Proxy-Abschnitt verschieben: '+front.name)
        tokens=shlex.split(lines[selected.start],comments=True)
        selected=next((s for s in sections if s.kind=='defaults' and s.name==tokens[tokens.index('from')+1]),None) if 'from' in tokens else None


def validate_document(doc):
    config=doc.imported_config or ''
    strip_managed(config)
    lines,sections=parse_sections(config)
    fronts={s.name:s for s in sections if s.kind in ('frontend','listen')}
    modes={name:s.mode for name,s in fronts.items()}
    if doc.imported_config is None:modes['public_http']='http'
    modes.update({f.name:f.mode for f in doc.frontends})
    for site in bindings(doc):
        if modes.get(site['frontend'])!='http':raise ValueError('IP-Zugriff benötigt ein vorhandenes HTTP-Frontend: '+site['frontend'])
        if site['policy']['paths'] and site['path']!='/' and any(not path.startswith(site['path']) for path in site['policy']['paths']):
            raise ValueError('IP-Zugriff: geschützte Pfade müssen innerhalb des Proxy-Host-Pfads liegen.')
        if site['frontend'] in fronts:
            front=fronts[site['frontend']]
            inherited_http_rules(lines,sections,front)
            if any(t[0]=='tcp-request' and 'set-src' in t for _,t in front.lines):raise ValueError('IP-Zugriff: set-src in TCP-Regeln des Frontends zuerst prüfen: '+front.name)


def inject(config,doc):
    config=strip_managed(config);sites=bindings(doc)
    if not sites:return config
    if 'mgmt_access_' in config:raise ValueError('Namenskonflikt mit internen mgmt_access_-Regeln.')
    lines,sections=parse_sections(config);fronts={s.name:s for s in sections if s.kind in ('frontend','listen')};patch={};blocks={}
    for name in dict.fromkeys(s['frontend'] for s in sites):
        front=fronts.get(name)
        if not front or front.mode!='http':raise ValueError('IP-Zugriff benötigt ein vorhandenes HTTP-Frontend: '+name)
        inherited_http_rules(lines,sections,front)
        if any(t[0]=='tcp-request' and 'set-src' in t for _,t in front.lines):raise ValueError('IP-Zugriff: set-src in TCP-Regeln des Frontends zuerst prüfen: '+name)
        block=[PREFIX+'BEGIN '+name]
        for site in (s for s in sites if s['frontend']==name):
            key='mgmt_access_'+digest(site)[:16];domain=site['domain'];policy=site['policy']
            # One named ACL may contain alternative expressions (exact/wildcard).
            block.append(f'    acl {key}_host hdr(host),field(1,:) '+('-m end -i '+domain[1:] if domain.startswith('*.') else '-i '+domain))
            paths=policy['paths'] or [site['path']]
            if policy['paths'] and site['path']!='/':
                if any(not path.startswith(site['path']) for path in paths):raise ValueError('IP-Zugriff: geschützte Pfade müssen innerhalb des Proxy-Host-Pfads liegen.')
            # Preserve original request URI; also check one URL-decoded form.
            block += [f'    acl {key}_path path_beg '+ ' '.join(paths), f'    acl {key}_path path,url_dec -m beg '+ ' '.join(paths),
                      f'    acl {key}_src fc_src '+ ' '.join(policy['networks'])]
            condition=f'{key}_host {key}_path !{key}_src'
            if doc.imported_config is None and doc.acme_enabled and name=='public_http':condition+=' !{ path_beg /.well-known/acme-challenge/ }'
            block.append('    http-request deny deny_status 403 if '+condition)
        block.append(PREFIX+'END '+name);text='\n'.join(block)+'\n';blocks[name]=hashlib.sha256(text.encode()).hexdigest()
        ending='\r\n' if lines[front.start].endswith('\r\n') else '\n'
        patch[front.start]=lines[front.start].rstrip('\r\n')+ending+text.replace('\n',ending)
    result=''.join(patch.get(i,line) for i,line in enumerate(lines))
    data={'version':1,'sites':sites,'blocks':blocks,'digest':digest(sites)}
    encoded=base64.b64encode(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).decode()
    if len(encoded)>2*1024*1024:raise ValueError('IP-Zugriffsmetadaten überschreiten das Server-Limit.')
    return result.rstrip('\r\n')+'\n'+META+encoded+'\n'
