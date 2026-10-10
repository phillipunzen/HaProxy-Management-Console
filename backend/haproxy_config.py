"""Inspect HAProxy sections and edit selected directives without regenerating the rest."""
import ipaddress
import base64
import hashlib
import json
import re
import shlex
import zlib
from dataclasses import dataclass, field

from backend.schemas import ImportedBackend, ImportedServer, ImportedRoute, DOMAIN

MAP_ROUTE = re.compile(r'^\s*use_backend\s+%\[req\.hdr\(host\),lower,map(?:_str)?\((/[^,\s)]+),([a-zA-Z0-9_.-]+)\)\]\s*(?:#.*)?$')
MIGRATION_PREFIX = '# haproxy-control-migration '
GRAPH_PREFIX = '# haproxy-control-document-v1 '

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
    preferences=None
    def alias_groups(frontend,backend,names):
        nonlocal preferences
        if preferences is None:
            from backend.basic_auth import read_metadata
            from backend.tls_bindings import read
            from backend.access_control import read as access_metadata,digest
            auth=read_metadata(config) or {};preferences={}
            for site in auth.get('sites',[]):
                if site['kind']=='route':preferences[(site['frontend'],site['backend'],site['domain'])]=(site['group'],site['forward'],site.get('replace_existing',False))
            tls={(p['frontend'],s['domain']):s['certificate'] for p in read(config) for s in p['sites']}
            access={(s['frontend'],s['backend'],s['domain']):digest(s['policy']) for s in (access_metadata(config) or {}).get('sites',[]) if s['kind']=='route'}
            preferences=(preferences,tls,access)
        auth,tls,access=preferences;groups={}
        for name in names:
            key=(auth.get((frontend,backend,name)),tls.get((frontend,name)),access.get((frontend,backend,name)))
            groups.setdefault(key,[]).append(name)
        # Differently protected/certified names stay separate on reimport.
        return [values[offset:offset+30] for values in groups.values() for offset in range(0,len(values),30)]
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
            if len(t)<5 or t[0]!='acl' or t[2] not in ('hdr(host)','req.hdr(host)') or t[3]!='-i':continue
            domains=[name.lower() for name in t[4:]]
            next_index,next_tokens=section.lines[position+1]
            if len(next_tokens)!=4 or next_tokens[0]!='use_backend' or next_tokens[2:]!=['if',t[1]]:continue
            if any(len(name)>253 or not DOMAIN.fullmatch(name) or name.startswith('*.') for name in domains) or len(set(domains))!=len(domains):continue
            if not re.fullmatch(r'[a-zA-Z0-9_.-]+',next_tokens[1]):continue
            # Do not remove ACLs that are referenced by other rules.
            if sum(tokens.count(t[1]) for _,tokens in section.lines)!=2:continue
            pairs.append((index,next_index,domains,next_tokens[1]))
        if not pairs:continue
        indexes={i for pair in pairs for i in pair[:2]}
        low,high=min(indexes),max(indexes)
        if any(low<=i<=high and i not in indexes for i,_ in section.lines):
            warnings.append(f'{section.name}: Verschachtelte Host-ACLs bleiben im Texteditor.');continue
        domains=[name for pair in pairs for name in pair[2]]
        if len(set(domains))!=len(domains):continue
        rules.append({'index':low,'indexes':sorted(indexes),'frontend':section.name,'path':None,'default':None})
        for _,_,names,backend in pairs:
            groups=alias_groups(section.name,backend,names) if len(names)>1 else [names]
            routes += [ImportedRoute(id='acl_'+hashlib.sha256(f'{section.name}:{names[0]}'.encode()).hexdigest()[:16],
                       frontend=section.name,domain=names[0],aliases=names[1:],backend=backend) for names in groups]
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
    server_verify: bool = True
    server_ca_file: str | None = None
    lines: list = field(default_factory=list)


