"""Instances API routes."""

import secrets

from fastapi import Depends, HTTPException
from sqlalchemy import select

from backend.db import get_db, Instance, InstanceMetadata, Infrastructure, InstanceInfrastructure, MetricLatest
from backend import agent_setup_drafts
from backend import infrastructures
from backend.settings import settings
from backend.schemas import InstanceIn, InstanceUpdateIn, InstanceMetadataIn, Document
from fastapi import APIRouter
from backend import core
from backend.core import (
    AgentUpdateRequired,
    SETUP_ROOT,
    admin,
    audit,
    current_user,
    instance,
    public_instance,
    store_instance_metadata,
)

router = APIRouter()


@router.get('/api/instances')
def list_instances(user=Depends(current_user), db=Depends(get_db)):
    out = []
    query = (
        select(Instance, MetricLatest, InstanceMetadata, Infrastructure)
        .outerjoin(MetricLatest, MetricLatest.instance_id == Instance.id)
        .outerjoin(InstanceMetadata, InstanceMetadata.instance_id == Instance.id)
        .outerjoin(InstanceInfrastructure, InstanceInfrastructure.instance_id == Instance.id)
        .outerjoin(Infrastructure, Infrastructure.id == InstanceInfrastructure.infrastructure_id)
        .order_by(Instance.id)
    )
    for i, metric, metadata, infrastructure in db.execute(query):
        out.append(
            public_instance(i, metadata, infrastructure)
            | {
                'stats': metric.data if metric else None,
                'collected_at': metric.collected_at.isoformat() + 'Z' if metric else None,
                'fresh_for_seconds': max(90, settings.metrics_interval * 3),
            }
        )
    return out


@router.post('/api/instances')
def add_instance(body: InstanceIn, user=Depends(admin), db=Depends(get_db)):
    duplicate = db.scalar(
        select(Instance.id).where(Instance.agent_url == body.agent_url, Instance.profile == body.profile)
    )
    if duplicate:
        raise HTTPException(409, 'Dieses Agent-Profil ist bereits angebunden.')
    i = Instance(
        name=body.name,
        agent_url=body.agent_url,
        profile=body.profile,
        allow_http=body.allow_http,
        notes=body.notes,
        token_cipher=core.cipher.encrypt(body.token.encode()).decode(),
        document=Document().model_dump(),
    )
    cap = core.agent(i)
    i.kind = cap['kind']
    db.add(i)
    db.flush()
    metadata = store_instance_metadata(db, i.id, body, check_version=False)
    agent_setup_drafts.complete(
        db, user.id, body, core.cipher, settings.agent_download_origin or settings.app_origin, SETUP_ROOT
    )
    audit(db, user.username, 'instance.created', i.name)
    db.commit()
    return public_instance(i, metadata, infrastructures.assignment(db, i.id))


@router.put('/api/instances/{id}')
def edit_instance(id: int, body: InstanceUpdateIn, user=Depends(admin), db=Depends(get_db)):
    i = instance(db, id)
    if db.scalar(
        select(Instance.id).where(
            Instance.id != id, Instance.agent_url == body.agent_url, Instance.profile == body.profile
        )
    ):
        raise HTTPException(409, 'Dieses Agent-Profil ist bereits angebunden.')
    before = (i.agent_url, i.profile, i.token_cipher, i.allow_http)
    token_cipher = core.cipher.encrypt(body.token.encode()).decode() if body.token else i.token_cipher
    connection_changed = (body.agent_url, body.profile, token_cipher, body.allow_http) != before
    candidate = Instance(
        agent_url=body.agent_url, profile=body.profile, token_cipher=token_cipher, allow_http=body.allow_http
    )
    cap = core.agent(candidate) if connection_changed else {'kind': i.kind}
    db.rollback()
    i = db.scalar(select(Instance).where(Instance.id == id).with_for_update().execution_options(populate_existing=True))
    if not i:
        raise HTTPException(404, 'Instanz wurde entfernt.')
    if (i.agent_url, i.profile, i.token_cipher, i.allow_http) != before:
        raise HTTPException(409, 'Serververbindung wurde parallel geändert. Bitte neu laden.')
    metadata = (
        store_instance_metadata(db, id, body)
        if {'tags', 'location', 'infrastructure_id'} & body.model_fields_set
        else db.get(InstanceMetadata, id)
    )
    for key in ('name', 'agent_url', 'profile', 'allow_http', 'notes'):
        setattr(i, key, getattr(body, key))
    i.token_cipher = candidate.token_cipher
    i.kind = cap['kind']
    audit(db, user.username, 'instance.updated', i.name)
    db.commit()
    return public_instance(i, metadata, infrastructures.assignment(db, id))


@router.post('/api/instances/{id}/rotate-token')
def rotate_agent_token(id: int, user=Depends(admin), db=Depends(get_db)):
    i = db.scalar(select(Instance).where(Instance.id == id).with_for_update())
    if not i:
        raise HTTPException(404, 'Instanz nicht gefunden.')
    if not core.agent(i).get('token_rotation'):
        raise AgentUpdateRequired(
            'Für die Token-Erneuerung den Agenten auf dem HAProxy-Server unter Server aktualisieren.'
        )
    token = secrets.token_urlsafe(48)
    token_cipher = core.cipher.encrypt(token.encode()).decode()
    try:
        core.agent(i, '/token', 'POST', {'token': token})
    except HTTPException as error:
        if error.status_code != 502:
            raise
        # The response may be lost after the agent already switched: probe with the new token.
        probe = Instance(agent_url=i.agent_url, profile=i.profile, token_cipher=token_cipher, allow_http=i.allow_http)
        try:
            core.agent(probe)
        except HTTPException:
            raise error
    i.token_cipher = token_cipher
    audit(db, user.username, 'instance.token_rotated', i.name)
    db.commit()
    return {'ok': True}


@router.put('/api/instances/{id}/metadata')
def edit_instance_metadata(id: int, body: InstanceMetadataIn, user=Depends(admin), db=Depends(get_db)):
    # End the authentication snapshot before locking; metadata edits never call
    # an agent and must also work when that HAProxy host is offline.
    db.rollback()
    i = db.scalar(select(Instance).where(Instance.id == id).with_for_update().execution_options(populate_existing=True))
    if not i:
        raise HTTPException(404, 'Instanz nicht gefunden.')
    metadata = store_instance_metadata(db, id, body)
    audit(db, user.username, 'instance.metadata.updated', i.name)
    db.commit()
    return public_instance(i, metadata, infrastructures.assignment(db, id))


@router.delete('/api/instances/{id}')
def remove_instance(id: int, user=Depends(admin), db=Depends(get_db)):
    i = instance(db, id)
    audit(db, user.username, 'instance.removed', i.name)
    db.delete(i)
    db.commit()
    return {'ok': True}


@router.get('/api/instances/{id}/capabilities')
def capabilities(id: int, user=Depends(current_user), db=Depends(get_db)):
    return core.agent(instance(db, id))
