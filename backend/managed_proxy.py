"""Extend generated or imported configurations with explicit listeners and pools."""
import re
from backend.haproxy_config import parse_sections


def endpoint(host,port):return f'[{host}]:{port}' if ':' in host else f'{host}:{port}'


def servers(values):
    result=[]
    for index,s in enumerate(values):
        tls=' ssl verify required ca-file /etc/ssl/certs/ca-certificates.crt' if s.tls else ''
        if s.tls and ':' not in s.address and not s.address.replace('.','').isdigit():tls+=f' sni str({s.address}) verifyhost {s.address}'
        result.append(f'    server srv_{index+1} {endpoint(s.address,s.port)} weight {s.weight} check{tls}')
    return result


def backend(name,mode,balance,values):
    return ['backend '+name,'    mode '+mode,'    balance '+balance]+servers(values)


def enhance(config,doc,cap):
    certificates={name:list(values) for name,values in doc.frontend_certificates.items() if values}
    for route in doc.imported_routes:
        if route.certificate:certificates.setdefault(route.frontend,[]).append(route.certificate)
    for host in doc.hosts:
        if host.enabled and host.certificate:certificates.setdefault(host.frontend,[]).append(host.certificate)
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
    hosts=sorted((h for h in doc.hosts if h.enabled),key=lambda h:(-len(h.path),h.domain.startswith('*.')))
    for host in hosts:
        front=frontends.get(host.frontend)
        if not front or front.mode!='http':raise ValueError('Proxy Host benötigt ein vorhandenes HTTP-Frontend: '+host.frontend)
        tls=any(t[0]=='bind' and 'ssl' in t for _,t in front.lines)
        if (host.force_https or host.certificate) and not tls:raise ValueError('Für HTTPS ein Frontend mit TLS auswählen: '+host.frontend)
        # The legacy shared listener already contains its hosts and backends.
        if doc.imported_config is None and host.frontend=='public_http':continue
        matcher='hdr_end(host),field(1,:) -i' if host.domain.startswith('*.') else 'hdr(host),field(1,:) -i'
        domain=host.domain[1:] if host.domain.startswith('*.') else host.domain
        entries=[f'    acl host_{host.id} {matcher} {domain}',f'    acl path_{host.id} path_beg {host.path}']
        if any(re.search(r'\bacl\s+(?:host_|path_)'+re.escape(host.id)+r'\b',line) for line in lines):raise ValueError('Proxy-Host-ACL existiert bereits: '+host.id)
        if host.force_https:entries+=[f'    http-request redirect scheme https code 301 if host_{host.id} path_{host.id} !{{ ssl_fc }}']
        entries+=[f'    use_backend backend_{host.id} if host_{host.id} path_{host.id}']
        index=next((i for i,t in front.lines if t[0] in ('use_backend','default_backend')),front.end)
        insert.setdefault(index,[]).extend(entries)
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
    return ''.join(result)
