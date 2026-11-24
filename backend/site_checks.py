"""Which domains to test after an apply and how to read the answers."""

import re

from backend.haproxy_config import parse_sections

MAX_SITES = 50


def listener_ports(config):
    """frontend name -> {'http': port | None, 'https': port | None} from its bind lines."""
    ports = {}
    for section in parse_sections(config)[1]:
        if section.kind not in ('frontend', 'listen'):
            continue
        entry = ports.setdefault(section.name, {'http': None, 'https': None})
        for _, tokens in section.lines:
            if tokens[0] != 'bind' or len(tokens) < 2:
                continue
            tls = 'ssl' in tokens[2:]
            for value in tokens[1].split(','):
                match = re.fullmatch(r'(.*):(\d+)', value)
                if not match or match[1].strip('[]') not in ('', '*', '0.0.0.0', '::', '127.0.0.1', '::1'):
                    continue  # only listeners reachable on the loopback interface
                key = 'https' if tls else 'http'
                entry[key] = entry[key] or int(match[2])
    return ports


def plan(doc, config, items=None):
    """Requests for the given ({kind, key}) items, or for every enabled host and route."""
    wanted = {(i['kind'], str(i['key'])) for i in items} if items is not None else None
    ports = listener_ports(config)
    entries = []
    sources = [('hosts', h, h.path, h.force_https) for h in doc.hosts if h.enabled] + [
        ('imported_routes', r, '/', False) for r in doc.imported_routes
    ]
    for kind, item, path, https in sources:
        if wanted is not None and (kind, item.id) not in wanted:
            continue
        listener = ports.get(item.frontend) or {}
        use_tls = bool(listener.get('https')) and (https or not listener.get('http'))
        port = listener.get('https') if use_tls else listener.get('http')
        names = [n for n in item.hostnames if not n.startswith('*.')]
        label = item.domain + (path if path != '/' else '')
        if not port or not names:
            reason = 'Kein lokal erreichbarer Listener' if not port else 'Nur Platzhalter-Domains'
            entries.append({'kind': kind, 'key': item.id, 'label': label, 'skipped': reason})
            continue
        entries.append(
            {
                'kind': kind,
                'key': item.id,
                'label': label,
                'request': {'domain': names[0], 'path': path, 'port': port, 'tls': use_tls},
            }
        )
    return entries[:MAX_SITES]


def verdict(result):
    """ok / warning / error with a short German explanation."""
    status = result.get('status')
    if status is None:
        return 'error', result.get('error') or 'Keine Antwort'
    if 200 <= status < 300:
        return 'ok', f'Antwortet mit {status}'
    if 300 <= status < 400:
        target = result.get('location') or ''
        return 'ok', f'Leitet weiter ({status})' + (f' nach {target}' if target else '')
    if status in (401, 403):
        return 'ok', f'Geschützt ({status})'
    if status == 404:
        return 'warning', '404 – Domain oder Pfad wird nicht zugeordnet oder die Anwendung kennt den Pfad nicht'
    if status in (502, 503, 504):
        return 'error', f'{status} – Zielserver nicht erreichbar oder antwortet nicht'
    return 'warning', f'Antwortet mit {status}'
