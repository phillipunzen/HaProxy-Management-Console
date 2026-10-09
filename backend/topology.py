"""Read-only graph of active routing. Never persists runtime detail or secrets."""
import hashlib
import math
import re
import shlex
from collections import Counter
from datetime import datetime, timezone

from backend.haproxy_config import MAP_ROUTE, inventory, parse_sections
from backend.schemas import DOMAIN


def node_id(*parts):
    return hashlib.sha256('\0'.join(parts).encode()).hexdigest()[:24]


def number(value):
    if value is None or value == '' or isinstance(value, bool):return None
    try:
        result=int(value) if isinstance(value, (int, str)) and re.fullmatch(r'\d+', str(value)) else float(value)
        return result if math.isfinite(result) and 0 <= result <= 2**64-1 else None
    except (ValueError, TypeError, OverflowError):return None


def counters(row):
    # CSV has req_rate on frontends only; rate is always a session rate.
    return {key:number(row.get(field)) for key,field in (
        ('sessions','scur'),('session_rate','rate'),('http_rate','req_rate'),
        ('http_total','req_tot'),('bytes_in','bin'),('bytes_out','bout'),
        ('errors','hrsp_5xx'),('response_ms','rtime'))}


def acl_sites(tokens):
    if len(tokens)<4:return []
    fetch=tokens[2]
    if not re.fullmatch(r'(?:req\.)?hdr(?:_end)?\(host\)(?:,field\(1,:\))?',fetch):return []
    # Only plain literal host matches; -f, regexes and negated matches remain rules.
    values=tokens[3:]
    suffix=fetch.startswith(('hdr_end','req.hdr_end'))
    if values[:2]==['-m','end']:
        suffix=True;values=values[2:]
    if not values or values[0]!='-i' or any(v.startswith('-') for v in values[1:]):return []
    result=[]
    for value in values[1:]:
        value=value.lower()
        if suffix and value.startswith('.'):value='*'+value
        if not DOMAIN.fullmatch(value):return []
        result.append(value)
    return result


