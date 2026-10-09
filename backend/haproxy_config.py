"""Inspect HAProxy sections and edit selected directives without regenerating the rest."""
import ipaddress
import base64
import hashlib
import json
import re
import shlex
from dataclasses import dataclass, field

from backend.schemas import ImportedBackend, ImportedServer, ImportedRoute, DOMAIN

MAP_ROUTE = re.compile(r'^\s*use_backend\s+%\[req\.hdr\(host\),lower,map(?:_str)?\((/[^,\s)]+),([a-zA-Z0-9_.-]+)\)\]\s*(?:#.*)?$')
MIGRATION_PREFIX = '# haproxy-control-migration '

def migration_context(config):
    lines=[line for line in config.splitlines() if line.startswith(MIGRATION_PREFIX)]
    if not lines:return None
    try:
        result=json.loads(base64.b64decode(lines[-1][len(MIGRATION_PREFIX):],validate=True))
        if not isinstance(result,dict) or set(result)!={'files','maps'}:raise ValueError()
        from backend.schemas import ImportedSource
        return {key:[ImportedSource.model_validate(item).model_dump() for item in result[key]] for key in ('files','maps')}
    except Exception as error:raise ValueError('Ungültige Migrationsmetadaten; erneut importieren.') from error

def map_references(config):
    return list(dict.fromkeys(re.findall(r'\bmap(?:_[a-z]+)?\((/[^,\s)]+)',config)))

def map_routes(config,maps):
    lines,sections=parse_sections(config); rules=[]; routes=[]; warnings=[]
    values={m['path']:m['content'] for m in maps}
    for section in sections:
        if section.kind not in ('frontend','listen'):continue
        for index,tokens in section.lines:
            match=MAP_ROUTE.fullmatch(lines[index].rstrip('\r\n'))
            if not match:continue
            path,default=match.groups()
            if path not in values:
                warnings.append(f'{section.name}: Host-Map {path} fehlt. Zuordnung bleibt im Texteditor.');continue
            entries=[];seen=set();valid=True
            for line in values[path].splitlines():
                try:t=shlex.split(line,comments=True)
                except ValueError:valid=False;break
                if not t:continue
                if len(t)!=2 or not DOMAIN.fullmatch(t[0]) or t[0].startswith('*.') or t[0]!=t[0].lower() or t[0] in seen or not re.fullmatch(r'[a-zA-Z0-9_.-]+',t[1]):valid=False;break
                seen.add(t[0]);entries.append(t)
            if not valid:
                warnings.append(f'{section.name}: Komplexe oder doppelte Map-Einträge bleiben unverändert im Texteditor.');continue
            if sum(1 for _,t in section.lines if t[0]=='use_backend' and len(t)>1 and t[1].startswith('%['))!=1:
                warnings.append(f'{section.name}: Mehrere dynamische Routen bleiben im Texteditor.');continue
            rules.append({'index':index,'frontend':section.name,'path':path,'default':default})
            routes += [ImportedRoute(id='map_'+hashlib.sha256(f'{section.name}:{domain}'.encode()).hexdigest()[:16],
                       frontend=section.name,domain=domain,backend=backend) for domain,backend in entries]
        if any(rule['frontend']==section.name for rule in rules):continue
        # A contiguous block of simple host ACL / use_backend pairs can be edited
        # in place. Complex conditions and ACLs used elsewhere remain untouched.
        pairs=[]
        for position,(index,t) in enumerate(section.lines[:-1]):
            if len(t)!=5 or t[0]!='acl' or t[2] not in ('hdr(host)','req.hdr(host)') or t[3]!='-i':continue
            domain=t[4]
            next_index,next_tokens=section.lines[position+1]
            if len(next_tokens)!=4 or next_tokens[0]!='use_backend' or next_tokens[2:]!=['if',t[1]]:continue
            if not DOMAIN.fullmatch(domain.lower()) or domain.startswith('*.'):continue
            if not re.fullmatch(r'[a-zA-Z0-9_.-]+',next_tokens[1]):continue
            # Do not remove ACLs that are referenced by other rules.
            if sum(tokens.count(t[1]) for _,tokens in section.lines)!=2:continue
            pairs.append((index,next_index,domain.lower(),next_tokens[1]))
        if not pairs:continue
        indexes={i for pair in pairs for i in pair[:2]}
        low,high=min(indexes),max(indexes)
        if any(low<=i<=high and i not in indexes for i,_ in section.lines):
            warnings.append(f'{section.name}: Verschachtelte Host-ACLs bleiben im Texteditor.');continue
        if len({pair[2] for pair in pairs})!=len(pairs):continue
        rules.append({'index':low,'indexes':sorted(indexes),'frontend':section.name,'path':None,'default':None})
        routes += [ImportedRoute(id='acl_'+hashlib.sha256(f'{section.name}:{domain}'.encode()).hexdigest()[:16],
                   frontend=section.name,domain=domain,backend=backend) for _,_,domain,backend in pairs]
    return rules,routes,warnings

