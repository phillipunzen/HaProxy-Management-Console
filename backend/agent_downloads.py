"""Standalone, anonymous file downloads; no management imports or credentials."""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)


@app.middleware('http')
async def download_headers(request: Request, call_next):
    # Downloads accept neither uploads nor ranges. Never parse a request body.
    if request.method not in ('GET', 'HEAD'):
        response = JSONResponse({'detail': 'Nur GET und HEAD erlaubt.'}, status_code=405,
                                headers={'Allow': 'GET, HEAD'})
    elif request.headers.get('range'):
        response = JSONResponse({'detail': 'Nur vollständige Downloads erlaubt.'}, status_code=416)
    elif request.headers.get('transfer-encoding') or request.headers.get('content-length', '0') != '0':
        response = JSONResponse({'detail': 'Anfragen ohne Body verwenden.'}, status_code=400)
    else:
        response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'none'; frame-ancestors 'none'; sandbox"
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.api_route('/api/agent-installer', methods=['GET', 'HEAD'])
def installer():
    return FileResponse(ROOT / 'scripts/install-agent.sh', media_type='text/plain',
                        filename='install-agent.sh')


@app.api_route('/api/agent-package', methods=['GET', 'HEAD'])
def package():
    return FileResponse(ROOT / 'downloads/haproxy-management-docker.zip', media_type='application/zip',
                        filename='haproxy-management-docker.zip')
