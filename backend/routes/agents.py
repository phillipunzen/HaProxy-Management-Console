"""Agents API routes."""

import json
import secrets
from pathlib import Path

from fastapi import Depends, Response
from fastapi.responses import FileResponse

from backend.db import get_db, AgentSetupDraft
from backend import agent_setup_drafts
from backend.settings import settings
from backend.agent_setup import AgentSetupIn, build_update_command
from fastapi import APIRouter
from backend import core
from backend.core import SETUP_ROOT, admin, audit, instance

router = APIRouter()


@router.get('/api/agent-setup')
def agent_setup_defaults(user=Depends(admin), db=Depends(get_db)):
    origin = settings.agent_download_origin or settings.app_origin
    row = db.get(AgentSetupDraft, user.id)
    return {
        'download_origin': origin,
        'pending_setup': agent_setup_drafts.response(row, core.cipher, origin, SETUP_ROOT) if row else None,
    }


@router.post('/api/agent-setup')
def agent_setup(body: AgentSetupIn, user=Depends(admin), db=Depends(get_db)):
    draft = agent_setup_drafts.save(
        db, user.id, body, core.cipher, settings.agent_download_origin or settings.app_origin, SETUP_ROOT
    )
    audit(db, user.username, 'agent.setup.saved', body.name)
    db.commit()
    return draft['plan'] | {'setup_id': draft['setup_id'], 'setup_version': draft['setup_version']}


@router.delete('/api/agent-setup')
def agent_setup_discard(body: agent_setup_drafts.DraftIdentity, user=Depends(admin), db=Depends(get_db)):
    agent_setup_drafts.discard(db, user.id, body)
    audit(db, user.username, 'agent.setup.discarded')
    db.commit()
    return {'ok': True}


@router.post('/api/instances/{id}/agent-update')
def agent_update(id: int, user=Depends(admin), db=Depends(get_db)):
    instance(db, id)
    return build_update_command(settings.agent_download_origin or settings.app_origin, SETUP_ROOT)


@router.get('/api/agent-installer')
def agent_installer():
    # Public installation code; credentials exist only in the admin's setup plan.
    return FileResponse(SETUP_ROOT / 'scripts/install-agent.sh', media_type='text/plain')


@router.get('/api/agent-package')
def agent_package():
    return FileResponse(
        SETUP_ROOT / 'downloads/haproxy-management-docker.zip',
        media_type='application/zip',
        filename='haproxy-management-docker.zip',
    )


@router.get('/api/agent-guide')
def agent_guide(user=Depends(admin)):
    return FileResponse(
        Path(__file__).resolve().parent.parent / 'docs' / 'AGENT.md',
        filename='HAProxy-Agent-Anleitung.md',
        media_type='text/markdown',
    )


@router.get('/api/agent-config')
def agent_config(user=Depends(admin)):
    path = Path(__file__).resolve().parent.parent / 'agent' / 'config.example.json'
    content = json.loads(path.read_text())
    for profile in content['profiles'].values():
        profile['token'] = secrets.token_urlsafe(48)
    return Response(
        json.dumps(content, indent=2),
        media_type='application/json',
        headers={'Content-Disposition': 'attachment; filename="agent.json"', 'Cache-Control': 'no-store'},
    )