HEADERS = {'global','defaults','frontend','backend','listen','resolvers','peers','userlist',
           'mailers','cache','program','ring','http-errors','log-forward','fcgi-app','crt-store'}


@dataclass
class Section:
    kind: str
    name: str
    start: int
    end: int = 0
    mode: str = 'tcp'
    balance: str | None = 'roundrobin'
    default_weight: int = 1
    server_tls: bool = False
    lines: list = field(default_factory=list)


def parse_sections(config):
    lines=config.splitlines(keepends=True); sections=[]; defaults={}; inherited={'mode':'tcp','balance':'roundrobin','default_weight':1,'server_tls':False}
    for index,line in enumerate(lines):
        try: tokens=shlex.split(line,comments=True)
        except ValueError: tokens=[]
        if tokens and tokens[0] in HEADERS:
            if sections: sections[-1].end=index
            kind=tokens[0];name=tokens[1] if len(tokens)>1 else ''
            values={'mode':'tcp','balance':'roundrobin','default_weight':1,'server_tls':False} if kind=='defaults' else inherited.copy()
            if 'from' in tokens:
                pos=tokens.index('from')
                if pos+1<len(tokens):values=defaults.get(tokens[pos+1],{'mode':'unknown'}).copy()
            sections.append(Section(kind,name,index,**values))
        elif sections and tokens:
            section=sections[-1];section.lines.append((index,tokens))
            if tokens[0]=='mode' and len(tokens)==2:
                section.mode=tokens[1]
            if tokens[0]=='balance':section.balance=tokens[1] if len(tokens)==2 and tokens[1] in ('roundrobin','leastconn','source') else None
            if tokens[0]=='default-server':
                if 'weight' in tokens:
                    try:section.default_weight=int(tokens[tokens.index('weight')+1])
                    except (ValueError,IndexError):pass
                if 'ssl' in tokens:section.server_tls=True
                if 'no-ssl' in tokens:section.server_tls=False
        if sections and sections[-1].kind=='defaults':
            s=sections[-1];inherited={key:getattr(s,key) for key in ('mode','balance','default_weight','server_tls')};defaults[s.name]=inherited.copy()
    if sections:sections[-1].end=len(lines)
    return lines,sections


