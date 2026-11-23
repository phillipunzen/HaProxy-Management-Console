from backend.naming import MARKER
from backend.schemas import Document


def address(host, port):
    return f'[{host}]:{port}' if ':' in host else f'{host}:{port}'


def backend_tls(server):
    if not server.tls:
        return ''
    tls = ' ssl verify required ca-file /etc/ssl/certs/ca-certificates.crt' if server.tls_verify else ' ssl verify none'
    if ':' not in server.address and not server.address.replace('.', '').isdigit():
        tls += f' sni str({server.address})'
        if server.tls_verify:
            tls += f' verifyhost {server.address}'
    return tls


def host_acl(host):
    from backend.naming import acls

    name = acls(host)[0]
    result = []
    exact = [name for name in host.hostnames if not name.startswith('*.')]
    wildcard = [name[1:] for name in host.hostnames if name.startswith('*.')]
    if exact:
        result.append(f'    acl {name} hdr(host),field(1,:) -i ' + ' '.join(exact))
    if wildcard:
        result.append(f'    acl {name} hdr(host),field(1,:) -m end -i ' + ' '.join(wildcard))
    return result


def path_acl(host):
    """The path ACL; readable hosts on ``/`` match every path and need none."""
    from backend.naming import acls, condition

    return [f'    acl {acls(host)[1]} path_beg {host.path}'] if len(condition(host)) == 2 else []


def site_rules(host, acme=False):
    """Comment, ACLs and HTTPS redirect of one host, in frontend order."""
    from backend.naming import condition, site_comment

    entries = ([f'    {site_comment(host)}'] if host.name else []) + host_acl(host) + path_acl(host)
    if host.force_https:
        entries.append(
            '    http-request redirect scheme https code 301 if '
            + ' '.join(condition(host))
            + ' !{ ssl_fc }'
            + (' !acme_challenge' if acme else '')
        )
    return entries


def route(host):
    from backend.naming import backend, condition

    return f'    use_backend {backend(host)} if ' + ' '.join(condition(host))


def server_lines(host):
    from backend.naming import servers

    return [
        f'    server {name} {address(s.address, s.port)} weight {s.weight} check{backend_tls(s)}'
        for name, s in zip(servers(host), host.servers)
    ]


def host_backend(host, mode_line=True):
    from backend.naming import backend, backend_comment

    return (
        ([backend_comment(host)] if host.name else [])
        + [f'backend {backend(host)}']
        + (['    mode http'] if mode_line else [])
        + [f'    balance {host.balance}']
        + ['    ' + line for line in host.proxy_options]
        + server_lines(host)
    )


def host_order(host):
    return (-len(host.path), any(name.startswith('*.') for name in host.hostnames))


def generate(doc: Document, capabilities: dict, basic_auth=None) -> str:
    from backend.basic_auth import inject
    from backend.haproxy_config import remember_document
    from backend.managed_proxy import enhance
    from backend.access_control import inject as restrict
    from backend.managed_rules import inject as apply_rules

    if any(h.name is None for h in doc.hosts):
        # Hosts assigned after validation still need their readable names.
        doc = Document.model_validate(doc.model_dump())

    def finish(config):
        if doc.removed_frontends:
            from backend.frontend_delete import require_listener

            require_listener(config)
        return remember_document(config, doc)

    if doc.imported_config is not None:
        from backend.haproxy_config import generate_imported

        return finish(
            restrict(inject(apply_rules(enhance(generate_imported(doc), doc, capabilities), doc), doc, basic_auth), doc)
        )
    socket = capabilities['runtime_socket_config']
    cert_dir = capabilities['cert_dir_config']
    for p in (socket, cert_dir):
        if any(x.isspace() for x in p) or not p.startswith('/'):
            raise ValueError('Agent-Pfade müssen absolute Pfade ohne Leerzeichen sein.')
    out = [
        '# Managed by HAProxy Control',
        'global',
        '    log stdout format raw local0',
        f'    maxconn {doc.maxconn}',
        f'    stats socket {socket} mode 660 level admin',
        '',
        'defaults',
        '    mode http',
        '    log global',
        '    option httplog',
        '    option dontlognull',
        '    timeout connect 5s',
        '    timeout client 60s',
        '    timeout server 60s',
        '',
        'frontend public_http',
        f'    bind :{doc.http_port}',
    ]
    if doc.tls_enabled:
        out += [f'    bind :{doc.https_port} ssl crt {cert_dir}/ alpn h2,http/1.1']
    if doc.acme_enabled:
        out += ['    acl acme_challenge path_beg /.well-known/acme-challenge/']
    hosts = sorted((h for h in doc.hosts if h.enabled and h.frontend == 'public_http'), key=host_order)
    for host in hosts:
        out += site_rules(host, doc.acme_enabled)
    if doc.acme_enabled:
        out += ['    use_backend acme_webroot if acme_challenge']
    if hosts:
        out += ['    ' + MARKER + 'Weiterleitung an die Backends']
    out += [route(host) for host in hosts]
    out += ['    default_backend unknown_host', '', 'backend unknown_host', '    http-request deny deny_status 404']
    if doc.acme_enabled:
        out += ['', 'backend acme_webroot', f'    server acme {address(doc.acme_address, doc.acme_port)}']
    for host in (h for h in doc.hosts if h.enabled):
        # defaults already sets mode http
        out += [''] + host_backend(host, mode_line=not host.name)
    from backend.frontend_delete import remove_sections as remove_frontends

    config = remove_frontends('\n'.join(out) + '\n', doc.removed_frontends)
    return finish(restrict(inject(apply_rules(enhance(config, doc, capabilities), doc), doc, basic_auth), doc))
