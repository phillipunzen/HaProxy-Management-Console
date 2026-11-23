"""Readable HAProxy names for proxy hosts.

A host's backend is named after its domain and path (``shop.example.com/api`` ->
``shop_example_com_api``). The host ACL reuses that name, the path ACL appends
``_path`` and servers are numbered after the first domain label (``shop_1``).
The chosen name is stored on the host so it stays stable; hosts saved by older
versions (``name`` unset) keep the legacy ``backend_<id>``/``host_<id>`` scheme
until the document is validated again.
"""

import hashlib
import re

# Comment lines the generator writes; stripped before a configuration is parsed
# again so they never pile up or block the recognition of generated hosts.
MARKER = '# [Proxy Host] '
MARKER_LINE = re.compile(r'^[ \t]*' + re.escape(MARKER.rstrip()) + r'(?: .*)?\r?\n?$')
NAME = re.compile(r'[a-zA-Z0-9_.-]{1,80}')
ID = re.compile(r'[a-zA-Z0-9_-]{1,40}')
RESERVED = {'public_http', 'unknown_host', 'acme_webroot'}


def slug(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')


def base(host) -> str:
    domain = 'wildcard_' + slug(host.domain[2:]) if host.domain.startswith('*.') else slug(host.domain)
    path = slug(host.path) if host.path != '/' else ''
    value = (domain + ('_' + path if path else ''))[:64].rstrip('_')
    return value or 'site'


def backend(host) -> str:
    return host.name or 'backend_' + host.id


def acls(host) -> tuple[str, str]:
    if host.name:
        return host.name, host.name + '_path'
    return 'host_' + host.id, 'path_' + host.id


def server_prefix(host) -> str:
    if not host.name:
        return 'srv'
    label = host.domain.split('.')[0]
    return 'wildcard' if label == '*' else slug(label)[:30] or 'srv'


def servers(host) -> list[str]:
    prefix = server_prefix(host)
    return [f'{prefix}_{i + 1}' for i in range(len(host.servers))]


def owned(host) -> set[str]:
    """Every backend name a generated host may still have in the active configuration."""
    return {backend(host), 'backend_' + host.id}


def condition(host) -> list[str]:
    """ACL names joined by AND; a root path needs no path ACL."""
    host_acl, path_acl = acls(host)
    return [host_acl] if host.name and host.path == '/' else [host_acl, path_acl]


def recovered_id(name: str) -> str:
    return name if ID.fullmatch(name) else 'h' + hashlib.sha256(name.encode()).hexdigest()[:12]


def assign(hosts, reserved=()) -> list:
    """Give every host a unique readable name; returns the (copied where changed) hosts."""
    taken = set(RESERVED) | set(reserved)
    bases = [base(h) for h in hosts]
    names = [None] * len(hosts)
    used = set()
    # Keep a stored name while it still fits domain and path, so adding a second
    # host for the same site never renames the first one.
    for i, (host, wanted) in enumerate(zip(hosts, bases)):
        current = host.name
        if (
            current
            and NAME.fullmatch(current)
            and (
                current == wanted
                or current == f'{wanted[:56]}_{slug(host.id)[:8] or "x"}'
                or re.fullmatch(re.escape(wanted[:56]) + r'_\d+', current)
            )
            and current not in taken
            and current not in used
        ):
            names[i] = current
            used.add(current)
    for i, (host, wanted) in enumerate(zip(hosts, bases)):
        if names[i]:
            continue
        name = wanted
        if name in taken or name in used or bases.count(wanted) > 1:
            name = f'{wanted[:56]}_{slug(host.id)[:8] or "x"}'
        n = 2
        while name in taken or name in used:
            name = f'{wanted[:56]}_{n}'
            n += 1
        names[i] = name
        used.add(name)
    return [h if h.name == n else h.model_copy(update={'name': n}) for h, n in zip(hosts, names)]


def strip_markers(config: str) -> str:
    if MARKER.rstrip() not in config:
        return config
    return ''.join(line for line in config.splitlines(keepends=True) if not MARKER_LINE.match(line))


def site_comment(host) -> str:
    names = ', '.join(host.hostnames)
    path = '' if host.path == '/' else '  Pfad ' + host.path
    return MARKER + names + path


def backend_comment(host) -> str:
    count = len(host.servers)
    target = '1 Zielserver' if count == 1 else f'{count} Zielserver'
    return MARKER + host.domain + ('' if host.path == '/' else host.path) + ' -> ' + target