def inventory(config):
    _,sections=parse_sections(config); result=[]
    for s in sections:
        if s.kind not in ('frontend','backend','listen'):continue
        binds=[' '.join(t[1:]) for _,t in s.lines if t[0]=='bind']
        routes=[{'backend':t[1],'default':t[0]=='default_backend'} for _,t in s.lines
                if t[0] in ('use_backend','default_backend') and len(t)>1]
        domains=[]
        for _,t in s.lines:
            if t[0]=='acl' and any('hdr(' in token or 'hdr_end(' in token for token in t[2:]):
                # Expose only simple domain literals; never arbitrary rule values.
                domains += [token for token in t[3:] if re.fullmatch(r'\*?[a-zA-Z0-9_.-]+\.[a-zA-Z0-9_.-]+',token)]
        tls=any(re.search(r'(?:^|\s)ssl(?:\s|$)',b) for b in binds)
        quic=any('quic' in b for b in binds)
        sni=any('req.ssl_sni' in ' '.join(t) or 'req_ssl_sni' in ' '.join(t) for _,t in s.lines)
        if s.mode=='http':
            if any(t[:3]==['http-request','use-service','prometheus-exporter'] for _,t in s.lines):service='Prometheus-Exporter'
            elif any(t[:2]==['stats','enable'] for _,t in s.lines):service='HAProxy-Statistikdienst'
            elif quic:service='HTTP/3 · QUIC'
            elif routes or s.kind in ('backend','listen'):service='HTTPS-Reverse-Proxy' if tls else 'HTTP-Reverse-Proxy'
            elif any(t[:2] in (['http-request','redirect'],['redirect','scheme']) for _,t in s.lines):service='HTTP-Weiterleitung'
            else:service='HTTP-Dienst'
        elif s.mode=='tcp':
            service='TCP mit TLS-Terminierung' if tls else 'TLS-Passthrough (SNI)' if sni else 'TCP-Proxy (Layer 4)'
        else:service=s.mode or 'Unbekannt'
        result.append({'kind':s.kind,'name':s.name,'mode':s.mode,'service':service,
                       'binds':binds,'routes':routes,'domains':list(dict.fromkeys(domains)),'tls':tls})
    return result


def enrich_stats(data, config=None, maps=None):
    proxies=inventory(config) if config is not None else []
    if config is not None and maps:
        rules,routes,_=map_routes(config,maps)
        for proxy in proxies:
            active=[rule for rule in rules if rule['frontend']==proxy['name']]
            if active:
                proxy['routes']=[{'backend':route.backend,'domain':route.domain,'default':False} for route in routes if route.frontend==proxy['name']]
                proxy['routes'] += [{'backend':rule['default'],'default':True} for rule in active if rule['default']]
                proxy['domains']=[route.domain for route in routes if route.frontend==proxy['name']]
    front={p['name']:p for p in proxies if p['kind'] in ('frontend','listen')}
    back={p['name']:p for p in proxies if p['kind'] in ('backend','listen')}
    rows=[]
    for raw in data.get('rows',[]):
        r={k:v for k,v in raw.items() if k is not None}
        role={'0':'frontend','1':'backend','2':'server','3':'listener'}.get(r.get('type'))
        if role is None:role='frontend' if r.get('svname')=='FRONTEND' else 'backend' if r.get('svname')=='BACKEND' else 'server'
        meta=(front if role in ('frontend','listener') else back).get(r.get('pxname'),{})
        mode=r.get('mode') or meta.get('mode') or 'unknown'
        incoming=[p['name'] for p in front.values() if p['name']==r.get('pxname') and p['kind']=='listen'
                  or any(route['backend']==r.get('pxname') for route in p['routes'])]
        rows.append(r|{'role':role,'mode':mode,'proxy_kind':meta.get('kind',''),
                       'service':meta.get('service','HTTP' if mode=='http' else 'TCP (Layer 4)' if mode=='tcp' else 'Unbekannter Modus'),
                       'binds':meta.get('binds',[]),'domains':meta.get('domains',[]),
                       'routes':meta.get('routes',[]),'frontends':incoming})
    return data|{'rows':rows,'proxies':proxies}


def endpoint(value):
    if value.startswith('['):
        match=re.fullmatch(r'\[([^]]+)\]:(\d+)',value)
    else:match=re.fullmatch(r'([^:]+):(\d+)',value)
    if not match:raise ValueError('Kein expliziter statischer Zielport')
    return match.group(1),int(match.group(2))


