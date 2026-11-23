"""Reachability checks: backend targets before saving, domains after applying."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend import core, site_checks
from backend.core import AgentUpdateRequired, audit, instance, operator
from backend.db import get_db
from backend.schemas import Document

router = APIRouter()

UPDATE = 'Der Agent auf diesem Server kennt die Erreichbarkeitsprüfung noch nicht. Agent aktualisieren (Server → Agent aktualisieren).'


class TargetIn(BaseModel):
    address: str = Field(min_length=1, max_length=253, pattern=r'^[a-zA-Z0-9.:_\[\]-]+$')
    port: int = Field(ge=1, le=65535)
    tls: bool = False


class TargetsIn(BaseModel):
    targets: list[TargetIn] = Field(min_length=1, max_length=30)


class SiteItem(BaseModel):
    kind: str = Field(pattern=r'^(hosts|imported_routes)$')
    key: str = Field(min_length=1, max_length=100)


class SitesIn(BaseModel):
    # None: every enabled host and route of the server.
    items: list[SiteItem] | None = Field(default=None, max_length=200)


def require_probes(i):
    if not core.agent(i).get('probes'):
        raise AgentUpdateRequired(UPDATE)


@router.post('/api/instances/{id}/check-targets')
def check_targets(id: int, body: TargetsIn, user=Depends(operator), db=Depends(get_db)):
    i = instance(db, id)
    require_probes(i)
    targets = [t.model_dump() | {'address': t.address.strip('[]')} for t in body.targets]
    result = core.agent(i, '/probe/targets', 'POST', {'targets': targets}, timeout=20)
    return {'results': result['results']}


@router.post('/api/instances/{id}/check-sites')
def check_sites(id: int, body: SitesIn = SitesIn(), user=Depends(operator), db=Depends(get_db)):
    i = instance(db, id)
    require_probes(i)
    try:
        doc = Document.model_validate(i.applied_document or i.document)
    except ValueError as error:
        raise HTTPException(409, 'Gespeicherter Stand kann nicht gelesen werden: ' + str(error))
    config = core.agent(i, '/config')['config']
    items = [s.model_dump() for s in body.items] if body.items is not None else None
    entries = site_checks.plan(doc, config, items)
    requests = [e['request'] for e in entries if 'request' in e]
    answers = (
        iter(core.agent(i, '/probe/sites', 'POST', {'sites': requests}, timeout=40)['results'])
        if requests
        else iter(())
    )
    results = []
    for entry in entries:
        if 'request' not in entry:
            reason = entry.pop('skipped')
            results.append(entry | {'state': 'skipped', 'message': reason})
            continue
        answer = next(answers)
        state, message = site_checks.verdict(answer)
        results.append(
            {k: entry[k] for k in ('kind', 'key', 'label')}
            | {
                'state': state,
                'message': message,
                'status': answer.get('status'),
                'ms': answer.get('ms'),
                'url': ('https://' if entry['request']['tls'] else 'http://')
                + entry['request']['domain']
                + entry['request']['path'],
            }
        )
    failed = sum(r['state'] == 'error' for r in results)
    audit(db, user.username, 'sites.checked', i.name, f'{len(results)} geprüft, {failed} mit Fehler')
    db.commit()
    return {'results': results}
