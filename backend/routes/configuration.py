"""Configuration API routes."""

import hashlib
import json

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select

from backend.db import get_db, now, Instance, Revision, BasicAuthDeployment
from backend import basic_auth
from backend import changes, config_policy
from backend.schemas import Document, DraftIn, ImportedMap
from backend.generator import generate
from backend.haproxy_config import import_config, migration_context, MIGRATION_PREFIX, inventory, extract_backends
from pydantic import BaseModel, Field
from fastapi import APIRouter
from backend import core
from backend.core import AgentUpdateRequired, SETUP_ROOT, audit, current_user, instance, operator, validate_document
from backend import naming

router = APIRouter()


@router.get('/api/import-guide')
def import_guide(user=Depends(operator)):
    return FileResponse(SETUP_ROOT / 'docs/IMPORT.md', media_type='text/plain', filename='HAPROXY-IMPORT.md')


class ImportRequest(BaseModel):
    config: str | None = Field(default=None, min_length=1, max_length=1024 * 1024)
    maps: list[ImportedMap] = Field(default_factory=list, max_length=100)
    active_hash: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    document_version: int | None = None
    preview_hash: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


def import_preview_for(i, body):
    try:
        bundle = core.agent(i, '/config-bundle')
    except HTTPException as error:
        if error.status_code not in (404, 405):
            raise HTTPException(error.status_code, 'Datei-Import fehlgeschlagen: ' + str(error.detail)) from error
        if body.config is None:
            raise AgentUpdateRequired(
                'Der Agent stellt den Datei-Import nicht bereit (/config-bundle fehlt). Den Agenten auf diesem HAProxy-Host aktualisieren und anschließend erneut einlesen.'
            ) from error
        active = core.agent(i, '/config')
        bundle = {
            'config': active['config'],
            'hash': active['hash'],
            'sources': [],
            'maps': [],
            'agent_update_required': True,
            'warnings': [
                'Nur eine Vorschau des hochgeladenen Texts: Der Agent stellt den Datei-Import nicht bereit. Vor der Übernahme den Agenten aktualisieren, damit alle geladenen Dateien geprüft werden.'
            ],
        }
    source = body.config if body.config is not None else bundle['config']
    if bundle.get('complete') is False:
        raise HTTPException(
            422,
            'Geladene Dateien konnten nicht vollständig ermittelt werden. config_sources im Agent-Profil mit allen -f-Dateien bzw. Verzeichnissen setzen, dann erneut einlesen.',
        )
    maps = (
        [m.model_dump() for m in body.maps]
        if body.config is not None
        else [{'path': m['path'], 'content': m['content']} for m in bundle['maps']]
    )
    try:
        preview = import_config(
            source,
            bundle['hash'],
            maps,
            [{'path': s['path'], 'hash': s['hash']} for s in bundle['sources']],
            [{'path': m['host_path'], 'hash': m['hash']} for m in bundle['maps']],
        )
    except ValueError as error:
        raise HTTPException(422, str(error))
    current = Document.model_validate(i.document).model_dump()
    incoming = preview['document']
    editable = (
        'hosts',
        'rules',
        'frontends',
        'backends',
        'imported_backends',
        'imported_routes',
        'frontend_certificates',
        'removed_backends',
        'removed_frontends',
        'access_profiles',
    )
    if any(current.get(key) for key in editable) and any(current.get(key) != incoming.get(key) for key in editable):
        preview['warnings'].append(
            'Die Übernahme ersetzt den aktuellen Arbeitsentwurf durch den eingelesenen Stand. Noch nicht angewendete Änderungen vorher anwenden oder sichern; Einlesen & Vorschau allein ändert den Entwurf nicht.'
        )
    preview['warnings'] += bundle['warnings']
    preview['active_hash'] = bundle['hash']
    preview['agent_update_required'] = bundle.get('agent_update_required', False)
    preview['can_import'] = not preview['agent_update_required']
    preview['source_files'] = [s['path'] for s in bundle['sources']]
    preview['preview_hash'] = hashlib.sha256(
        json.dumps(preview['document'], sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()
    return preview


@router.post('/api/instances/{id}/import-preview')
def preview_import(id: int, body: ImportRequest, user=Depends(operator), db=Depends(get_db)):
    i = instance(db, id)
    preview = import_preview_for(i, body)
    preview['document_version'] = i.document_version
    return preview


@router.post('/api/instances/{id}/import')
def commit_import(id: int, body: ImportRequest, user=Depends(operator), db=Depends(get_db)):
    i = instance(db, id)
    preview = import_preview_for(i, body)
    if not preview['can_import']:
        raise AgentUpdateRequired(
            'Vor der Übernahme den Agenten aktualisieren und die Vorschau erneut laden. Die vollständige Liste geladener Dateien ist mit diesem Agenten nicht verfügbar.'
        )
    if body.active_hash != preview['active_hash']:
        raise HTTPException(409, 'Aktive Hauptdatei wurde geändert; Vorschau erneut laden.')
    if body.preview_hash != preview['preview_hash']:
        raise HTTPException(409, 'Dateien, Maps oder Import-Inhalt wurden geändert; Vorschau erneut laden.')
    basic_auth.lock_directory(db)
    try:
        validate_document(db, Document.model_validate(preview['document']))
    except ValueError as error:
        raise HTTPException(422, str(error))
    i = db.scalar(select(Instance).where(Instance.id == id).with_for_update().execution_options(populate_existing=True))
    if not i:
        raise HTTPException(404, 'Instanz wurde entfernt.')
    if i.document_version != body.document_version:
        raise HTTPException(409, 'Grafischer Entwurf wurde parallel geändert; Vorschau erneut laden.')
    i.document = preview['document']
    i.document_version += 1
    # An import reflects exactly what runs on the server.
    i.applied_document = preview['document']
    audit(db, user.username, 'configuration.imported', i.name)
    db.commit()
    return i.document | {'version': i.document_version}


@router.get('/api/instances/{id}/config')
def config(id: int, user=Depends(operator), db=Depends(get_db)):
    return core.agent(instance(db, id), '/config')


@router.get('/api/instances/{id}/document')
def document(id: int, user=Depends(operator), db=Depends(get_db)):
    i = instance(db, id)
    recover_applied_document(db, i)
    draft = Document.model_validate(i.document).model_dump(exclude={'version'})
    return draft | {
        'version': i.document_version,
        'basic_auth_existing': basic_auth.existing_rules(i.document.get('imported_config')),
    }


def without_migration(config):
    return ''.join(line for line in config.splitlines(keepends=True) if not line.startswith(MIGRATION_PREFIX))


def applied_bundle(i, config, result, cap=None):
    """Read back the committed bytes before advancing the editor's base."""
    active = core.agent(i, '/config')
    digest = hashlib.sha256(config.encode()).hexdigest()
    if active['hash'] != digest or active['config'] != config or result.get('hash', digest) != digest:
        raise ValueError('Aktive Konfiguration wurde nach dem Anwenden geändert. Änderungen zuerst abgleichen.')
    if (cap or {}).get('config_bundle') or i.document.get('imported_sources') or migration_context(config):
        bundle = core.agent(i, '/config-bundle')
        if bundle.get('complete') is False or bundle['hash'] != digest:
            raise ValueError('Geladene Konfigurationsdateien konnten nicht vollständig abgeglichen werden.')
        for key, field, path in (('sources', 'sources', 'path'), ('map_hashes', 'maps', 'host_path')):
            expected = result.get(key)
            if expected is not None and {s['path']: s['hash'] for s in expected} != {
                s[path]: s['hash'] for s in bundle[field]
            }:
                raise ValueError('Eine Konfigurations- oder Map-Datei wurde nach dem Anwenden extern geändert.')
        return bundle
    return {'config': config, 'hash': digest, 'sources': [], 'maps': [], 'warnings': []}


def advance_import(document, bundle):
    value = dict(document)
    value['imported_active_hash'] = bundle['hash']
    value['imported_sources'] = [{'path': s['path'], 'hash': s['hash']} for s in bundle['sources']]
    value['imported_map_hashes'] = [{'path': m['host_path'], 'hash': m['hash']} for m in bundle['maps']]
    return value


def recover_applied_document(db, i):
    """Repair an unchanged draft left stale by an older management version."""
    if i.document.get('imported_config') is None:
        return
    revision = db.scalar(
        select(Revision)
        .where(Revision.instance_id == i.id, Revision.status == 'applied')
        .order_by(Revision.id.desc())
        .limit(1)
    )
    if not revision or hashlib.sha256(revision.config.encode()).hexdigest() == i.document.get('imported_active_hash'):
        return
    version = i.document_version
    try:
        cap = core.agent(i)
        doc = Document.model_validate(i.document)
        if without_migration(generate(doc, cap, basic_auth.snapshots(db, basic_auth.ids(doc)))) != without_migration(
            revision.config
        ):
            return
        bundle = applied_bundle(i, revision.config, {}, cap)
        if {m.path: m.hash for m in doc.imported_map_hashes} != {m['host_path']: m['hash'] for m in bundle['maps']}:
            return
        context = migration_context(revision.config)
        if context:
            if {s['path'] for s in bundle['sources']} != {s['path'] for s in context['files']}:
                return
            primary = next((s['path'] for s in bundle['sources'] if s['hash'] == bundle['hash']), None)
            if not primary:
                return
            stub = hashlib.sha256(('# Consolidated into ' + primary + ' by HAProxy Control\n').encode()).hexdigest()
            if any(s['hash'] != stub for s in bundle['sources'] if s['path'] != primary):
                return
        value = advance_import(i.document, bundle)
    except (HTTPException, ValueError):
        return
    locked = db.scalar(
        select(Instance).where(Instance.id == i.id).with_for_update().execution_options(populate_existing=True)
    )
    if not locked or locked.document_version != version:
        return
    locked.document = value
    locked.document_version += 1
    db.commit()


@router.get('/api/instances/{id}/proxy-layout')
def proxy_layout(id: int, user=Depends(current_user), db=Depends(get_db)):
    i = instance(db, id)
    cap = core.agent(i)
    doc = Document.model_validate(i.document)
    try:
        config = generate(doc, cap, basic_auth.snapshots(db, basic_auth.ids(doc)))
        from backend.frontend_delete import require_listener

        require_listener(config)
        error = None
    except ValueError as exc:
        config = doc.imported_config or ''
        error = str(exc)
        # Keep incomplete new listeners and pools visible so the operator can
        # fix their references or ports from the same editor.
        from backend.managed_proxy import backend as pool_config, endpoint

        if doc.imported_config is None:
            config = f'defaults\n    mode http\nfrontend public_http\n    bind :{doc.http_port}\n'
        for pool in doc.backends:
            config += '\n' + '\n'.join(pool_config(pool.name, pool.mode, pool.balance, pool.servers)) + '\n'
        for host in doc.hosts:
            if host.enabled:
                config += '\n' + '\n'.join(pool_config(naming.backend(host), 'http', host.balance, host.servers)) + '\n'
        for front in doc.frontends:
            config += (
                f'\nfrontend {front.name}\n    mode {front.mode}\n    bind {endpoint(front.bind_address, front.port)}'
                + (' ssl' if front.tls_enabled else '')
                + '\n'
            )
            if front.backend:
                config += '    default_backend ' + front.backend + '\n'
    proxies = inventory(config)
    # An unrelated incomplete listener/auth setting may require the fallback
    # inventory; it must still respect deletions already saved in the draft.
    proxies = [
        p
        for p in proxies
        if not (p['kind'] in ('backend', 'listen') and p['name'] in doc.removed_backends)
        and not (p['kind'] in ('frontend', 'listen') and p['name'] in doc.removed_frontends)
    ]
    for proxy in proxies:
        proxy['routes'] = [r for r in proxy['routes'] if r['backend'] not in doc.removed_backends]
    pools, _ = extract_backends(config)
    for proxy in proxies:
        proxy['targets'] = next(
            (
                [{'name': s.name, 'address': s.address, 'port': s.port} for s in b.servers]
                for b in pools
                if b.name == proxy['name']
            ),
            [],
        )
    return {
        'frontends': [p for p in proxies if p['kind'] in ('frontend', 'listen')],
        'backends': [p for p in proxies if p['kind'] in ('backend', 'listen')],
        'error': error,
    }


@router.put('/api/instances/{id}/document')
def save_document(id: int, body: Document, user=Depends(operator), db=Depends(get_db)):
    basic_auth.lock_directory(db)
    try:
        validate_document(db, body)
        existing = basic_auth.existing_rules(body.imported_config)
    except ValueError as error:
        raise HTTPException(422, str(error))
    i = db.scalar(select(Instance).where(Instance.id == id).with_for_update().execution_options(populate_existing=True))
    if not i:
        raise HTTPException(404)
    if body.version != i.document_version:
        raise HTTPException(409, 'Entwurf wurde parallel geändert. Bitte neu laden.')
    before = i.document or {}
    i.document = body.model_dump(exclude={'version'})
    i.document_version += 1
    audit(db, user.username, 'document.saved', i.name)
    record_history(db, user.username, i.name, 'item.saved', before, i.document)
    db.commit()
    return i.document | {'version': i.document_version, 'basic_auth_existing': existing}


HISTORY_KINDS = ('hosts', 'imported_routes', 'rules', 'backends', 'imported_backends', 'frontends')


def record_history(db, actor, target, action, before, after):
    """One audit row per changed item, readable as a per-host history."""
    for entry in changes.history_entries(before, after)[:200]:
        if entry['kind'] in HISTORY_KINDS:
            audit(db, actor, action, target, json.dumps(entry, ensure_ascii=False, sort_keys=True))


@router.get('/api/instances/{id}/history/{kind}/{key}')
def item_history(id: int, kind: str, key: str, user=Depends(operator), db=Depends(get_db)):
    """Who changed or applied a host, route or rule, newest first."""
    from backend.db import Audit

    i = instance(db, id)
    if kind not in HISTORY_KINDS:
        raise HTTPException(404)
    needle = json.dumps({'key': key}, ensure_ascii=False)[1:-1]
    rows = db.scalars(
        select(Audit)
        .where(
            Audit.target == i.name,
            Audit.action.in_(('item.saved', 'item.applied')),
            Audit.detail.contains(needle),
        )
        .order_by(Audit.id.desc())
        .limit(200)
    )
    result = []
    for row in rows:
        try:
            entry = json.loads(row.detail)
        except ValueError:
            continue
        if entry.get('kind') != kind or entry.get('key') != key:
            continue
        result.append(
            {
                'at': row.created_at.isoformat() + 'Z',
                'actor': row.actor,
                'event': 'applied' if row.action == 'item.applied' else 'saved',
                'action': entry.get('action'),
                'label': entry.get('label'),
                'fields': entry.get('fields', []),
            }
        )
        if len(result) >= 50:
            break
    return result


class ProxyDeleteIn(BaseModel):
    name: str = Field(pattern=r'^[a-zA-Z0-9_.-]{1,100}$')
    version: int = Field(ge=0)


@router.post('/api/instances/{id}/backend-delete-preview')
def preview_backend_delete(id: int, body: ProxyDeleteIn, user=Depends(operator), db=Depends(get_db)):
    from backend.backend_delete import plan

    i = instance(db, id)
    if body.version != i.document_version:
        raise HTTPException(409, 'Entwurf wurde parallel geändert. Bitte neu laden.')
    try:
        _, summary = plan(Document.model_validate(i.document), body.name)
    except ValueError as error:
        raise HTTPException(422, str(error))
    return summary | {'version': i.document_version}


@router.post('/api/instances/{id}/backend-delete')
def delete_backend(id: int, body: ProxyDeleteIn, user=Depends(operator), db=Depends(get_db)):
    from backend.backend_delete import plan

    basic_auth.lock_directory(db)
    i = db.scalar(select(Instance).where(Instance.id == id).with_for_update().execution_options(populate_existing=True))
    if not i:
        raise HTTPException(404)
    if body.version != i.document_version:
        raise HTTPException(409, 'Entwurf wurde parallel geändert. Bitte neu laden.')
    try:
        doc, _ = plan(Document.model_validate(i.document), body.name)
        validate_document(db, doc)
    except ValueError as error:
        raise HTTPException(422, str(error))
    i.document = doc.model_dump(exclude={'version'})
    i.document_version += 1
    audit(db, user.username, 'backend.deleted', i.name, body.name)
    db.commit()
    return i.document | {
        'version': i.document_version,
        'basic_auth_existing': basic_auth.existing_rules(i.document.get('imported_config')),
    }


@router.post('/api/instances/{id}/frontend-delete-preview')
def preview_frontend_delete(id: int, body: ProxyDeleteIn, user=Depends(operator), db=Depends(get_db)):
    from backend.frontend_delete import plan

    i = instance(db, id)
    if body.version != i.document_version:
        raise HTTPException(409, 'Entwurf wurde parallel geändert. Bitte neu laden.')
    try:
        _, summary = plan(Document.model_validate(i.document), body.name)
    except ValueError as error:
        raise HTTPException(422, str(error))
    return summary | {'version': i.document_version}


@router.post('/api/instances/{id}/frontend-delete')
def delete_frontend(id: int, body: ProxyDeleteIn, user=Depends(operator), db=Depends(get_db)):
    from backend.frontend_delete import plan

    basic_auth.lock_directory(db)
    i = db.scalar(select(Instance).where(Instance.id == id).with_for_update().execution_options(populate_existing=True))
    if not i:
        raise HTTPException(404)
    if body.version != i.document_version:
        raise HTTPException(409, 'Entwurf wurde parallel geändert. Bitte neu laden.')
    try:
        doc, _ = plan(Document.model_validate(i.document), body.name)
        validate_document(db, doc)
    except ValueError as error:
        raise HTTPException(422, str(error))
    i.document = doc.model_dump(exclude={'version'})
    i.document_version += 1
    audit(db, user.username, 'frontend.deleted', i.name, body.name)
    db.commit()
    return i.document | {
        'version': i.document_version,
        'basic_auth_existing': basic_auth.existing_rules(i.document.get('imported_config')),
    }


@router.post('/api/instances/{id}/generate')
def generate_config(id: int, user=Depends(operator), db=Depends(get_db)):
    i = instance(db, id)
    cap = core.agent(i)
    current = core.agent(i, '/config')
    try:
        doc = Document.model_validate(i.document)
        from backend.tls_bindings import read as tls_plans

        if (
            any(h.enabled and h.certificate for h in doc.hosts)
            or any(r.certificate for r in doc.imported_routes)
            or tls_plans(doc.imported_config or '')
        ) and not cap.get('tls_site_bindings'):
            raise ValueError(
                'Für die gezielte Zertifikatsauswahl am Reverseproxy den Agenten auf dem HAProxy-Server unter Server aktualisieren. Danach Konfiguration erneut erzeugen.'
            )
        if doc.imported_config is not None:
            if doc.imported_active_hash != current['hash']:
                raise HTTPException(
                    409,
                    'Konfiguration seit dem Import geändert. Erneut importieren, damit externe Änderungen erhalten bleiben.',
                )
            if doc.imported_sources:
                bundle = core.agent(i, '/config-bundle')
                if {s.path: s.hash for s in doc.imported_sources} != {
                    s['path']: s['hash'] for s in bundle['sources']
                } or {m.path: m.hash for m in doc.imported_map_hashes} != {
                    m['host_path']: m['hash'] for m in bundle['maps']
                }:
                    raise HTTPException(409, 'Eine Konfigurations- oder Map-Datei wurde geändert. Erneut importieren.')
        config = generate(doc, cap, basic_auth.snapshots(db, basic_auth.ids(doc)))
        from backend.frontend_delete import require_listener

        require_listener(config)
        assigned = {name for names in doc.frontend_certificates.values() for name in names}
        assigned.update(h.certificate for h in doc.hosts if h.enabled and h.certificate)
        assigned.update(r.certificate for r in doc.imported_routes if r.certificate)
        if assigned:
            certs = {
                c['name']: c
                for c in core.agent(i, '/certificates')
                if not c.get('staging') and c.get('days_remaining', -1) >= 0 and not c.get('error')
            }
            if assigned - certs.keys():
                raise ValueError(
                    'Zugewiesene Produktionszertifikate fehlen oder sind abgelaufen: '
                    + ', '.join(sorted(assigned - certs.keys()))
                )
            for site in [h for h in doc.hosts if h.enabled] + doc.imported_routes:
                if not site.certificate:
                    continue
                for name in site.hostnames:
                    covered = any(
                        name == d
                        or (
                            d.startswith('*.')
                            and not name.startswith('*.')
                            and name.count('.') == d.count('.')
                            and name.endswith(d[1:])
                        )
                        for d in certs[site.certificate].get('domains', [])
                    )
                    if not covered:
                        raise ValueError('Zertifikat ' + site.certificate + ' deckt ' + name + ' nicht ab.')
        if len(config.encode()) > 1024 * 1024:
            raise ValueError('Erzeugte Konfiguration mit Basic-Auth-Benutzern ist größer als 1 MB.')
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {'config': config, 'base_hash': current['hash'], 'document_version': i.document_version}


@router.get('/api/instances/{id}/revisions')
def revisions(id: int, user=Depends(operator), db=Depends(get_db)):
    instance(db, id)
    return [
        {
            'id': r.id,
            'status': r.status,
            'message': r.message,
            'author': r.author,
            'created_at': r.created_at.isoformat() + 'Z',
            'applied_at': r.applied_at.isoformat() + 'Z' if r.applied_at else None,
        }
        for r in db.scalars(select(Revision).where(Revision.instance_id == id).order_by(Revision.id.desc()).limit(100))
    ]


@router.get('/api/instances/{id}/revisions/{rev}')
def read_revision(id: int, rev: int, user=Depends(operator), db=Depends(get_db)):
    r = db.get(Revision, rev)
    if not r or r.instance_id != id:
        raise HTTPException(404)
    return {'config': r.config, 'base_hash': r.base_hash, 'id': r.id, 'status': r.status}


@router.post('/api/instances/{id}/revisions')
def save_revision(id: int, body: DraftIn, user=Depends(operator), db=Depends(get_db)):
    i = instance(db, id)
    r = Revision(
        instance_id=id, config=body.config, base_hash=body.base_hash, message=body.message, author=user.username
    )
    db.add(r)
    db.flush()
    audit(db, user.username, 'revision.saved', i.name, str(r.id))
    db.commit()
    return {'id': r.id}


@router.post('/api/instances/{id}/validate')
def validate_config(id: int, body: DraftIn, user=Depends(operator), db=Depends(get_db)):
    i = instance(db, id)
    # `haproxy -c` already executes lua-load scripts, so validation is guarded too.
    if user.role != 'admin' and config_policy.privileged_directives(body.config):
        require_privileged_allowed(user, body.config, core.agent(i, '/config')['config'])
    return core.agent(i, '/validate', 'POST', {'config': body.config, 'expected_hash': body.base_hash}, timeout=40)


def require_privileged_allowed(user, config, active_config):
    if user.role == 'admin':
        return
    added = config_policy.added_privileged_directives(config, active_config)
    if added:
        raise HTTPException(403, config_policy.message(added))


class ApplyRevisionIn(BaseModel):
    document_version: int | None = Field(default=None, ge=0)


@router.post('/api/instances/{id}/revisions/{rev}/apply')
def apply_revision(
    id: int, rev: int, body: ApplyRevisionIn = ApplyRevisionIn(), user=Depends(operator), db=Depends(get_db)
):
    i = instance(db, id)
    r = db.scalar(select(Revision).where(Revision.id == rev).with_for_update())
    if not r or r.instance_id != id:
        raise HTTPException(404)
    if r.status in ('applied', 'applying', 'uncertain'):
        raise HTTPException(409, 'Diese Version wurde bereits angewendet. Für einen Rollback als neuen Entwurf laden.')
    if body.document_version is not None and body.document_version != i.document_version:
        raise HTTPException(
            409, 'Grafischer Entwurf wurde geändert. Ansicht neu laden und Konfiguration erneut erzeugen.'
        )
    try:
        auth_metadata = basic_auth.assert_current(db, r.config)
    except ValueError as error:
        raise HTTPException(409, str(error))
    document_before = dict(i.document)
    version_before = i.document_version
    cap = core.agent(i)
    try:
        doc = Document.model_validate(document_before)
        matches = generate(doc, cap, basic_auth.snapshots(db, basic_auth.ids(doc))) == r.config
    except ValueError:
        matches = False
    # Persist the previous configuration before executing the remote mutation.
    previous = core.agent(i, '/config')
    if previous['hash'] != r.base_hash:
        raise HTTPException(409, 'Aktive Konfiguration wurde geändert. Neu laden und abgleichen.')
    require_privileged_allowed(user, r.config, previous['config'])
    try:
        context = migration_context(r.config)
    except ValueError as error:
        raise HTTPException(422, str(error))
    previous_config = previous['config']
    if context:
        bundle = core.agent(i, '/config-bundle')
        previous_config = bundle['config']
    previous_config = ''.join(
        line for line in previous_config.splitlines(keepends=True) if not line.startswith(MIGRATION_PREFIX)
    )
    snapshot = Revision(
        instance_id=id,
        config=previous_config,
        base_hash=previous['hash'],
        status='snapshot',
        message='Sicherung vor Version ' + str(rev),
        author=user.username,
    )
    db.add(snapshot)
    r.status = 'applying'
    audit(db, user.username, 'revision.apply.requested', i.name, str(rev))
    db.commit()
    try:
        result = core.agent(i, '/apply', 'POST', {'config': r.config, 'expected_hash': r.base_hash}, timeout=65)
    except HTTPException as error:
        # A network timeout is ambiguous: preserve that distinction in the audit trail.
        r.status = (
            'uncertain'
            if error.status_code == 502 and str(error.detail).startswith('Agent nicht erreichbar')
            else 'failed'
        )
        audit(db, user.username, 'revision.apply.' + r.status, i.name, str(error.detail))
        db.commit()
        raise
    r.status = 'applied'
    r.applied_at = now()
    sync_error = None
    bundle = None
    try:
        bundle = applied_bundle(i, r.config, result, cap)
    except (HTTPException, ValueError) as error:
        sync_error = str(error.detail) if isinstance(error, HTTPException) else str(error)
    i = db.scalar(select(Instance).where(Instance.id == id).with_for_update().execution_options(populate_existing=True))
    if not i:
        raise HTTPException(404, 'Instanz wurde während des Anwendens entfernt.')
    # The live state after this apply becomes the baseline for "pending changes".
    applied = None
    if bundle is not None and matches:
        applied = (
            advance_import(document_before, bundle)
            if document_before.get('imported_config') is not None
            else document_before
        )
    if bundle is not None:
        if i.document_version != version_before:
            sync_error = 'Ein anderer Benutzer hat den grafischen Entwurf geändert. Dieser Entwurf bleibt erhalten; Ansicht neu laden und abgleichen.'
        elif matches:
            if document_before.get('imported_config') is not None:
                i.document = applied
                i.document_version += 1
        else:
            # Text edits and rollbacks become the new imported baseline. Complex
            # directives stay intact; central auth and TLS metadata restore their assignments.
            try:
                preview = import_config(
                    bundle['config'],
                    bundle['hash'],
                    [{'path': m['path'], 'content': m['content']} for m in bundle['maps']],
                    [{'path': s['path'], 'hash': s['hash']} for s in bundle['sources']],
                    [{'path': m['host_path'], 'hash': m['hash']} for m in bundle['maps']],
                )
                validate_document(db, Document.model_validate(preview['document']))
                i.document = preview['document']
                i.document_version += 1
                applied = preview['document']
            except ValueError as error:
                sync_error = 'Grafischer Abgleich fehlgeschlagen: ' + str(error)
    if applied is not None:
        record_history(db, user.username, i.name, 'item.applied', i.applied_document or {}, applied)
    i.applied_document = applied
    deployment = db.get(BasicAuthDeployment, id)
    if auth_metadata or deployment:
        if not deployment:
            deployment = BasicAuthDeployment(instance_id=id)
            db.add(deployment)
        deployment.metadata_json = auth_metadata or {}
        deployment.applied_at = now()
    audit(db, user.username, 'revision.applied', i.name, str(rev))
    db.commit()
    return result | {'document_synced': sync_error is None, 'sync_warning': sync_error}


@router.post('/api/instances/{id}/service/{action}')
def service(id: int, action: str, user=Depends(operator), db=Depends(get_db)):
    if action not in ('reload', 'start', 'restart', 'stop'):
        raise HTTPException(404)
    if action == 'stop' and user.role != 'admin':
        raise HTTPException(403, 'Stoppen benötigt Administratorrechte.')
    i = instance(db, id)
    audit(db, user.username, 'service.' + action + '.requested', i.name)
    db.commit()
    try:
        result = core.agent(i, '/service/' + action, 'POST', {}, timeout=50)
    except HTTPException as e:
        audit(db, user.username, 'service.' + action + '.failed', i.name, str(e.detail))
        db.commit()
        raise
    audit(db, user.username, 'service.' + action, i.name)
    db.commit()
    return result


# --- Pending changes and the single apply dialog -------------------------------------


@router.get('/api/instances/{id}/changes')
def pending_changes(id: int, user=Depends(operator), db=Depends(get_db)):
    i = instance(db, id)
    if i.applied_document is None:
        return {'baseline': False, 'count': None, 'changes': [], 'version': i.document_version}
    items = changes.summarize(i.document, i.applied_document)
    return {'baseline': True, 'count': len(items), 'changes': items, 'version': i.document_version}


class RevertIn(BaseModel):
    kind: str = Field(pattern=r'^[a-z_]{1,40}$')
    key: str = Field(default='', max_length=200)
    version: int = Field(ge=0)


@router.post('/api/instances/{id}/changes/revert')
def revert_change(id: int, body: RevertIn, user=Depends(operator), db=Depends(get_db)):
    basic_auth.lock_directory(db)
    i = db.scalar(select(Instance).where(Instance.id == id).with_for_update().execution_options(populate_existing=True))
    if not i:
        raise HTTPException(404, 'Instanz nicht gefunden.')
    if i.applied_document is None:
        raise HTTPException(
            409,
            'Der angewendete Stand ist unbekannt. Einmal anwenden, danach lassen sich Änderungen einzeln verwerfen.',
        )
    if body.version != i.document_version:
        raise HTTPException(409, 'Entwurf wurde parallel geändert. Bitte neu laden.')
    try:
        value = changes.revert(i.document, i.applied_document, body.kind, body.key)
        doc = Document.model_validate(value)
        validate_document(db, doc)
    except ValueError as error:
        raise HTTPException(
            422,
            'Diese Änderung hängt mit anderen zusammen und lässt sich nicht einzeln verwerfen: '
            + str(error)
            + ' Stattdessen alle Änderungen verwerfen.',
        )
    i.document = doc.model_dump(exclude={'version'})
    i.document_version += 1
    audit(db, user.username, 'document.change_reverted', i.name, body.kind + ':' + body.key)
    db.commit()
    return i.document | {'version': i.document_version}


class ApplyPreviewIn(BaseModel):
    # Without config the saved draft is generated; with config (text editor) it is used as is.
    config: str | None = Field(default=None, min_length=1, max_length=1024 * 1024)
    base_hash: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


@router.post('/api/instances/{id}/apply-preview')
def apply_preview(id: int, body: ApplyPreviewIn = ApplyPreviewIn(), user=Depends(operator), db=Depends(get_db)):
    i = instance(db, id)
    if body.config is None:
        generated = generate_config(id, user, db)
        config, base_hash, version, source = (
            generated['config'],
            generated['base_hash'],
            generated['document_version'],
            'draft',
        )
    else:
        config, base_hash, version, source = body.config, body.base_hash, i.document_version, 'text'
    active = core.agent(i, '/config')
    if base_hash and base_hash != active['hash']:
        raise HTTPException(409, 'Aktive Konfiguration wurde geändert. Neu laden und abgleichen.')
    base_hash = active['hash']
    diff = changes.line_diff(active['config'], config)
    try:
        result = validate_config(id, DraftIn(config=config, base_hash=base_hash), user, db)
        valid, output = True, str(result.get('output') or '')
    except HTTPException as error:
        if error.status_code not in (403, 422):
            raise
        valid, output = False, str(error.detail)
    if source == 'draft' and not diff['hunks'] and i.applied_document is None:
        # Draft and server agree: this is a reliable baseline for pending changes.
        i.applied_document = dict(i.document)
        db.commit()
    items = changes.summarize(i.document, i.applied_document) if i.applied_document is not None else None
    return {
        'source': source,
        'config': config,
        'base_hash': base_hash,
        'document_version': version,
        'diff': diff,
        'valid': valid,
        'output': output,
        'changes': items,
    }