def extract_backends(config):
    lines,sections=parse_sections(config); backends=[]; warnings=[]
    names=[s.name for s in sections if s.kind in ('backend','listen')]
    for s in sections:
        if s.kind not in ('backend','listen'):continue
        if names.count(s.name)!=1:
            warnings.append(f'{s.name}: Mehrdeutiger Name bleibt im Texteditor.');continue
        balance=s.balance;default_weight=s.default_weight;servers=[]
        for index,t in s.lines:
            if t[0]=='server-template':warnings.append(f'{s.name}: Dynamische server-template-Ziele bleiben im Texteditor.')
            if t[0]!='server':continue
            try:
                if len(t)<3 or any(c in lines[index] for c in ('"',"'",'\\')):raise ValueError('Komplexe Maskierung')
                address,port=endpoint(t[2]); weight=default_weight
                if 'weight' in t:weight=int(t[t.index('weight')+1])
                servers.append(ImportedServer(name=t[1],address=address,port=port,weight=weight,tls='ssl' in t[3:] or s.server_tls and 'no-ssl' not in t[3:]))
            except (ValueError,IndexError):warnings.append(f'{s.name}: Server {t[1] if len(t)>1 else "?"} bleibt unverändert im Texteditor.')
        # Ambiguous server names cannot be edited safely.
        duplicates={sv.name for sv in servers if sum(other.name==sv.name for other in servers)>1}
        if duplicates:
            warnings.append(f'{s.name}: Doppelte Servernamen bleiben im Texteditor.')
            servers=[sv for sv in servers if sv.name not in duplicates]
        if servers:
            try:backends.append(ImportedBackend(name=s.name,mode=s.mode if s.mode in ('http','tcp') else 'unknown',balance=balance,servers=servers))
            except ValueError:warnings.append(f'{s.name}: Komplexer Backend-Name bleibt im Texteditor.')
    return backends,warnings


def import_config(config, active_hash, maps=None, sources=None, map_hashes=None):
    from backend.schemas import Document
    from backend.basic_auth import restore_routes
    backends,warnings=extract_backends(config)
    proxies=inventory(config)
    rules,routes,map_warnings=map_routes(config,maps or [])
    known={p['name'] for p in proxies if p['kind'] in ('backend','listen')}
    for route in routes:
        if route.backend not in known:warnings.append(f'{route.domain}: Backend {route.backend} fehlt in den eingelesenen Dateien.')
    for rule in rules:
        if rule['default'] and rule['default'] not in known:warnings.append(f'{rule["frontend"]}: Fallback {rule["default"]} fehlt in den eingelesenen Dateien.')
    return {'document':restore_routes(Document(imported_config=config,imported_active_hash=active_hash,imported_backends=backends,
                               imported_routes=routes,imported_route_frontends=list(dict.fromkeys(r['frontend'] for r in rules)),imported_maps=maps or [],imported_sources=sources or [],
                               imported_map_hashes=map_hashes or [])).model_dump(),
            'proxies':proxies,'warnings':warnings+map_warnings,
            'summary':{'frontends':sum(p['kind'] in ('frontend','listen') for p in proxies),
                       'backends':sum(p['kind'] in ('backend','listen') for p in proxies),
                       'editable_backends':len(backends),'editable_servers':sum(len(b.servers) for b in backends),
                       'tcp':sum(p['mode']=='tcp' for p in proxies),'routes':len(routes),'converted_maps':sum(bool(r['path']) for r in rules)}}


