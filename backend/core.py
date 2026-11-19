"""Shared state, authentication dependencies and the agent client."""

import os
import hashlib
import hmac
import logging
import secrets
import threading
from collections import defaultdict, deque
from pathlib import Path

import httpx
from argon2 import PasswordHasher
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db import get_db, now, User, LoginSession, Instance, InstanceMetadata, Audit
from backend import basic_auth, infrastructures
from backend.settings import settings


# Set from the Git tag or commit when the image is built; shown to signed-in users only.
APP_VERSION = os.environ.get('APP_VERSION') or 'dev'
ph = PasswordHasher()
cipher = Fernet(settings.encryption_key.encode())
logger = logging.getLogger('haproxy-control')
failures = defaultdict(deque)
rate_lock = threading.Lock()
dummy_hash = ph.hash(secrets.token_urlsafe(32))
topology_cache = {}
topology_cache_lock = threading.Lock()


def audit(db, actor, action, target='', detail=''):
    db.add(Audit(actor=actor, action=action, target=str(target), detail=detail))


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def csrf(token):
    return hmac.new(settings.session_secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def user_data(user):
    return {
        'id': user.id,
        'username': user.username,
        'role': user.role,
        'must_change_password': user.must_change_password,
    }


def current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get('haproxy_session', '')
    session = db.get(LoginSession, token_hash(token)) if token else None
    if not session or session.expires_at < now():
        raise HTTPException(401, 'Bitte anmelden.')
    user = db.get(User, session.user_id)
    if not user:
        raise HTTPException(401, 'Sitzung ungültig.')
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if not hmac.compare_digest(request.headers.get('x-csrf-token', ''), csrf(token)):
            raise HTTPException(403, 'Sicherheitsprüfung fehlgeschlagen. Seite neu laden.')
    if user.must_change_password and request.url.path not in ('/api/auth/me', '/api/auth/password', '/api/auth/logout'):
        raise HTTPException(403, 'Bitte zuerst das Startpasswort ändern.')
    return user


def operator(user=Depends(current_user)):
    if user.role not in ('admin', 'operator'):
        raise HTTPException(403, 'Bearbeitungsrechte erforderlich.')
    return user


def admin(user=Depends(current_user)):
    if user.role != 'admin':
        raise HTTPException(403, 'Administratorrechte erforderlich.')
    return user


def instance(db, id):
    value = db.get(Instance, id)
    if not value:
        raise HTTPException(404, 'Instanz nicht gefunden.')
    return value


def public_instance(i, metadata=None, infrastructure=None):
    return {
        'id': i.id,
        'name': i.name,
        'agent_url': i.agent_url,
        'profile': i.profile,
        'kind': i.kind,
        'notes': i.notes,
        'allow_http': i.allow_http,
        'created_at': i.created_at.isoformat() + 'Z',
        'tags': metadata.tags if metadata else [],
        'location': metadata.location if metadata else '',
        'metadata_version': metadata.version if metadata else 0,
        'infrastructure_id': infrastructure.id if infrastructure else None,
        'infrastructure_name': infrastructure.name if infrastructure else None,
    }


def store_instance_metadata(db, id, body, check_version=True):
    value = db.scalar(
        select(InstanceMetadata)
        .where(InstanceMetadata.instance_id == id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if check_version and body.metadata_version != (value.version if value else 0):
        raise HTTPException(409, 'Server-Zuordnung wurde parallel geändert. Bitte neu laden.')
    if not value:
        value = InstanceMetadata(instance_id=id, tags=[], location='', version=0)
        db.add(value)
    for key in ('tags', 'location'):
        if key in body.model_fields_set:
            setattr(value, key, getattr(body, key))
    if 'infrastructure_id' in body.model_fields_set:
        infrastructures.assign(db, id, body.infrastructure_id)
    if check_version:
        value.version += 1
    return value


def agent(i, path='', method='GET', body=None, timeout=10):
    try:
        with httpx.Client(
            timeout=timeout, trust_env=False, follow_redirects=False, verify=settings.agent_ca_file or True
        ) as client:
            result = client.request(
                method,
                i.agent_url + '/profiles/' + i.profile + path,
                headers={'Authorization': 'Bearer ' + cipher.decrypt(i.token_cipher.encode()).decode()},
                json=body,
            )
        if result.status_code >= 400:
            try:
                detail = result.json().get('detail', 'Agent-Fehler')
            except ValueError:
                detail = 'Agent antwortet mit einem Fehler.'
            # A missing endpoint is distinct from a failed file read. Agent authentication
            # also must not expire the user's management session in the browser.
            if result.status_code == 401:
                raise HTTPException(
                    502, 'Agent hat den Zugriff verweigert. Profilname und Agent-Token dieser Serververbindung prüfen.'
                )
            raise HTTPException(
                result.status_code if result.status_code in (403, 404, 405, 409, 422, 502, 504) else 502, detail
            )
        if result.status_code >= 300:
            raise HTTPException(502, 'Agent-Weiterleitungen sind nicht erlaubt.')
        return result.json()
    except InvalidToken:
        raise HTTPException(
            503,
            'Gespeicherter Agent-Token kann nicht entschlüsselt werden. Den bisherigen ENCRYPTION_KEY in der Management-.env wiederherstellen; bei Neustarts keinen neuen Schlüssel erzeugen.',
        )
    except (httpx.RequestError, ValueError):
        raise HTTPException(502, 'Agent nicht erreichbar. Adresse, TLS-Zertifikat und Firewall prüfen.')


def validate_document(db, doc):
    from backend.access_control import validate_document as validate_access

    validate_access(doc)
    from backend.managed_rules import validate_document as validate_rules

    validate_rules(doc)
    basic_auth.validate_document(db, doc)


class AgentUpdateRequired(HTTPException):
    def __init__(self, message):
        super().__init__(422, message)


SETUP_ROOT = Path(__file__).resolve().parent.parent

proxy_warning_logged = False
