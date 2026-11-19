"""Certificates API routes."""

import json
import re
from pathlib import Path

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse

from backend.db import get_db
from backend.schemas import (
    CertificateIn,
    CertificateAdoptIn,
    CertificateRenewIn,
    RenewalSettingsIn,
    CertificatePolicyIn,
)
from fastapi import APIRouter
from backend import core
from backend.core import audit, current_user, instance, operator

router = APIRouter()


@router.get('/api/instances/{id}/certificates')
def certificates(id: int, user=Depends(current_user), db=Depends(get_db)):
    return core.agent(instance(db, id), '/certificates')


@router.delete('/api/instances/{id}/certificates/{name}')
def delete_certificate(id: int, name: str, staging: bool = False, user=Depends(operator), db=Depends(get_db)):
    if not re.fullmatch(r'[a-zA-Z0-9_-][a-zA-Z0-9_.-]{0,249}', name):
        raise HTTPException(422, 'Ungültiger Zertifikatsname.')
    i = instance(db, id)
    if not staging:
        doc = i.document
        uses = [f'Proxy Host {h.get("domain", "")}' for h in doc.get('hosts', []) if h.get('certificate') == name]
        uses += [
            f'Domain {r.get("domain", "")}' for r in doc.get('imported_routes', []) if r.get('certificate') == name
        ]
        uses += [f'Frontend {f}' for f, names in doc.get('frontend_certificates', {}).items() if name in names]
        if uses:
            raise HTTPException(
                409,
                'Zertifikat ist im Entwurf zugewiesen: '
                + ', '.join(uses)
                + '. Zuerst diese Zuweisungen ändern und die Konfiguration prüfen und anwenden.',
            )
    if not core.agent(i).get('certificate_delete'):
        raise HTTPException(422, 'Für das Löschen von Zertifikaten zuerst unter Server den Agenten aktualisieren.')
    result = core.agent(i, f'/certificates/{name}?staging={str(staging).lower()}', 'DELETE', timeout=65)
    audit(db, user.username, 'certificate.deleted', i.name, name + (' (Staging)' if staging else ' (Produktion)'))
    db.commit()
    return result


@router.post('/api/instances/{id}/certificates/issue')
def issue(id: int, body: CertificateIn, user=Depends(operator), db=Depends(get_db)):
    i = instance(db, id)
    audit(db, user.username, 'certificate.issue.requested', i.name, ','.join(body.domains))
    db.commit()
    try:
        result = acme_request(i, '/certificates/issue', body, 300)
    except HTTPException as e:
        audit(db, user.username, 'certificate.issue.failed', i.name, str(e.detail))
        db.commit()
        raise
    audit(db, user.username, 'certificate.issued', i.name, body.name)
    db.commit()
    return result


@router.post('/api/instances/{id}/certificates/import')
def import_cert(id: int, body: dict, user=Depends(operator), db=Depends(get_db)):
    from agent.main import PemIn

    try:
        payload = PemIn.model_validate(body)
    except ValueError:
        raise HTTPException(422, 'Name und gültiges PEM erforderlich.')
    i = instance(db, id)
    result = core.agent(i, '/certificates/import', 'POST', payload.model_dump(), timeout=65)
    audit(db, user.username, 'certificate.imported', i.name, payload.name)
    db.commit()
    return result


@router.post('/api/instances/{id}/certificates/renew')
def renew(id: int, body: CertificateRenewIn, user=Depends(operator), db=Depends(get_db)):
    i = instance(db, id)
    result = core.agent(i, '/certificates/renew', 'POST', body.model_dump(), timeout=300)
    audit(db, user.username, 'certificates.renewed', i.name)
    db.commit()
    return result


@router.get('/api/instances/{id}/certificates/renewal-settings')
def renewal_settings(id: int, user=Depends(current_user), db=Depends(get_db)):
    return core.agent(instance(db, id), '/certificates/renewal-settings')


@router.put('/api/instances/{id}/certificates/renewal-settings')
def update_renewal_settings(id: int, body: RenewalSettingsIn, user=Depends(operator), db=Depends(get_db)):
    i = instance(db, id)
    result = core.agent(i, '/certificates/renewal-settings', 'PUT', body.model_dump())
    audit(db, user.username, 'certificates.schedule.updated', i.name)
    db.commit()
    return result


@router.put('/api/instances/{id}/certificates/{name}/policy')
def update_certificate_policy(
    id: int, name: str, body: CertificatePolicyIn, user=Depends(operator), db=Depends(get_db)
):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', name):
        raise HTTPException(422, 'Ungültiger Zertifikatsname.')
    i = instance(db, id)
    result = core.agent(i, f'/certificates/{name}/policy', 'PUT', body.model_dump())
    audit(db, user.username, 'certificate.policy.updated', i.name, name)
    db.commit()
    return result


@router.post('/api/instances/{id}/certificates/adopt-lego')
def adopt_lego(id: int, body: CertificateAdoptIn, user=Depends(operator), db=Depends(get_db)):
    i = instance(db, id)
    result = acme_request(i, '/certificates/adopt-lego', body, 65)
    audit(db, user.username, 'certificate.lego.adopted', i.name, body.name)
    db.commit()
    return result


def acme_request(i, path, body, timeout):
    if body.dns_token or body.dns_credential:
        if not core.agent(i).get('dns_credentials_ui'):
            raise HTTPException(
                422,
                'Für DNS-Tokens im Dialog zuerst unter Server den Agenten aktualisieren und Zertifikate neu öffnen.',
            )
    try:
        return core.agent(i, path, 'POST', body.acme_payload(), timeout=timeout)
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, str) else json.dumps(error.detail, ensure_ascii=False)
        for token in (body.dns_token, body.dns_zone_token):
            if token:
                detail = detail.replace(token.get_secret_value(), '[geschützt]')
        raise HTTPException(error.status_code, detail) from None


@router.get('/api/certificate-guide')
def certificate_guide(user=Depends(current_user)):
    return FileResponse(
        Path(__file__).resolve().parent.parent / 'docs' / 'CERTIFICATES.md',
        filename='HAProxy-Zertifikate.md',
        media_type='text/markdown',
    )
