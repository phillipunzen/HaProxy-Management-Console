"""HTTPS assistant: what a proxy host or domain needs to be served over HTTPS.

The assistant combines steps that otherwise live on three pages: make the ACME
challenge reachable, request a certificate, assign it, redirect HTTP to HTTPS and
apply. ``plan`` only reads; ``prepare_challenge`` and ``assign`` change the draft.
"""

import re

from backend import changes
from backend.haproxy_config import parse_sections

KINDS = ('hosts', 'imported_routes')
CHALLENGE_PATH = '/.well-known/acme-challenge'


def find(doc, kind, key):
    items = doc.hosts if kind == 'hosts' else doc.imported_routes
    return next((item for item in items if item.id == key), None)


def covers(domains, name):
    return any(
        name == d
        or (
            d.startswith('*.')
            and not name.startswith('*.')
            and name.count('.') == d.count('.')
            and name.endswith(d[1:])
        )
        for d in domains
    )


def usable(cert):
    return (
        not cert.get('staging')
        and not cert.get('error')
        and (cert.get('days_remaining') if cert.get('days_remaining') is not None else -1) >= 0
    )


def suggested_name(domain, taken):
    base = re.sub(r'[^a-zA-Z0-9_-]+', '_', domain.removeprefix('*.').lower()).strip('_')[:70] or 'site'
    if domain.startswith('*.'):
        base = ('wildcard_' + base)[:70]
    name, n = base, 2
    while name in taken:
        name, n = f'{base}-{n}', n + 1
    return name


def tls_listener(doc, item):
    """('ready' | 'enable' | None): TLS bind present, can be switched on, or missing."""
    managed = next((f for f in doc.frontends if f.name == item.frontend), None)
    if managed is not None:
        return 'ready' if managed.tls_enabled else None
    if doc.imported_config is None:
        if item.frontend != 'public_http':
            return None
        return 'ready' if doc.tls_enabled else 'enable'
    for section in parse_sections(doc.imported_config)[1]:
        if section.kind in ('frontend', 'listen') and section.name == item.frontend:
            return 'ready' if any(t[0] == 'bind' and 'ssl' in t for _, t in section.lines) else None
    return None


def plan(doc, applied, kind, key, cap, certs, active_config):
    item = find(doc, kind, key)
    if item is None:
        return None
    names = list(item.hostnames)
    wildcard = any(n.startswith('*.') for n in names)
    valid = [c for c in certs if usable(c)]
    existing = [
        {'name': c['name'], 'domains': c.get('domains', []), 'days_remaining': c.get('days_remaining')}
        for c in valid
        if all(covers(c.get('domains', []), n) for n in names)
    ]
    native = doc.imported_config is None
    challenge_live = CHALLENGE_PATH in (active_config or '')
    http_reason = None
    if not cap.get('http_challenge'):
        http_reason = 'Auf dem HAProxy-Server ist kein Webroot für die HTTP-Challenge eingerichtet.'
    elif wildcard:
        http_reason = 'Platzhalter-Domains (*.) lassen sich nur per DNS-Challenge bestätigen.'
    elif not native and not challenge_live:
        http_reason = (
            'Die importierte Konfiguration leitet /.well-known/acme-challenge/ nicht an den Webroot weiter. '
            'DNS-Challenge verwenden oder die Weiterleitung im Texteditor ergänzen.'
        )
    credentials = cap.get('dns_credentials') or []
    dns_reason = None
    if 'lego' not in (cap.get('acme_engines') or []) or not cap.get('dns_credentials_ui'):
        dns_reason = 'Der Agent unterstützt die DNS-Challenge noch nicht. Agent aktualisieren.'
    elif not credentials:
        dns_reason = (
            'Noch kein DNS-Zugang gespeichert. Einmal unter Zertifikate → Zertifikat anfordern mit '
            'DNS-Challenge und API-Token anlegen; danach steht er hier zur Auswahl.'
        )
    listener = tls_listener(doc, item)
    blockers = []
    if cap.get('certificate_scope_error'):
        blockers.append(cap['certificate_scope_error'])
    if not cap.get('tls_site_bindings'):
        blockers.append('Der Agent kann Zertifikate noch nicht gezielt zuweisen. Agent aktualisieren.')
    if kind == 'hosts' and not item.enabled:
        blockers.append('Der Proxy Host ist deaktiviert. Zuerst aktivieren.')
    if listener is None:
        blockers.append(
            f'Das Frontend {item.frontend} hat keinen HTTPS-Listener (TLS-Bind). '
            'Für HTTPS ein Frontend mit TLS wählen oder dort einen TLS-Bind ergänzen.'
        )
    if not existing and http_reason and dns_reason:
        blockers.append('Kein Weg zum Zertifikat: ' + http_reason + ' ' + dns_reason)
    others = [
        c
        for c in (changes.summarize(doc.model_dump(), applied) if applied is not None else [])
        if not (c['kind'] == kind and c['key'] == key)
    ]
    template = next((c.get('production_template') for c in certs if c.get('production_template')), None) or {}
    return {
        'kind': kind,
        'key': key,
        'label': item.domain + (item.path if kind == 'hosts' and item.path != '/' else ''),
        'domains': names,
        'wildcard': wildcard,
        'certificate': item.certificate,
        'certificate_ok': bool(item.certificate) and any(e['name'] == item.certificate for e in existing),
        'redirect': kind == 'hosts',
        'force_https': bool(getattr(item, 'force_https', False)),
        'existing': existing,
        'challenges': {
            'http': {'available': http_reason is None, 'reason': http_reason},
            'dns': {
                'available': dns_reason is None,
                'reason': dns_reason,
                'credentials': [{'id': c['id'], 'provider': c['provider'], 'label': c['label']} for c in credentials],
            },
        },
        # Native configs route the challenge once acme_enabled is applied.
        'enable_challenge': native and not doc.acme_enabled,
        'challenge_live': challenge_live,
        'challenge_target': f'{doc.acme_address}:{doc.acme_port}' if native else None,
        'enable_tls': listener == 'enable',
        'suggested_name': suggested_name(names[0], {c['name'] for c in certs}),
        'suggested_email': template.get('email') or '',
        'pending': others,
        'applied_known': applied is not None,
        'blockers': blockers,
        'can_check_sites': bool(cap.get('probes')),
    }


def prepare_challenge(doc):
    """Routes /.well-known/acme-challenge/ to the agent's webroot (native configs)."""
    if doc.imported_config is not None:
        raise ValueError('Importierte Konfigurationen leiten die Challenge selbst weiter.')
    return doc.model_copy(update={'acme_enabled': True})


def assign(doc, kind, key, certificate):
    """Assigns the certificate, redirects HTTP to HTTPS and switches on the TLS listener."""
    item = find(doc, kind, key)
    if item is None:
        raise LookupError
    listener = tls_listener(doc, item)
    if listener is None:
        raise ValueError(f'Das Frontend {item.frontend} hat keinen HTTPS-Listener.')
    update = {'certificate': certificate}
    if kind == 'hosts':
        update['force_https'] = True
    changed = item.model_copy(update=update)
    values = {kind: [changed if x.id == key else x for x in getattr(doc, kind)]}
    if listener == 'enable':
        values['tls_enabled'] = True
    return doc.model_copy(update=values)
