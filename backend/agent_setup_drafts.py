"""Persist unfinished agent setup without rotating credentials on UI/process restarts."""
import hmac
import json
import secrets
from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.db import AgentSetupDraft, User, now
from backend.agent_setup import AgentSetupIn, build_plan

IDENTITY=('kind','host','port','profile','config_path','runtime_socket','service','container')

class DraftIdentity(BaseModel):
    setup_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    setup_version: int = Field(ge=1)


def read(row,cipher):
    try:return json.loads(cipher.decrypt(row.data_cipher.encode()))
    except (InvalidToken,ValueError):
        raise HTTPException(503,'Gespeicherte Agent-Einrichtung kann nicht entschlüsselt werden. Den bisherigen ENCRYPTION_KEY in der Management-.env wiederherstellen; bei Neustarts keinen neuen Schlüssel erzeugen.')


def lock(db,user_id):
    # Serialize creation even when no draft row exists yet (including MySQL).
    db.scalar(select(User.id).where(User.id==user_id).with_for_update())
    return db.scalar(select(AgentSetupDraft).where(AgentSetupDraft.user_id==user_id).with_for_update().execution_options(populate_existing=True))


def response(row,cipher,origin,root):
    value=read(row,cipher)
    body=AgentSetupIn.model_validate(value['body'])
    return {'setup_id':row.setup_id,'setup_version':row.version,'body':body.model_dump(exclude={'setup_id','setup_version'}),
            'plan':build_plan(body,origin,root,token=value['token']), 'updated_at':row.updated_at.isoformat()+'Z'}


def save(db,user_id,body,cipher,origin,root):
    row=lock(db,user_id)
    if row:
        if body.setup_id!=row.setup_id or body.setup_version!=row.version:
            raise HTTPException(409,'Eine begonnene Agent-Einrichtung ist vorhanden oder wurde parallel geändert. Assistent schließen und erneut öffnen, um sie mit demselben Token fortzusetzen.')
        previous=read(row,cipher)
        if any(previous['body'][key]!=getattr(body,key) for key in IDENTITY):
            raise HTTPException(409,'Die begonnene Einrichtung gehört zu einem anderen Host, Port oder Profil. Für einen anderen Agenten zuerst diese Einrichtung ausdrücklich verwerfen. Für den bereits installierten Agenten den bisherigen Token verwenden.')
        token=previous['token'];row.version+=1
    else:
        if body.setup_id is not None or body.setup_version is not None:
            raise HTTPException(409,'Diese Einrichtung wurde bereits abgeschlossen oder verworfen. Assistent schließen und neu öffnen.')
        token=secrets.token_urlsafe(48)
        row=AgentSetupDraft(user_id=user_id,setup_id=secrets.token_hex(16),version=1)
        db.add(row)
    data={'body':body.model_dump(exclude={'setup_id','setup_version'}),'token':token}
    row.data_cipher=cipher.encrypt(json.dumps(data).encode()).decode();row.updated_at=now()
    try:db.flush()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409,'Parallel wurde eine Agent-Einrichtung begonnen. Assistent erneut öffnen.') from error
    return response(row,cipher,origin,root)


def discard(db,user_id,identity):
    row=lock(db,user_id)
    if not row or row.setup_id!=identity.setup_id or row.version!=identity.setup_version:
        raise HTTPException(409,'Einrichtung wurde parallel geändert oder bereits abgeschlossen. Assistent erneut öffnen.')
    db.delete(row)


def complete(db,user_id,body,cipher,origin,root):
    row=lock(db,user_id)
    if not row:return
    value=read(row,cipher)
    if not hmac.compare_digest(value['token'],body.token):return
    pending=build_plan(AgentSetupIn.model_validate(value['body']),origin,root,token=value['token'])['instance']
    if pending['profile']==body.profile and pending['agent_url']==body.agent_url:
        db.delete(row)