def parse_sections(config):
    global_values=re.findall(r'(?m)^\s*ssl-server-verify\s+(none|required)\s*(?:#.*)?$',config)
    global_verify=not global_values or global_values[-1]!='none'
    def base():return {'mode':'tcp','balance':'roundrobin','default_weight':1,'server_tls':False,'server_verify':global_verify,'server_ca_file':None}
    lines=config.splitlines(keepends=True); sections=[]; defaults={}; inherited=base()
    for index,line in enumerate(lines):
        try: tokens=shlex.split(line,comments=True)
        except ValueError: tokens=[]
        if tokens and tokens[0] in HEADERS:
            if sections: sections[-1].end=index
            kind=tokens[0];name=tokens[1] if len(tokens)>1 else ''
            values=base() if kind=='defaults' else inherited.copy()
            if 'from' in tokens:
                pos=tokens.index('from')
                if pos+1<len(tokens):values=defaults.get(tokens[pos+1],{'mode':'unknown'}).copy()
            sections.append(Section(kind,name,index,**values))
        elif sections and tokens:
            section=sections[-1];section.lines.append((index,tokens))
            if section.kind=='global' and tokens[:1]==['ssl-server-verify'] and len(tokens)==2:
                global_verify=tokens[1]!='none';inherited['server_verify']=global_verify
            if tokens[0]=='mode' and len(tokens)==2:
                section.mode=tokens[1]
            if tokens[0]=='balance':section.balance=tokens[1] if len(tokens)==2 and tokens[1] in ('roundrobin','leastconn','source','first') else None
            if tokens[0]=='default-server':
                if 'weight' in tokens:
                    try:section.default_weight=int(tokens[tokens.index('weight')+1])
                    except (ValueError,IndexError):pass
                if 'ssl' in tokens:section.server_tls=True
                if 'no-ssl' in tokens:section.server_tls=False
                if 'verify' in tokens and tokens.index('verify')+1<len(tokens):section.server_verify=tokens[tokens.index('verify')+1]!='none'
                if 'ca-file' in tokens and tokens.index('ca-file')+1<len(tokens):section.server_ca_file=tokens[tokens.index('ca-file')+1]
        if sections and sections[-1].kind=='defaults':
            s=sections[-1];inherited={key:getattr(s,key) for key in ('mode','balance','default_weight','server_tls','server_verify','server_ca_file')};defaults[s.name]=inherited.copy()
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
                proxy['routes']=[{'backend':route.backend,'domain':name,'default':False} for route in routes if route.frontend==proxy['name'] for name in route.hostnames]
                proxy['routes'] += [{'backend':rule['default'],'default':True} for rule in active if rule['default']]
                proxy['domains']=[name for route in routes if route.frontend==proxy['name'] for name in route.hostnames]
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
    from backend.proxy_options import option_lines
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
                options=t[3:]
                verify=options[options.index('verify')+1]!='none' if 'verify' in options else s.server_verify
                servers.append(ImportedServer(name=t[1],address=address,port=port,weight=weight,tls_verify=verify,tls='ssl' in t[3:] or s.server_tls and 'no-ssl' not in t[3:]))
            except (ValueError,IndexError):warnings.append(f'{s.name}: Server {t[1] if len(t)>1 else "?"} bleibt unverändert im Texteditor.')
        # Ambiguous server names cannot be edited safely.
        duplicates={sv.name for sv in servers if sum(other.name==sv.name for other in servers)>1}
        if duplicates:
            warnings.append(f'{s.name}: Doppelte Servernamen bleiben im Texteditor.')
            servers=[sv for sv in servers if sv.name not in duplicates]
        if servers:
            try:backends.append(ImportedBackend(name=s.name,mode=s.mode if s.mode in ('http','tcp') else 'unknown',balance=balance,servers=servers,proxy_options=[text for _,text in option_lines(lines,s)]))
            except ValueError:warnings.append(f'{s.name}: Komplexer Backend-Name bleibt im Texteditor.')
    return backends,warnings