def generate_imported(doc):
    original=doc.imported_config
    baseline,_=extract_backends(original)
    if doc.rules:raise ValueError('Übernommene Konfiguration: allgemeine Regeln im Texteditor ergänzen.')
    if {b.name for b in doc.imported_backends}!={b.name for b in baseline}:
        raise ValueError('Übernommene Backend-Pools können hier nicht hinzugefügt oder entfernt werden.')
    lines,sections=parse_sections(original); lookup={b.name:b for b in baseline}; patch={}
    for edited in doc.imported_backends:
        old=lookup[edited.name]; section=next(s for s in sections if s.kind in ('backend','listen') and s.name==edited.name)
        if edited.mode!=old.mode or {s.name for s in old.servers}!={s.name for s in edited.servers}:
            raise ValueError('Modus und Servernamen einer übernommenen Konfiguration im Texteditor ändern.')
        if edited.balance!=old.balance:
            if edited.balance is None:raise ValueError('Einen unterstützten Algorithmus wählen.')
            indexes=[i for i,t in section.lines if t[0]=='balance']
            if len(indexes)>1:raise ValueError('Mehrere balance-Direktiven im Texteditor ändern.')
            if indexes:
                index=indexes[0]; match=re.fullmatch(r'(\s*balance\s+)([^#]*)(#.*)?',lines[index].rstrip('\r\n'))
                ending='\r\n' if lines[index].endswith('\r\n') else '\n' if lines[index].endswith('\n') else ''
                patch[index]=match[1]+edited.balance+(' '+match[3] if match[3] else '')+ending
            else:
                ending='\r\n' if lines[section.start].endswith('\r\n') else '\n'
                patch[section.start]=lines[section.start].rstrip('\r\n')+ending+'    balance '+edited.balance+ending
        old_servers={s.name:s for s in old.servers}
        for server in edited.servers:
            before=old_servers[server.name]
            if server.tls!=before.tls:raise ValueError('TLS-Optionen einer übernommenen Verbindung im Texteditor ändern.')
            if server==before:continue
            index=next(i for i,t in section.lines if t[0]=='server' and t[1]==server.name)
            line=lines[index]; match=re.match(r'(\s*server\s+\S+\s+)(\S+)([^\r\n]*)(\r?\n)?$',line)
            addr=f'[{server.address}]:{server.port}' if ':' in server.address else f'{server.address}:{server.port}'
            tail=match[3];body,sep,comment=tail.partition('#')
            if server.weight!=before.weight:
                if re.search(r'\sweight\s+\d+(?=\s|$)',body):body=re.sub(r'(\sweight\s+)\d+(?=\s|$)',lambda m:m[1]+str(server.weight),body,count=1)
                else:body=body.rstrip()+' weight '+str(server.weight)+(' ' if sep else '')
            patch[index]=match[1]+addr+body+sep+comment+(match[4] or '')
    rules,_,_=map_routes(original,[m.model_dump() for m in doc.imported_maps])
    valid_fronts={rule['frontend'] for rule in rules};back_names={s.name for s in sections if s.kind in ('backend','listen')}
    back_names.update(b.name for b in doc.backends if b.mode=='http')
    if any(r.frontend not in valid_fronts or r.backend not in back_names for r in doc.imported_routes):
        raise ValueError('Domain-Routen müssen einen eingelesenen Frontend- und Backend-Namen verwenden.')
    for rule in rules:
        routes=[r for r in doc.imported_routes if r.frontend==rule['frontend']]
        if len({r.domain for r in routes})!=len(routes):raise ValueError('Doppelte Domain im selben Frontend.')
        index=rule['index'];ending='\r\n' if lines[index].endswith('\r\n') else '\n'
        indent=re.match(r'\s*',lines[index])[0]
        entries=[]
        for route in routes:
            acl='mgmt_'+route.id
            if any(i not in rule.get('indexes',[]) and re.search(r'\bacl\s+'+re.escape(acl)+r'\b',line) for i,line in enumerate(lines)):
                raise ValueError('ACL-Namenskonflikt; Domain-ID ändern.')
            entries += [indent+f'acl {acl} hdr(host) -i {route.domain}',indent+f'use_backend {route.backend} if {acl}']
        if rule['default']:entries.append(indent+'use_backend '+rule['default'])
        patch[index]=ending.join(entries)+ending
        for other in rule.get('indexes',[]):
            if other!=index:patch[other]=''
    result=''.join(patch.get(index,line) for index,line in enumerate(lines) if not line.startswith(MIGRATION_PREFIX))
    if doc.imported_sources:
        context={'files':[s.model_dump() for s in doc.imported_sources],'maps':[s.model_dump() for s in doc.imported_map_hashes]}
        result=result.rstrip('\r\n')+'\n'+MIGRATION_PREFIX+base64.b64encode(json.dumps(context,separators=(',',':')).encode()).decode()+'\n'
    return result
