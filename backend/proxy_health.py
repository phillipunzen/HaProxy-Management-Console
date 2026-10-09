"""Live backend health for draft routes; no storage and no probes of arbitrary URLs."""
from datetime import datetime, timezone

from backend.haproxy_config import extract_backends, parse_sections
from backend.topology import build as topology, number


def server_signature(server):
    # Verification does not affect cleartext targets; None is a legacy policy.
    return (server.address.lower(), server.port, server.weight, server.tls,
            server.tls_verify if server.tls else None)


def target(row, name, address='', backup=False):
    status=str(row.get('status') or 'UNKNOWN')[:80]
    state=status.split(' ', 1)[0].upper()
    check=str(row.get('check_status') or '')[:80]
    checked=bool(check and check not in ('INI', 'START', 'UNK'))
    weight=number(row.get('weight'))
    if not row:health='unknown'
    elif state.startswith('MAINT'):health='maintenance'
    elif state in ('DRAIN', 'NOLB') or state=='UP' and weight==0:health='draining'
    elif state=='DOWN':health='down'
    elif state=='UP':health='up' if checked else 'unknown' if check else 'unchecked'
    else:health='unknown'
    return {'name':name, 'address':str(row.get('addr') or address)[:300],
            'status':status, 'state':health, 'checked':checked,
            'backup':str(row.get('bck'))=='1' or backup,
            'check_status':check or None, 'check_code':number(row.get('check_code')),
            'check_duration_ms':number(row.get('check_duration')),
            'check_description':str(row.get('check_desc') or '')[:500]}


def pool_health(servers, local_response=False):
    total=len(servers);available=sum(s['state']=='up' for s in servers)
    result={'state':'unknown','available':available,'total':total,'targets':servers}
    states=[s['state'] for s in servers]
    if not servers:
        if local_response:result.update(state='local',reason='HAProxy erzeugt hier eine lokale Antwort ohne Zielserver.')
        else:result['reason']='Keine Zielserver oder Runtime-Daten für diesen Pool verfügbar.'
    elif available:
        # Backup targets count for availability, and are explicitly identified below.
        result['state']='up' if available==total else 'partial'
        result['reason']=f'{available} von {total} Zielen meldet HAProxy als erreichbar.'
    elif 'unknown' in states:
        result['reason']='Healthcheck-Daten fehlen oder der erste Check läuft noch.'
    elif 'unchecked' in states:
        result.update(state='unchecked',reason='Mindestens ein Ziel hat keinen laufenden HAProxy-Healthcheck. Erreichbarkeit ist nicht bestätigt.')
    elif 'maintenance' in states or 'draining' in states:
        result.update(state='maintenance',reason='Kein Ziel nimmt neue Verbindungen an; Wartung oder Drain ist aktiv.')
    else:result.update(state='down',reason='Alle Zielserver werden von HAProxy als DOWN gemeldet.')
    return result


