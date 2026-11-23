"""Pending changes: what the saved draft changes compared with the last applied state.

The draft (``Instance.document``) is compared with ``Instance.applied_document`` item by
item, so the UI can list "Proxy Host app.example.com hinzugefügt" and undo single
entries before anything is applied.
"""

import difflib

# (document key, identity field, label field, singular label)
COLLECTIONS = (
    ('hosts', 'id', 'domain', 'Proxy Host'),
    ('imported_managed_hosts', 'id', 'domain', 'Proxy Host'),
    ('imported_routes', 'id', 'domain', 'Domain-Route'),
    ('rules', 'id', 'name', 'Regel'),
    ('access_profiles', 'id', 'name', 'IP-Filter-Profil'),
    ('frontends', 'name', 'name', 'Frontend'),
    ('backends', 'name', 'name', 'Backend'),
    ('imported_backends', 'name', 'name', 'Backend'),
)
SETTINGS = ('http_port', 'https_port', 'tls_enabled', 'acme_enabled', 'acme_address', 'acme_port', 'maxconn')
REMOVALS = (('removed_frontends', 'Frontend'), ('removed_backends', 'Backend'))


def _label(item, field):
    value = item.get(field) or item.get('name') or item.get('id') or '?'
    if field == 'domain' and item.get('path') and item.get('path') != '/':
        value += item['path']
    return str(value)


def _by_key(items, key):
    return {str(item.get(key)): item for item in items or []}


def summarize(draft: dict, applied: dict) -> list[dict]:
    """List of {kind, key, action, label, type} entries; action is added/changed/removed."""
    out = []
    for kind, key, label, singular in COLLECTIONS:
        old, new = _by_key(applied.get(kind), key), _by_key(draft.get(kind), key)
        for k, item in new.items():
            if k not in old:
                out.append({'kind': kind, 'key': k, 'action': 'added', 'type': singular, 'label': _label(item, label)})
            elif item != old[k]:
                out.append(
                    {'kind': kind, 'key': k, 'action': 'changed', 'type': singular, 'label': _label(item, label)}
                )
        for k, item in old.items():
            if k not in new:
                out.append(
                    {'kind': kind, 'key': k, 'action': 'removed', 'type': singular, 'label': _label(item, label)}
                )
    for kind, singular in REMOVALS:
        for name in sorted(set(draft.get(kind) or []) - set(applied.get(kind) or [])):
            out.append({'kind': kind, 'key': name, 'action': 'removed', 'type': singular, 'label': name})
    old_certs, new_certs = applied.get('frontend_certificates') or {}, draft.get('frontend_certificates') or {}
    for name in sorted(set(old_certs) | set(new_certs)):
        if sorted(old_certs.get(name, [])) != sorted(new_certs.get(name, [])):
            out.append(
                {
                    'kind': 'frontend_certificates',
                    'key': name,
                    'action': 'changed',
                    'type': 'Zertifikate',
                    'label': 'Frontend ' + name,
                }
            )
    if any(draft.get(k) != applied.get(k) for k in SETTINGS):
        out.append(
            {'kind': 'settings', 'key': 'settings', 'action': 'changed', 'type': 'Listener', 'label': 'HTTP/HTTPS'}
        )
    return out


