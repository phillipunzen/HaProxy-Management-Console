"""Auth API routes."""

import secrets
import time
from datetime import timedelta

from argon2.exceptions import VerifyMismatchError, InvalidHashError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select, delete, func

from backend.db import get_db, now, User, LoginSession, Audit
from backend.settings import settings
from backend.schemas import LoginIn, PasswordIn, UserIn
from fastapi import APIRouter
from backend import core
from backend.core import admin, audit, csrf, current_user, dummy_hash, logger, ph, token_hash, user_data

router = APIRouter()


def warn_untrusted_proxy(request):
    if core.proxy_warning_logged or not request.headers.get('x-forwarded-for'):
        return
    core.proxy_warning_logged = True
    logger.warning(
        'Request from %s carries X-Forwarded-For, but this proxy is not trusted. '
        'Set FORWARDED_ALLOW_IPS to the reverse proxy address so login limits apply per client.',
        request.client.host,
    )


@router.post('/api/auth/login')
def login(body: LoginIn, request: Request, response: Response, db=Depends(get_db)):
    # request.client.host is the real client only if uvicorn trusts the reverse proxy
    # (FORWARDED_ALLOW_IPS); otherwise all users behind a proxy would share one counter.
    warn_untrusted_proxy(request)
    key = (request.client.host, body.username.lower())
    stamp = time.monotonic()
    with core.rate_lock:
        # Also cap attempts across accounts from the same source.
        for k in (key, (request.client.host, '*')):
            while core.failures[k] and core.failures[k][0] < stamp - 900:
                core.failures[k].popleft()
        if len(core.failures[key]) >= 8 or len(core.failures[(request.client.host, '*')]) >= 40:
            raise HTTPException(429, 'Zu viele Anmeldeversuche. Bitte 15 Minuten warten.')
        core.failures[key].append(stamp)
        core.failures[(request.client.host, '*')].append(stamp)
    user = db.scalar(select(User).where(User.username == body.username))
    try:
        ph.verify(user.password_hash if user else dummy_hash, body.password)
    except (VerifyMismatchError, InvalidHashError):
        audit(db, body.username, 'login.failed', detail=request.client.host)
        db.commit()
        raise HTTPException(401, 'Benutzername oder Passwort ist falsch.')
    if not user:
        raise HTTPException(401, 'Benutzername oder Passwort ist falsch.')
    with core.rate_lock:
        core.failures.pop(key, None)
    token = secrets.token_urlsafe(48)
    db.add(LoginSession(token_hash=token_hash(token), user_id=user.id, expires_at=now() + timedelta(hours=12)))
    audit(db, user.username, 'login.success')
    db.commit()
    response.set_cookie(
        'haproxy_session',
        token,
        max_age=43200,
        httponly=True,
        secure=settings.cookie_secure,
        samesite='strict',
        path='/',
    )
    return {'user': user_data(user), 'csrf': csrf(token)}


@router.get('/api/auth/me')
def me(request: Request, user=Depends(current_user)):
    return {'user': user_data(user), 'csrf': csrf(request.cookies['haproxy_session']), 'version': core.APP_VERSION}


@router.post('/api/auth/logout')
def logout(request: Request, response: Response, user=Depends(current_user), db=Depends(get_db)):
    db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(request.cookies['haproxy_session'])))
    db.commit()
    response.delete_cookie('haproxy_session', path='/')
    return {'ok': True}


@router.post('/api/auth/password')
def password(body: PasswordIn, request: Request, response: Response, user=Depends(current_user), db=Depends(get_db)):
    try:
        ph.verify(user.password_hash, body.current_password)
    except VerifyMismatchError:
        raise HTTPException(422, 'Aktuelles Passwort ist falsch.')
    if body.current_password == body.password:
        raise HTTPException(422, 'Bitte ein neues Passwort wählen.')
    user.password_hash = ph.hash(body.password)
    user.must_change_password = False
    # Password changes invalidate every existing session, including the current one.
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    audit(db, user.username, 'password.changed')
    db.commit()
    response.delete_cookie('haproxy_session', path='/')
    return {'ok': True}


@router.get('/api/audit')
def audit_log(user=Depends(admin), db=Depends(get_db)):
    return [
        {
            'id': a.id,
            'actor': a.actor,
            'action': a.action,
            'target': a.target,
            'detail': a.detail,
            'created_at': a.created_at.isoformat() + 'Z',
        }
        for a in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(200))
    ]


@router.get('/api/users')
def users(user=Depends(admin), db=Depends(get_db)):
    return [user_data(u) for u in db.scalars(select(User).order_by(User.id))]


@router.post('/api/users')
def add_user(body: UserIn, user=Depends(admin), db=Depends(get_db)):
    if db.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(409, 'Benutzername bereits vergeben.')
    u = User(username=body.username, password_hash=ph.hash(body.password), role=body.role, must_change_password=True)
    db.add(u)
    audit(db, user.username, 'user.created', body.username, body.role)
    db.commit()
    return user_data(u)


@router.delete('/api/users/{id}')
def delete_user(id: int, user=Depends(admin), db=Depends(get_db)):
    if id == user.id:
        raise HTTPException(422, 'Eigenes Konto kann hier nicht gelöscht werden.')
    u = db.get(User, id)
    if not u:
        raise HTTPException(404)
    if u.role == 'admin' and db.scalar(select(func.count()).select_from(User).where(User.role == 'admin')) <= 1:
        raise HTTPException(422, 'Der letzte Administrator darf nicht gelöscht werden.')
    audit(db, user.username, 'user.deleted', u.username)
    db.delete(u)
    db.commit()
    return {'ok': True}