def import_config(config, active_hash, maps=None, sources=None, map_hashes=None):
    from backend.schemas import Document
    from backend.basic_auth import restore_routes
    from backend.tls_bindings import restore_document
    from backend.access_control import restore_document as restore_access,strip_managed as check_access
    check_access(config)  # Refuse manually altered managed restrictions.
    recalled,recall_warnings=recalled_document(config,active_hash,maps,sources,map_hashes)
    proxies=inventory(config)
    if recalled is not None:
        return {'document':recalled.model_dump(),'proxies':proxies,'warnings':recall_warnings,
                'summary':{'frontends':sum(p['kind'] in ('frontend','listen') for p in proxies),
                           'backends':sum(p['kind'] in ('backend','listen') for p in proxies),
                           'editable_backends':len(recalled.imported_backends)+len(recalled.backends)+sum(h.enabled for h in recalled.hosts),
                           'editable_servers':sum(len(b.servers) for b in recalled.imported_backends+recalled.backends)+sum(len(h.servers) for h in recalled.hosts if h.enabled),
                           'tcp':sum(p['mode']=='tcp' for p in proxies),'routes':len(recalled.imported_routes),'converted_maps':0,
                           'managed_hosts':len(recalled.hosts),'restored_document':True}}
    config=strip_document_metadata(config)
    hosts,_=recover_tool_hosts(config,maps)
    owned={'backend_'+h.id for h in hosts}
    backends,warnings=extract_backends(config)
    backends=[b for b in backends if b.name not in owned]
    warnings=recall_warnings+warnings
    rules,routes,map_warnings=map_routes(config,maps or [])
    known={p['name'] for p in proxies if p['kind'] in ('backend','listen')}
    for route in routes:
        if route.backend not in known:warnings.append(f'{route.domain}: Backend {route.backend} fehlt in den eingelesenen Dateien.')
    for rule in rules:
        if rule['default'] and rule['default'] not in known:warnings.append(f'{rule["frontend"]}: Fallback {rule["default"]} fehlt in den eingelesenen Dateien.')
    return {'document':restore_access(restore_document(restore_routes(Document(imported_config=config,imported_active_hash=active_hash,hosts=hosts,imported_managed_hosts=hosts,imported_backends=backends,
                               imported_routes=routes,imported_route_frontends=list(dict.fromkeys(r['frontend'] for r in rules)),imported_maps=maps or [],imported_sources=sources or [],
                               imported_map_hashes=map_hashes or [])))).model_dump(),
            'proxies':proxies,'warnings':warnings+map_warnings,
            'summary':{'frontends':sum(p['kind'] in ('frontend','listen') for p in proxies),
                       'backends':sum(p['kind'] in ('backend','listen') for p in proxies),
                       'editable_backends':len(backends)+len(hosts),'editable_servers':sum(len(b.servers) for b in backends)+sum(len(h.servers) for h in hosts),
                       'tcp':sum(p['mode']=='tcp' for p in proxies),'routes':len(routes),'converted_maps':sum(bool(r['path']) for r in rules),'managed_hosts':len(hosts),'restored_document':False}}