def build(doc, config, maps, data, captured_at=None):
    captured_at=captured_at or datetime.now(timezone.utc).isoformat()
    result={'online':bool(data.get('online')), 'captured_at':captured_at,
            'poll_seconds':10, 'hosts':{}, 'routes':{}}
    graph=topology(config, maps, {'rows':[], 'online':True}) if config is not None else None
    # The graph understands generated host/path ACLs and imported host maps alike.
    nodes={n['id']:n for n in graph['nodes']} if graph else {}
    edges={e['source']:e['target'] for e in graph['edges'] if nodes[e['source']]['kind']=='route'} if graph else {}
    active_routes={(n['proxy'],n.get('domain'),n.get('path') or '/',nodes[edges[n['id']]]['name'])
                   for n in nodes.values() if n['kind']=='route' and n.get('domain') and n['id'] in edges}
    active_backends={b.name:b for b in extract_backends(config)[0]} if config is not None else {}
    active_sections={s.name:s for s in parse_sections(config or '')[1] if s.kind in ('backend','listen')}
    backend_nodes={n['name']:n for n in nodes.values() if n['kind']=='backend'}
    rows={};fronts={}
    for row in data.get('rows',[]):
        role=str(row.get('type') or '')
        if role=='0' or not role and row.get('svname')=='FRONTEND':fronts[row.get('pxname')]=row
        elif role=='2' or not role and row.get('svname') not in ('FRONTEND','BACKEND'):
            rows[(row.get('pxname'),row.get('svname'))]=row
    pools={}
    for name,section in active_sections.items():
        servers={t[1]:target(rows.get((name,t[1]),{}),t[1],t[2], 'backup' in t[3:])
                 for _,t in section.lines if t[0]=='server' and len(t)>=3}
        servers.update({server:target(row,server,servers.get(server,{}).get('address',''),servers.get(server,{}).get('backup',False))
                        for (pool,server),row in rows.items() if pool==name})
        pools[name]=pool_health(list(servers.values()),backend_nodes.get(name,{}).get('local_response',False))
    draft_pools={b.name:b for b in doc.imported_backends}

    def health(frontend, names, path, backend, desired=None, enabled=True, exclusive=False):
        base={'backend':backend,'state':'unknown','available':0,'total':0,'targets':[]}
        if not data.get('online'):
            return base|{'reason':'Agent oder HAProxy-Runtime nicht erreichbar. Dienstzustand ist unbekannt.'}
        if config is None:return base|{'reason':'Aktive Konfiguration konnte nicht gelesen werden.'}
        matches=all((frontend,name,path,backend) in active_routes for name in names)
        if exclusive:
            active_names={domain for front,domain,active_path,pool in active_routes if (front,active_path,pool)==(frontend,path,backend)}
            matches=matches and active_names==set(names)
        if not enabled:
            still_active=any(pool==backend for _,_,_,pool in active_routes) if exclusive else matches
            return base|{'state':'pending' if still_active else 'disabled', 'reason':
                        'Deaktivierung ist noch nicht angewendet.' if still_active else 'Dieser Eintrag ist im Entwurf deaktiviert.'}
        if not matches:
            incomplete=any('Host-Map nicht verfügbar' in w for w in (graph or {}).get('warnings',[]))
            return base|{'state':'unknown' if incomplete else 'pending','reason':
                        'Domain-Zuordnung kann ohne die referenzierte Host-Map nicht bestätigt werden.' if incomplete else
                        'Diese Domain-/Pfad-Zuordnung ist noch nicht in der aktiven Konfiguration.'}
        if desired is not None:
            active=active_backends.get(backend)
            active_targets={s.name:server_signature(s) for s in active.servers} if active else {}
            # Legacy imported TLS policies retain the currently effective value.
            for name,s in desired.items():
                wanted=server_signature(s);existing=active_targets.get(name)
                if wanted[-1] is None and s.tls and existing:wanted=wanted[:-1]+existing[-1:]
                if existing!=wanted:
                    return base|{'state':'pending','reason':'Geänderte Backend-Ziele sind noch nicht angewendet.'}
            if set(desired)!=set(active_targets):
                return base|{'state':'pending','reason':'Die Zielserver im Entwurf unterscheiden sich vom aktiven Pool.'}
        front=fronts.get(frontend)
        if not front:return base|{'reason':'Frontend fehlt in den aktuellen Runtime-Daten.'}
        if front.get('status')!='OPEN':
            return base|{'state':'frontend_down','reason':'Das zugehörige Frontend nimmt keine Verbindungen an.'}
        return base|pools.get(backend,{'reason':'Backend fehlt in der aktiven Konfiguration.'})

    for host in doc.hosts:
        result['hosts'][host.id]=health(host.frontend,host.hostnames,host.path,'backend_'+host.id,
                                       {f'srv_{n+1}':s for n,s in enumerate(host.servers)},host.enabled,exclusive=True)
    for route in doc.imported_routes:
        pool=draft_pools.get(route.backend)
        result['routes'][route.id]=health(route.frontend,route.hostnames,'/',route.backend,
                                         {s.name:s for s in pool.servers} if pool else None)
    return result
