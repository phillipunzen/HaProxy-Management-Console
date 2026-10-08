"""Minimal HTTP-01 server. Expose behind HAProxy, not the management login."""
import os
import re
from pathlib import Path
from fastapi import FastAPI,HTTPException
from fastapi.responses import FileResponse
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
ROOT=Path(os.environ.get('ACME_WEBROOT','/var/lib/haproxy-control/webroot')).resolve()
@app.get('/.well-known/acme-challenge/{token}')
def challenge(token:str):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,200}',token): raise HTTPException(404)
    path=ROOT/'.well-known/acme-challenge'/token
    if not path.is_file() or not path.resolve().is_relative_to(ROOT): raise HTTPException(404)
    return FileResponse(path,media_type='text/plain')
