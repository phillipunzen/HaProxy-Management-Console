"""HTTPS assistant API: plan for one host or domain, then two draft changes.

Applying and requesting the certificate reuse the regular endpoints, so every
safeguard there (validation, base hash, snapshots, audit) also covers the assistant.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend import core, https_assistant
from backend.core import audit, instance, operator, validate_document
from backend.db import Instance, get_db
from backend.routes.configuration import record_history
from backend.schemas import Document

router = APIRouter()


class VersionIn(BaseModel):
    version: int = Field(ge=0)


class AssignIn(VersionIn):
    certificate: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')


def kind_ok(kind):
    if kind not in https_assistant.KINDS:
        raise HTTPException(404)


def current_doc(i):
    try:
        return Document.model_validate(i.document)
    except ValueError as error:
        raise HTTPException(409, 'Entwurf kann nicht gelesen werden: ' + str(error))


@router.get('/api/instances/{id}/https/{kind}/{key}')
def https_plan(id: int, kind: str, key: str, user=Depends(operator), db=Depends(get_db)):
    kind_ok(kind)
    i = instance(db, id)
    cap = core.agent(i)
    certs = core.agent(i, '/certificates')
    active = core.agent(i, '/config')['config']
    result = https_assistant.plan(current_doc(i), i.applied_document, kind, key, cap, certs, active)
    if result is None:
        raise HTTPException(404, 'Eintrag nicht gefunden. Ansicht neu laden.')
    return result | {'version': i.document_version}


def save(db, id, user, body, change, action, detail):
    i = db.scalar(select(Instance).where(Instance.id == id).with_for_update().execution_options(populate_existing=True))
    if not i:
        raise HTTPException(404)
    if body.version != i.document_version:
        raise HTTPException(409, 'Entwurf wurde parallel geändert. Assistent neu öffnen.')
    try:
        doc = Document.model_validate(change(current_doc(i)).model_dump())
        validate_document(db, doc)
    except LookupError:
        raise HTTPException(404, 'Eintrag nicht gefunden. Ansicht neu laden.')
    except ValueError as error:
        raise HTTPException(422, str(error))
    before = i.document or {}
    i.document = doc.model_dump(exclude={'version'})
    i.document_version += 1
    audit(db, user.username, action, i.name, detail)
    record_history(db, user.username, i.name, 'item.saved', before, i.document)
    db.commit()
    return i.document | {'version': i.document_version}


@router.post('/api/instances/{id}/https/prepare-challenge')
def prepare_challenge(id: int, body: VersionIn, user=Depends(operator), db=Depends(get_db)):
    instance(db, id)
    return save(db, id, user, body, https_assistant.prepare_challenge, 'https.challenge.enabled', 'ACME-Weiterleitung')


@router.post('/api/instances/{id}/https/{kind}/{key}/assign')
def assign(id: int, kind: str, key: str, body: AssignIn, user=Depends(operator), db=Depends(get_db)):
    kind_ok(kind)
    i = instance(db, id)
    cert = next(
        (c for c in core.agent(i, '/certificates') if c['name'] == body.certificate and https_assistant.usable(c)),
        None,
    )
    if cert is None:
        raise HTTPException(422, 'Produktionszertifikat ' + body.certificate + ' fehlt oder ist abgelaufen.')

    def change(doc):
        item = https_assistant.find(doc, kind, key)
        if item is None:
            raise LookupError
        missing = [n for n in item.hostnames if not https_assistant.covers(cert.get('domains', []), n)]
        if missing:
            raise ValueError('Zertifikat ' + body.certificate + ' deckt ' + ', '.join(missing) + ' nicht ab.')
        return https_assistant.assign(doc, kind, key, body.certificate)

    return save(db, id, user, body, change, 'https.assigned', f'{key} → {body.certificate}')