def revert(draft: dict, applied: dict, kind: str, key: str) -> dict:
    """Return a copy of the draft with one entry restored to its applied state."""
    value = dict(draft)
    if kind == 'all':
        return dict(applied)
    if kind == 'settings':
        for k in SETTINGS:
            if k in applied:
                value[k] = applied[k]
        return value
    if kind == 'frontend_certificates':
        certs = dict(value.get('frontend_certificates') or {})
        old = (applied.get('frontend_certificates') or {}).get(key)
        if old is None:
            certs.pop(key, None)
        else:
            certs[key] = old
        value['frontend_certificates'] = certs
        return value
    for removal, _ in REMOVALS:
        if kind == removal:
            value[kind] = [n for n in value.get(kind) or [] if n != key]
            return value
    spec = next((c for c in COLLECTIONS if c[0] == kind), None)
    if not spec:
        raise ValueError('Unbekannte Änderung.')
    id_field = spec[1]
    old_items, new_items = list(applied.get(kind) or []), list(value.get(kind) or [])
    old = next((item for item in old_items if str(item.get(id_field)) == key), None)
    index = next((n for n, item in enumerate(new_items) if str(item.get(id_field)) == key), None)
    if old is None and index is not None:
        new_items.pop(index)  # undo "added"
    elif old is not None and index is not None:
        new_items[index] = old  # undo "changed"
    elif old is not None:
        # undo "removed": restore at its previous position
        position = old_items.index(old)
        before = {str(item.get(id_field)) for item in old_items[:position]}
        insert_at = max((n + 1 for n, item in enumerate(new_items) if str(item.get(id_field)) in before), default=0)
        new_items.insert(insert_at, old)
    else:
        raise ValueError('Diese Änderung ist nicht mehr vorhanden.')
    value[kind] = new_items
    return value


def line_diff(old: str, new: str, context: int = 3) -> dict:
    """Unified diff as hunks of {type, text, old, new} lines for the apply dialog."""
    a, b = old.splitlines(), new.splitlines()
    hunks, added, removed = [], 0, 0
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(context):
        lines = []
        for tag, i1, i2, j1, j2 in group:
            if tag == 'equal':
                lines += [
                    {'type': 'context', 'text': a[i], 'old': i + 1, 'new': j1 + i - i1 + 1} for i in range(i1, i2)
                ]
                continue
            if tag in ('replace', 'delete'):
                lines += [{'type': 'removed', 'text': a[i], 'old': i + 1, 'new': None} for i in range(i1, i2)]
                removed += i2 - i1
            if tag in ('replace', 'insert'):
                lines += [{'type': 'added', 'text': b[j], 'old': None, 'new': j + 1} for j in range(j1, j2)]
                added += j2 - j1
        first = group[0]
        hunks.append({'header': f'@@ -{first[1] + 1} +{first[3] + 1} @@', 'lines': lines})
    return {'hunks': hunks, 'added': added, 'removed': removed}


FIELD_LABELS = {
    'domain': 'Domain',
    'aliases': 'weitere Domains',
    'path': 'Pfad',
    'enabled': 'Aktiv',
    'force_https': 'HTTPS-Weiterleitung',
    'frontend': 'Frontend',
    'certificate': 'Zertifikat',
    'balance': 'Verteilung',
    'servers': 'Zielserver',
    'basic_auth_group': 'Passwortschutz',
    'basic_auth_forward': 'Passwortschutz',
    'access_profile': 'IP-Filter',
    'access_policy': 'IP-Filter',
    'access_paths': 'IP-Filter',
    'proxy_options': 'Proxy-Optionen',
    'backend': 'Backend',
    'name': None,  # derived from domain and path
}


def changed_fields(old: dict, new: dict) -> list[str]:
    """Readable names of the fields that differ, e.g. ['Zielserver', 'Pfad']."""
    labels = []
    for key in sorted(set(old) | set(new)):
        if old.get(key) == new.get(key):
            continue
        label = FIELD_LABELS.get(key, key)
        if label and label not in labels:
            labels.append(label)
    return labels


def history_entries(before: dict, after: dict) -> list[dict]:
    """summarize() plus the changed fields, for the per-item history."""
    entries = []
    for change in summarize(after, before):
        entry = dict(change)
        if change['action'] == 'changed':
            spec = next((c for c in COLLECTIONS if c[0] == change['kind']), None)
            if spec:
                old = _by_key(before.get(change['kind']), spec[1]).get(change['key'], {})
                new = _by_key(after.get(change['kind']), spec[1]).get(change['key'], {})
                entry['fields'] = changed_fields(old, new)
        entries.append(entry)
    return entries