def generate_imported(doc):
    from backend.proxy_options import patch_options
    from backend.backend_delete import remove_sections
    original=remove_original_tool_hosts(strip_document_metadata(doc.imported_config),doc.imported_managed_hosts,[m.model_dump() for m in doc.imported_maps])
    baseline,_=extract_backends(original)
    if doc.rules:raise ValueError('Übernommene Konfiguration: allgemeine Regeln im Texteditor ergänzen.')
    removed=set(doc.removed_backends)
    if {b.name for b in doc.imported_backends}!={b.name for b in baseline if b.name not in removed}:
        raise ValueError('Backend-Pools über die Löschfunktion entfernen; neue Pools unter Frontends & Backends anlegen.')
    lines,sections=parse_sections(original); lookup={b.name:b for b in baseline}; patch={}
    for edited in doc.imported_backends:
        old=lookup[edited.name]; section=next(s for s in sections if s.kind in ('backend','listen') and s.name==edited.name)
        if edited.mode!=old.mode or {s.name for s in old.servers}!={s.name for s in edited.servers}:
            raise ValueError('Modus und Servernamen einer übernommenen Konfiguration im Texteditor ändern.')
        # None means keep the raw algorithm, including one recognized only by
        # a newer parser since this draft was imported (for example "first").
        if edited.balance is not None and edited.balance!=old.balance:
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
            verify=before.tls_verify if server.tls_verify is None else server.tls_verify
            if verify!=before.tls_verify and not server.tls:raise ValueError('Zertifikatsprüfung kann nur für eine TLS-Verbindung geändert werden.')
            if server==before:continue
            index=next(i for i,t in section.lines if t[0]=='server' and t[1]==server.name)
            line=lines[index]; match=re.match(r'(\s*server\s+\S+\s+)(\S+)([^\r\n]*)(\r?\n)?$',line)
            addr=f'[{server.address}]:{server.port}' if ':' in server.address else f'{server.address}:{server.port}'
            tail=match[3];body,sep,comment=tail.partition('#')
            if server.weight!=before.weight:
                if re.search(r'\sweight\s+\d+(?=\s|$)',body):body=re.sub(r'(\sweight\s+)\d+(?=\s|$)',lambda m:m[1]+str(server.weight),body,count=1)
                else:body=body.rstrip()+' weight '+str(server.weight)+(' ' if sep else '')
            if verify!=before.tls_verify:
                pattern=r'(?<!\S)verify\s+(?:none|required)(?=\s|$)'
                value='verify '+('required' if verify else 'none')
                if re.search(pattern,body):body=re.sub(pattern,value,body,count=1)
                else:body=body.rstrip()+' '+value+(' ' if sep else '')
                if verify and not re.search(r'(?<!\S)ca-file\s+',body) and not section.server_ca_file:
                    body=body.rstrip()+' ca-file /etc/ssl/certs/ca-certificates.crt'+(' ' if sep else '')
            patch[index]=match[1]+addr+body+sep+comment+(match[4] or '')
        if edited.proxy_options!=old.proxy_options:
            if edited.mode!="http":raise ValueError("Proxy-Optionen benötigen einen HTTP-Backend-Pool.")
            patch_options(lines,section,edited.proxy_options or [],patch)
    rules,_,_=map_routes(original,[m.model_dump() for m in doc.imported_maps])
    valid_fronts={rule['frontend'] for rule in rules if rule['frontend'] not in removed};back_names={s.name for s in sections if s.kind in ('backend','listen') and s.name not in removed}
    back_names.update(b.name for b in doc.backends if b.mode=='http')
    if any(r.frontend not in valid_fronts or r.backend not in back_names for r in doc.imported_routes):
        raise ValueError('Domain-Routen müssen einen eingelesenen Frontend- und Backend-Namen verwenden.')
    for rule in rules:
        if rule['frontend'] in removed:continue
        routes=[r for r in doc.imported_routes if r.frontend==rule['frontend']]
        names=[name for r in routes for name in r.hostnames]
        if len(set(names))!=len(names):raise ValueError('Doppelte Domain im selben Frontend.')
        index=rule['index'];ending='\r\n' if lines[index].endswith('\r\n') else '\n'
        indent=re.match(r'\s*',lines[index])[0]
        entries=[]
        for route in routes:
            acl='mgmt_'+route.id
            if any(i not in rule.get('indexes',[]) and re.search(r'\bacl\s+'+re.escape(acl)+r'\b',line) for i,line in enumerate(lines)):
                raise ValueError('ACL-Namenskonflikt; Domain-ID ändern.')
            entries += [indent+f'acl {acl} hdr(host) -i '+ ' '.join(route.hostnames),indent+f'use_backend {route.backend} if {acl}']
        if rule['default'] and rule['default'] not in removed:entries.append(indent+'use_backend '+rule['default'])
        patch[index]=ending.join(entries)+ending
        for other in rule.get('indexes',[]):
            if other!=index:patch[other]=''
    result=''.join(patch.get(index,line) for index,line in enumerate(lines) if not line.startswith(MIGRATION_PREFIX))
    result=remove_sections(result,removed)
    if doc.imported_sources:
        context={'files':[s.model_dump() for s in doc.imported_sources],'maps':[s.model_dump() for s in doc.imported_map_hashes]}
        result=result.rstrip('\r\n')+'\n'+MIGRATION_PREFIX+base64.b64encode(json.dumps(context,separators=(',',':')).encode()).decode()+'\n'
    return result


def strip_document_metadata(config):
    return ''.join(line for line in config.splitlines(keepends=True) if not line.startswith(GRAPH_PREFIX))


def document_fingerprint(config):
    # File bundles append consolidation stubs; migration hashes change on apply.
    lines=[line for line in strip_document_metadata(config).splitlines(keepends=True)
           if not line.startswith(MIGRATION_PREFIX) and not re.fullmatch(r'# Consolidated into /[^\r\n]+ by HAProxy Control\r?\n?',line)]
    return hashlib.sha256(''.join(lines).rstrip('\r\n').encode()).hexdigest()


