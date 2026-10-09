"""Extend generated or imported configurations with explicit listeners and pools."""
import re
from pathlib import PurePosixPath
from backend.haproxy_config import parse_sections
from backend import tls_bindings


def endpoint(host,port):return f'[{host}]:{port}' if ':' in host else f'{host}:{port}'


def servers(values):
    from backend.generator import backend_tls
    result=[]
    for index,s in enumerate(values):
        tls=backend_tls(s)
        result.append(f'    server srv_{index+1} {endpoint(s.address,s.port)} weight {s.weight} check{tls}')
    return result


def backend(name,mode,balance,values):
    return ['backend '+name,'    mode '+mode,'    balance '+balance]+servers(values)


def enhance(config,doc,cap):
    old=tls_bindings.read(config)
    config=tls_bindings.restore(config)
    sites={}
    for host in doc.hosts:
        if host.enabled and host.certificate:sites.setdefault(host.frontend,[]).extend({'domain':name,'certificate':host.certificate} for name in host.hostnames)
    for route in doc.imported_routes:
        if route.certificate:sites.setdefault(route.frontend,[]).extend({'domain':name,'certificate':route.certificate} for name in route.hostnames)
    edited={(h.frontend,name) for h in doc.hosts for name in h.hostnames}|{(r.frontend,name) for r in doc.imported_routes for name in r.hostnames}
    edited.update((h.frontend,name) for h in doc.imported_managed_hosts for name in h.hostnames)
    if doc.imported_config:
        from backend.haproxy_config import map_routes
        _,original_routes,_=map_routes(doc.imported_config,[m.model_dump() for m in doc.imported_maps])
        edited.update((r.frontend,name) for r in original_routes for name in r.hostnames)
    for plan in old:
        for site in plan['sites']:
            if (plan['frontend'],site['domain']) not in edited:sites.setdefault(plan['frontend'],[]).append(site)
    for name,values in sites.items():
        unique={}
        for site in values:
            if site['domain'] in unique and unique[site['domain']]!=site['certificate']:raise ValueError('Eine Domain kann am selben TLS-Frontend nur ein Zertifikat verwenden, auch bei verschiedenen Pfaden: '+site['domain'])
            unique[site['domain']]=site['certificate']
        sites[name]=[{'domain':domain,'certificate':cert} for domain,cert in sorted(unique.items())]
    certificates={name:list(values) for name,values in doc.frontend_certificates.items() if values}
    for route in doc.imported_routes:
        if route.certificate:certificates.setdefault(route.frontend,[]).append(route.certificate)
    for host in doc.hosts:
        if host.enabled and host.certificate:certificates.setdefault(host.frontend,[]).append(host.certificate)
    for name,values in sites.items():certificates.setdefault(name,[]).extend(site['certificate'] for site in values)
    extra_hosts=[h for h in doc.hosts if h.enabled and (doc.imported_config is not None or h.frontend!='public_http')]
    if not doc.frontends and not doc.backends and not extra_hosts and not certificates:return config
    cert_dir=cap.get('cert_dir_config','')
    if (certificates or any(f.tls_enabled for f in doc.frontends)) and (not cert_dir.startswith('/') or any(c.isspace() for c in cert_dir) or any(c in cert_dir for c in '#;\\\x00')):
        raise ValueError('Agent-Zertifikatspfad muss absolut und ohne Steuerzeichen sein.')
    lines,sections=parse_sections(config)
    names={s.name for s in sections if s.kind in ('frontend','backend','listen')}
    additions=[]
    for pool in doc.backends:
        if pool.name in names:raise ValueError('Backend-Namenskonflikt: '+pool.name)
        names.add(pool.name);additions+=['']+backend(pool.name,pool.mode,pool.balance,pool.servers)
    for front in doc.frontends:
        if front.name in names:raise ValueError('Frontend-Namenskonflikt: '+front.name)
        names.add(front.name)
        bind=endpoint(front.bind_address,front.port)+(' ssl crt '+cert_dir+'/' if front.tls_enabled else '')
        additions+=['','frontend '+front.name,'    mode '+front.mode,'    option '+('tcplog' if front.mode=='tcp' else 'httplog'),'    bind '+bind]
        if front.backend:additions+=['    default_backend '+front.backend]
        elif front.mode=='tcp':raise ValueError('TCP-Frontend benötigt einen Backend-Pool: '+front.name)
        else:additions+=['    http-request return status 404 if !{ hdr(host) -m found }']
    if doc.imported_config is not None:
        for host in doc.hosts:
            if not host.enabled:continue
            name='backend_'+host.id
            if name in names:raise ValueError('Proxy-Host-Backend existiert bereits: '+name)
            names.add(name);additions+=['']+backend(name,'http',host.balance,host.servers)
    if additions:config=config.rstrip()+'\n'+'\n'.join(additions)+'\n'
    lines,sections=parse_sections(config)
    frontends={s.name:s for s in sections if s.kind in ('frontend','listen')}
    backends={s.name:s for s in sections if s.kind in ('backend','listen')}
    for front in doc.frontends:
        if front.backend and (front.backend not in backends or backends[front.backend].mode!=front.mode):
            raise ValueError('Frontend und Backend müssen existieren und denselben HTTP-/TCP-Modus verwenden: '+front.name)
    seen={};patch={};insert={}
    for section in frontends.values():
        for _,tokens in section.lines:
            if tokens[0]!='bind' or len(tokens)<2:continue
            for value in tokens[1].split(','):
                match=re.fullmatch(r'(.*):(\d+)',value)
                if not match:continue
                addr=match[1].strip('[]');port=int(match[2]);wild=addr in ('','*','0.0.0.0','::')
                for old_addr,old_name in seen.get(port,[]):
                    if section.name!=old_name and (wild or old_addr in ('','*','0.0.0.0','::') or addr==old_addr):
                        # Existing multi-bind setups are preserved; new listeners
                        # cannot silently collide with an already used port.
                        if any(f.name in (old_name,section.name) for f in doc.frontends):raise ValueError(f'Port {port} ist bereits durch {old_name} belegt.')
                seen.setdefault(port,[]).append((addr,section.name))
    from backend.generator import host_acl,host_order
    hosts=sorted((h for h in doc.hosts if h.enabled),key=host_order)
    for host in hosts:
        front=frontends.get(host.frontend)
        if not front or front.mode!='http':raise ValueError('Proxy Host benötigt ein vorhandenes HTTP-Frontend: '+host.frontend)
        tls=any(t[0]=='bind' and 'ssl' in t for _,t in front.lines)
        if (host.force_https or host.certificate) and not tls:raise ValueError('Für HTTPS ein Frontend mit TLS auswählen: '+host.frontend)
        # The legacy shared listener already contains its hosts and backends.
        if doc.imported_config is None and host.frontend=='public_http':continue
        entries=host_acl(host)+[f'    acl path_{host.id} path_beg {host.path}']
        if any(re.search(r'\bacl\s+(?:host_|path_)'+re.escape(host.id)+r'\b',line) for line in lines):raise ValueError('Proxy-Host-ACL existiert bereits: '+host.id)
        if host.force_https:entries+=[f'    http-request redirect scheme https code 301 if host_{host.id} path_{host.id} !{{ ssl_fc }}']
        entries+=[f'    use_backend backend_{host.id} if host_{host.id} path_{host.id}']
        index=next((i for i,t in front.lines if t[0] in ('use_backend','default_backend')),front.end)
        insert.setdefault(index,[]).extend(entries)
    plans=[]
    for name,values in certificates.items():
        front=frontends.get(name)
        if not front:raise ValueError('Zertifikats-Frontend fehlt: '+name)
        ssl_binds=[(i,t) for i,t in front.lines if t[0]=='bind' and 'ssl' in t]
        if not ssl_binds:raise ValueError('Zertifikatszuweisung benötigt einen TLS-Bind: '+name)
        for index,tokens in ssl_binds:
            if 'crt-list' in tokens or 'crt-store' in tokens:raise ValueError('Eigene crt-list/crt-store-Zuweisung im Texteditor bearbeiten: '+name)
            line=lines[index].rstrip('\r\n');content,sep,comment=line.partition('#')
            explicit=bool(doc.frontend_certificates.get(name))
            loaded=re.findall(r'\s+crt\s+(\S+)',content)
            if sites.get(name):
                fallback=[cert_dir.rstrip('/')+'/'+v+'.pem' for v in doc.frontend_certificates[name]] if explicit else loaded
                for path in fallback:
                    if not PurePosixPath(tls_bindings.path_ok(path)).is_relative_to(cert_dir.rstrip('/')):raise ValueError('Bestehendes TLS-Zertifikat liegt außerhalb des Agent-Zertifikatsverzeichnisses: '+path)
                plan=tls_bindings.managed_bind(lines[index],name,fallback,sites[name],doc.frontend_certificates.get(name,[]),cert_dir)
                plans.append(plan);patch[index]=plan['managed'];continue
            if explicit:content=re.sub(r'\s+crt\s+\S+','',content);loaded=[]
            paths=[cert_dir.rstrip('/')+'/'+v+'.pem' for v in dict.fromkeys(values)]
            # Site-specific additions must retain a legacy catch-all cert.pem.
            # A directory bind already loads the selected certificate through SNI.
            paths=[path for path in paths if path not in loaded and cert_dir.rstrip('/') not in [v.rstrip('/') for v in loaded]]
            content=content.rstrip()+''.join(' crt '+path for path in paths)
            patch[index]=content+(' #'+comment if sep else '')+'\n'
    result=[]
    for index,line in enumerate(lines):
        if index in insert:result+=['\n'.join(insert[index])+'\n']
        result.append(patch.get(index,line))
    if len(lines) in insert:result+=['\n'.join(insert[len(lines)])+'\n']
    return tls_bindings.append_metadata(''.join(result),plans)
