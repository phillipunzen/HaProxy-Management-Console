from backend.schemas import Document

def address(host, port):
    return f'[{host}]:{port}' if ':' in host else f'{host}:{port}'

def backend_tls(server):
    if not server.tls:return ''
    tls=' ssl verify required ca-file /etc/ssl/certs/ca-certificates.crt' if server.tls_verify else ' ssl verify none'
    if ':' not in server.address and not server.address.replace('.','').isdigit():
        tls+=f' sni str({server.address})'
        if server.tls_verify:tls+=f' verifyhost {server.address}'
    return tls

def host_acl(host):
    result=[]
    exact=[name for name in host.hostnames if not name.startswith('*.')]
    wildcard=[name[1:] for name in host.hostnames if name.startswith('*.')]
    if exact:result.append(f'    acl host_{host.id} hdr(host),field(1,:) -i '+ ' '.join(exact))
    if wildcard:result.append(f'    acl host_{host.id} hdr(host),field(1,:) -m end -i '+ ' '.join(wildcard))
    return result

def host_order(host):return (-len(host.path),any(name.startswith('*.') for name in host.hostnames))

def generate(doc: Document, capabilities: dict, basic_auth=None) -> str:
    from backend.basic_auth import inject
    from backend.haproxy_config import remember_document
    from backend.managed_proxy import enhance
    if doc.imported_config is not None:
        from backend.haproxy_config import generate_imported
        return remember_document(inject(enhance(generate_imported(doc),doc,capabilities),doc,basic_auth),doc)
    socket = capabilities['runtime_socket_config']
    cert_dir = capabilities['cert_dir_config']
    for p in (socket, cert_dir):
        if any(x.isspace() for x in p) or not p.startswith('/'):
            raise ValueError('Agent-Pfade müssen absolute Pfade ohne Leerzeichen sein.')
    out = ['# Managed by HAProxy Control', 'global', '    log stdout format raw local0',
           f'    maxconn {doc.maxconn}', f'    stats socket {socket} mode 660 level admin',
           '', 'defaults', '    mode http', '    log global', '    option httplog',
           '    option dontlognull', '    timeout connect 5s', '    timeout client 60s',
           '    timeout server 60s', '', 'frontend public_http', f'    bind :{doc.http_port}']
    if doc.tls_enabled:
        out += [f'    bind :{doc.https_port} ssl crt {cert_dir}/ alpn h2,http/1.1']
    if doc.acme_enabled:
        out += ['    acl acme_challenge path_beg /.well-known/acme-challenge/']
    for rule in doc.rules:
        if not rule.enabled:
            continue
        criterion = {'host':'hdr(host),field(1,:) -i','path_prefix':'path_beg','source_ip':'src','method':'method'}[rule.match]
        out += [f'    acl rule_{rule.id} {criterion} {rule.value}']
        condition = f'rule_{rule.id}' + (' !acme_challenge' if doc.acme_enabled else '')
        if rule.action == 'deny':
            out += [f'    http-request deny if {condition}']
        elif rule.action == 'redirect':
            out += [f'    http-request redirect location {rule.target} code {rule.code} if {condition}']
        else:
            name,value = rule.target.split(':',1)
            out += [f'    http-request set-header {name} {value.strip()} if {condition}']
    hosts = sorted((h for h in doc.hosts if h.enabled and h.frontend=='public_http'), key=host_order)
    for host in hosts:
        out += host_acl(host)+[f'    acl path_{host.id} path_beg {host.path}']
        if host.force_https:
            out += [f'    http-request redirect scheme https code 301 if host_{host.id} path_{host.id} !{{ ssl_fc }}' + (' !acme_challenge' if doc.acme_enabled else '')]
    if doc.acme_enabled:
        out += ['    use_backend acme_webroot if acme_challenge']
    for host in hosts:
        out += [f'    use_backend backend_{host.id} if host_{host.id} path_{host.id}']
    out += ['    default_backend unknown_host', '', 'backend unknown_host', '    http-request deny deny_status 404']
    if doc.acme_enabled:
        out += ['', 'backend acme_webroot', f'    server acme {address(doc.acme_address,doc.acme_port)}']
    for host in (h for h in doc.hosts if h.enabled):
        out += ['', f'backend backend_{host.id}', f'    balance {host.balance}']
        for i, server in enumerate(host.servers):
            tls = backend_tls(server)
            out += [f'    server srv_{i+1} {address(server.address,server.port)} weight {server.weight} check{tls}']
    return remember_document(inject(enhance('\n'.join(out) + '\n',doc,capabilities),doc,basic_auth),doc)