def remember_document(config,doc):
    if not (doc.imported_config is None or doc.hosts or doc.frontends or doc.backends or doc.rules or doc.removed_backends or any(r.access_policy for r in doc.imported_routes)):return strip_document_metadata(config)
    snapshot=doc.model_dump(exclude={'version','imported_active_hash','imported_sources','imported_map_hashes'})
    if snapshot['imported_config'] is not None:snapshot['imported_config']=strip_document_metadata(snapshot['imported_config'])
    payload={'document':snapshot,'fingerprint':document_fingerprint(config)}
    encoded=base64.b64encode(zlib.compress(json.dumps(payload,sort_keys=True,separators=(',',':')).encode(),9)).decode()
    return strip_document_metadata(config).rstrip('\r\n')+'\n'+GRAPH_PREFIX+encoded+'\n'


def recalled_document(config,active_hash,maps,sources,map_hashes):
    from backend.schemas import Document
    values=[line[len(GRAPH_PREFIX):] for line in config.splitlines() if line.startswith(GRAPH_PREFIX)]
    if not values:return None,[]
    try:
        if len(values)!=1 or len(values[0])>1400000:raise ValueError()
        decoder=zlib.decompressobj();raw=decoder.decompress(base64.b64decode(values[0],validate=True),4*1024*1024+1)
        if len(raw)>4*1024*1024 or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:raise ValueError()
        payload=json.loads(raw)
        if not isinstance(payload,dict) or set(payload)!={'document','fingerprint'}:raise ValueError()
        if not isinstance(payload['document'],dict) or not re.fullmatch(r'[a-f0-9]{64}',payload['fingerprint']):raise ValueError()
        if payload['fingerprint']!=document_fingerprint(config):
            return None,['Die Konfiguration wurde außerhalb des grafischen Entwurfs geändert. Erkennbare Tool-Hosts werden rekonstruiert; weitere Änderungen bleiben im importierten Text.']
        value=payload['document']
        if value.get('imported_config') is not None:
            old_maps={m['path']:m['content'] for m in value.get('imported_maps',[])}
            if any(m['path'] in old_maps and m['content']!=old_maps[m['path']] for m in maps or []):
                return None,['Eine Host-Map wurde extern geändert. Die aktuellen Dateien werden eingelesen; gespeicherte Domain-Zuordnungen werden nicht darüber geschrieben.']
            value.update(imported_active_hash=active_hash,imported_sources=sources or [],imported_map_hashes=map_hashes or [])
        return Document.model_validate(value),[]
    except (ValueError,TypeError,KeyError,AttributeError,RecursionError,zlib.error):
        return None,['Die gespeicherte grafische Zuordnung ist ungültig. Die vorhandene Konfiguration wird anhand ihrer tatsächlichen Regeln eingelesen.']