def build(config, maps, data):
    lines,sections=parse_sections(config or '')
    metadata={(p['kind'],p['name']):p for p in inventory(config or '')}
    nodes={};edges={};warnings=[];map_values={m['path']:m['content'] for m in (maps or [])}
    rows={}
    for row in data.get('rows',[]):
        role={'0':'frontend','1':'backend','2':'server','3':'listener'}.get(str(row.get('type')))
        role=role or ('frontend' if row.get('svname')=='FRONTEND' else 'backend' if row.get('svname')=='BACKEND' else 'server')
        if role!='listener':rows[(role,row.get('pxname'),row.get('svname'))]=row

    def add(kind,name,proxy,mode,service='',address='',**extra):
        key=node_id(kind,proxy,name)
        row=rows.get((kind,proxy,'FRONTEND' if kind=='frontend' else 'BACKEND' if kind=='backend' else name),{})
        nodes[key]={'id':key,'kind':kind,'name':name,'proxy':proxy,'mode':mode,
                    'service':service,'address':address,'status':row.get('status') or 'UNKNOWN',
                    'metrics':counters(row),'runtime':bool(row),**extra}
        return key

    def edge(source,target,measured=False):
        key=node_id(source,target)
        edges[key]={'id':key,'source':source,'target':target,'measured':measured}

    for s in sections:
        if s.kind not in ('frontend','backend','listen'):continue
        meta=metadata[(s.kind,s.name)]
        if s.kind in ('frontend','listen'):
            binds=[t[1] for _,t in s.lines if t[0]=='bind' and len(t)>1]
            add('frontend',s.name,s.name,s.mode,meta['service'],' · '.join(binds),binds=binds)
        if s.kind in ('backend','listen'):
            local_response=s.mode=='http' and not any(t[0] in ('server','server-template') for _,t in s.lines) and any(t[:2] in (['http-request','return'],['http-request','deny']) for _,t in s.lines)
            b=add('backend',s.name,s.name,s.mode,'HTTP-Antwort / Zugriffskontrolle' if local_response else meta['service'],balance=s.balance,local_response=local_response)
            static_names=set()
            for _,t in s.lines:
                if t[0]=='server-template':warnings.append(f'{s.name}: Dynamische Server werden aus der Runtime ergänzt.')
                if t[0]!='server' or len(t)<3:continue
                static_names.add(t[1]);row=rows.get(('server',s.name,t[1]),{})
                server=add('server',t[1],s.name,s.mode,'Zielserver',row.get('addr') or t[2],
                           configured_address=t[2],tls='ssl' in t[3:] or s.server_tls and 'no-ssl' not in t[3:],backup='backup' in t[3:])
                edge(b,server,True)
            for (role,proxy,name),row in rows.items():
                if role=='server' and proxy==s.name and name not in static_names:
                    edge(b,add('server',name,s.name,s.mode,'Dynamischer Zielserver',row.get('addr') or ''),True)

    def route(s,index,label,target=None,condition='',domain='',path='',**extra):
        key=node_id('route',s.name,str(index),label,target or '')
        nodes[key]={'id':key,'kind':'route','name':label,'proxy':s.name,'mode':s.mode,
                    'service':metadata[(s.kind,s.name)]['service'],'address':'','status':'UNKNOWN',
                    'metrics':{},'runtime':False,'condition':condition[:500],'domain':domain,'path':path,**extra}
        edge(node_id('frontend',s.name,s.name),key)
        if target:
            b=node_id('backend',target,target)
            if b not in nodes:
                add('backend',target,target,s.mode,'Backend nicht in Konfiguration',missing=True)
                warnings.append(f'{s.name}: Backend {target} fehlt in der gelesenen Konfiguration.')
            edge(key,b)

    for s in sections:
        if s.kind not in ('frontend','listen'):continue
        acls={}
        for _,t in s.lines:
            if t[0]=='acl' and len(t)>2:acls.setdefault(t[1],[]).append(t)
        count=0
        for index,t in s.lines:
            if t[0] not in ('use_backend','default_backend') or len(t)<2:continue
            count+=1;target=t[1]
            # Inline ACLs may compare credentials. Expose named ACL references,
            # but leave arbitrary inline values in the privileged text editor.
            condition='Inline-Bedingung (Details im Konfigurationseditor)' if '{' in t[2:] else ' '.join(t[2:])
            match=MAP_ROUTE.fullmatch(lines[index].rstrip('\r\n'))
            if match:
                map_path,default=match.groups();valid_entries=[]
                if map_path not in map_values:warnings.append(f'{s.name}: Host-Map nicht verfügbar; Domain-Zuordnung unvollständig.')
                else:
                    for value in map_values[map_path].splitlines():
                        try:entry=shlex.split(value,comments=True)
                        except ValueError:entry=[]
                        if len(entry)==2 and DOMAIN.fullmatch(entry[0]) and entry[0]==entry[0].lower() and not entry[0].startswith('*.') and re.fullmatch(r'[a-zA-Z0-9_.-]+',entry[1]):valid_entries.append(entry)
                        elif entry:warnings.append(f'{s.name}: Komplexer Map-Eintrag wird nicht als Domain interpretiert.')
                    duplicates={domain for domain,count in Counter(domain for domain,_ in valid_entries).items() if count>1}
                    if duplicates:
                        warnings.append(f'{s.name}: Doppelte Map-Schlüssel werden nicht als eindeutige Domain-Zuordnung dargestellt.')
                        valid_entries=[entry for entry in valid_entries if entry[0] not in duplicates]
                for domain,backend in valid_entries:route(s,index,domain,backend,domain=domain,source='map')
                route(s,index,'Standardroute',default,default=True)
                continue
            if not re.fullmatch(r'[a-zA-Z0-9_.-]+',target):
                route(s,index,'Dynamische Backend-Regel',condition=condition,dynamic=True)
                warnings.append(f'{s.name}: Dynamische Backend-Auswahl kann nicht statisch aufgelöst werden.')
                continue
            domains=[];path=''
            if len(t)>3 and t[2]=='if' and all(token in acls for token in t[3:]):
                # Multiple host ACLs joined with AND can be ambiguous; keep them a rule.
                groups=[]
                for token in t[3:]:
                    matches=[acl_sites(acl) for acl in acls[token]]
                    groups.append(list(dict.fromkeys(name for values in matches for name in values)) if all(matches) else [])
                groups=[g for g in groups if g]
                if len(groups)==1:domains=groups[0]
                for token in t[3:]:
                    values=acls[token]
                    if len(values)==1 and len(values[0])==4 and values[0][2]=='path_beg':path=values[0][3]
            if domains:
                for domain in domains:route(s,index,domain+(path if path and path!='/' else ''),target,condition,domain,path)
            else:route(s,index,'Standardroute' if t[0]=='default_backend' else target,target,condition,default=t[0]=='default_backend')
        if s.kind=='listen' and any(t[0] in ('server','server-template') for _,t in s.lines):
            route(s,-1,s.name,s.name);count+=1
        if not count:route(s,-1,metadata[(s.kind,s.name)]['service'],terminal=True)

    if not config:
        warnings.append('Aktive Konfiguration nicht verfügbar; nur Runtime-Knoten, keine erfundenen Routen.')
        for (kind,proxy,name),row in rows.items():
            add(kind,proxy if kind!='server' else name,proxy,row.get('mode') or 'unknown',address=row.get('addr') or '')
            if kind=='server' and ('backend',proxy,'BACKEND') in rows:edge(node_id('backend',proxy,proxy),node_id('server',proxy,name),True)
    return {'nodes':list(nodes.values()),'edges':list(edges.values()),'warnings':list(dict.fromkeys(warnings)),
            'online':bool(data.get('online')),'captured_at':datetime.now(timezone.utc).isoformat(),
            'uptime_seconds':number(data.get('info',{}).get('Uptime_sec')),'poll_seconds':10}
