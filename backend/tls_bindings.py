"""Reversible TLS bind plans; no keys or certificates are stored in metadata."""
import base64
import hashlib
import json
import re
from pathlib import PurePosixPath

from backend.haproxy_config import parse_sections
from backend.schemas import Host

PREFIX='# haproxy-control-tls-v1 '
NAME=re.compile(r'[a-zA-Z0-9_.-]{1,100}')
CERT=re.compile(r'[a-zA-Z0-9_-]{1,80}')


def key(plan):
    return hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def path_ok(value):
    if not isinstance(value,str) or not value.startswith('/') or '..' in PurePosixPath(value).parts or any(c.isspace() or c in '#;\\\x00' for c in value):raise ValueError('TLS-Dateipfad muss absolut und ohne Steuerzeichen sein.')
    return value


def read(config):
    values=[line[len(PREFIX):] for line in config.splitlines() if line.startswith(PREFIX)]
    if not values:return []
    try:
        if len(values)!=1:raise ValueError()
        plans=json.loads(base64.b64decode(values[0],validate=True))
        if not isinstance(plans,list) or not 1<=len(plans)<=500:raise ValueError()
        paths=set()
        for plan in plans:
            if set(plan)!={'frontend','original','fallback','sites','explicit','directory','path','managed'}:raise ValueError()
            if not NAME.fullmatch(plan['frontend']):raise ValueError()
            directory=path_ok(plan['directory']).rstrip('/')
            if not isinstance(plan['fallback'],list) or len(plan['fallback'])>500:raise ValueError()
            for value in plan['fallback']:
                if not PurePosixPath(path_ok(value)).is_relative_to(directory):raise ValueError()
            if not isinstance(plan['sites'],list) or not 1<=len(plan['sites'])<=2200:raise ValueError()
            for site in plan['sites']:
                if set(site)!={'domain','certificate'} or Host.domain_ok(site['domain'])!=site['domain'] or not CERT.fullmatch(site['certificate']):raise ValueError()
            if len({s['domain'] for s in plan['sites']})!=len(plan['sites']):raise ValueError()
            if not isinstance(plan['explicit'],list) or any(not CERT.fullmatch(v) for v in plan['explicit']):raise ValueError()
            base={k:v for k,v in plan.items() if k not in ('path','managed')}
            if plan['path']!=directory+'/.control-tls/'+key(base)+'.list' or plan['path'] in paths:raise ValueError()
            paths.add(plan['path'])
            if any('\n' in plan[k].rstrip('\r\n') or '\r' in plan[k].rstrip('\r\n') or not re.match(r'^\s*bind\s+',plan[k]) for k in ('original','managed')):raise ValueError()
            if 'crt-list '+plan['path'] not in plan['managed']:raise ValueError()
        return plans
    except (ValueError,TypeError,KeyError,AttributeError) as error:raise ValueError('Ungültige TLS-Zuordnungsmetadaten. Konfiguration erneut einlesen.') from error


def restore(config):
    plans=read(config)
    if not plans:return config
    lines,sections=parse_sections(config);patch={}
    for plan in plans:
        matches=[i for s in sections if s.kind in ('frontend','listen') and s.name==plan['frontend'] for i,t in s.lines if lines[i].rstrip('\r\n')==plan['managed'].rstrip('\r\n')]
        if len(matches)!=1:raise ValueError('Verwalteter TLS-Bind wurde manuell geändert. Ursprüngliche TLS-Konfiguration erneut einlesen.')
        patch[matches[0]]=plan['original'].rstrip('\r\n')+'\n'
    return ''.join(patch.get(i,line) for i,line in enumerate(lines) if not line.startswith(PREFIX))


def restore_document(doc):
    for plan in read(doc.imported_config or ''):
        if plan['explicit']:doc.frontend_certificates[plan['frontend']]=plan['explicit']
        for route in doc.imported_routes:
            if route.frontend==plan['frontend']:
                site=next((s for s in plan['sites'] if s['domain']==route.domain),None)
                if site:route.certificate=site['certificate']
    return doc


def managed_bind(original,frontend,fallback,sites,explicit,directory):
    base={'frontend':frontend,'original':original,'fallback':fallback,'sites':sites,'explicit':explicit,'directory':directory.rstrip('/')}
    path=directory.rstrip('/')+'/.control-tls/'+key(base)+'.list'
    content,sep,comment=original.rstrip('\r\n').partition('#')
    content=re.sub(r'\s+crt\s+\S+','',content).rstrip()+' crt-list '+path
    managed=content+(' #'+comment if sep else '')+'\n'
    return base|{'path':path,'managed':managed}


def append_metadata(config,plans):
    if not plans:return config
    return config.rstrip('\r\n')+'\n'+PREFIX+base64.b64encode(json.dumps(plans,sort_keys=True,separators=(',',':')).encode()).decode()+'\n'