def recover_tool_hosts(config,maps=None):
    """Recognize only complete generated host rules and owned backend pools."""
    from backend.basic_auth import strip_managed,read_metadata
    from backend.tls_bindings import read as tls_plans
    from backend.schemas import Host,BackendServer
    from backend.generator import host_acl,backend_tls,address
    from backend.proxy_options import option_lines
    original_lines,original_sections=parse_sections(config)
    clean=strip_managed(config);lines,sections=parse_sections(clean)
    pools,_=extract_backends(clean);pools={p.name:p for p in pools}
    auth=read_metadata(config) or {};plans=tls_plans(config)
    result=[];remove=set()
    for front in sections:
        if front.kind not in ('frontend','listen') or front.mode!='http':continue
        for _,rule in front.lines:
            if len(rule)!=5 or rule[0]!='use_backend' or rule[2]!='if' or not rule[1].startswith('backend_'):continue
            id=rule[1][8:];acl='host_'+id;path_acl='path_'+id
            if rule[3:]!=[acl,path_acl] or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}',id):continue
            pool=pools.get(rule[1]);section=next((s for s in sections if s.kind=='backend' and s.name==rule[1]),None)
            if not pool or not section or pool.mode!='http' or not pool.balance:continue
            if sum(any(re.search(r'(?<![a-zA-Z0-9_.-])'+re.escape(rule[1])+r'(?![a-zA-Z0-9_.-])',token) for token in t) for s in sections for _,t in s.lines)!=1:continue
            if any(rule[1] in m['content'].split() for m in maps or []):continue
            host_rules=[t for _,t in front.lines if t[:2]==['acl',acl]]
            paths=[t for _,t in front.lines if t[:2]==['acl',path_acl]]
            if len(paths)!=1 or len(paths[0])!=4 or paths[0][2]!='path_beg':continue
            names=[]
            for t in host_rules:
                if t[2:4]==['hdr(host),field(1,:)','-i']:names+=t[4:]
                elif t[2:6]==['hdr(host),field(1,:)','-m','end','-i']:names+=['*'+v for v in t[6:] if v.startswith('.')]
                elif t[2:4]==['hdr_end(host),field(1,:)','-i']:names+=['*'+v for v in t[4:] if v.startswith('.')]
            if not names:continue
            redirect=['http-request','redirect','scheme','https','code','301','if',acl,path_acl,'!{','ssl_fc','}']
            redirects=[t for _,t in front.lines if t==redirect or t==redirect+['!acme_challenge']]
            if len(redirects)>1:continue
            expected=host_rules+paths+[rule]+redirects
            if any((acl in t or path_acl in t) and t not in expected for s in sections for _,t in s.lines):continue
            if any(acl in t or path_acl in t for s in sections if s is not front for _,t in s.lines):continue
            try:
                servers=[BackendServer(**s.model_dump(exclude={'name'})) for s in pool.servers]
                host=Host(id=id,frontend=front.name,domain=names[0],aliases=names[1:],path=paths[0][3],force_https=bool(redirects),balance=pool.balance,servers=servers,proxy_options=pool.proxy_options or [])
                generated=[shlex.split(line) for line in host_acl(host)]
                legacy=[t[:2]+['hdr(host),field(1,:)','-m','end','-i']+t[4:] if t[2:4]==['hdr_end(host),field(1,:)','-i'] else t for t in host_rules]
                if generated!=legacy:continue
                expected_pool=[['balance',pool.balance]]+[['server','srv_'+str(i+1),address(s.address,s.port),'weight',str(s.weight),'check']+shlex.split(backend_tls(s)) for i,s in enumerate(servers)]
                option_indexes={i for i,_ in option_lines(lines,section)}
                actual=[t for i,t in section.lines if t!=['mode','http'] and i not in option_indexes]
                if actual!=expected_pool:continue
                # Do not silently discard user comments or custom directives.
                original_front=next(s for s in original_sections if s.kind==front.kind and s.name==front.name)
                indexes=[i for i,t in original_front.lines if t in expected]
                if any('#' in original_lines[i] for i in indexes):continue
                sites=[s for s in auth.get('sites',[]) if s['kind']=='host' and s['backend']==rule[1] and s['frontend']==front.name and s['path']==host.path]
                settings={(s['group'],s['forward']) for s in sites}
                if len(settings)>1:continue
                if settings:host.basic_auth_group,host.basic_auth_forward=next(iter(settings))
                certificates={s['certificate'] for p in plans if p['frontend']==front.name for s in p['sites'] if s['domain'] in host.hostnames}
                if len(certificates)>1:continue
                if certificates:host.certificate=next(iter(certificates))
                original_pool=next(s for s in original_sections if s.kind=='backend' and s.name==rule[1])
            except (ValueError,StopIteration):continue
            result.append(host);remove.update(indexes);remove.update(i for i in range(original_pool.start,original_pool.end) if not original_lines[i].lstrip().startswith('#') or original_lines[i].strip().startswith(('# haproxy-control-basic-auth BEGIN ','# haproxy-control-basic-auth END ')))
    return result,remove


def remove_original_tool_hosts(config,hosts,maps=None):
    if not hosts:return config
    recognized,indexes=recover_tool_hosts(config,maps)
    expected={h.id for h in hosts}
    if {h.id for h in recognized}!=expected:raise ValueError('Ursprüngliche Tool-Hosts wurden im Text verändert. Konfiguration erneut einlesen.')
    return ''.join(line for index,line in enumerate(config.splitlines(keepends=True)) if index not in indexes)
