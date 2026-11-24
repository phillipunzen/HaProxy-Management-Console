import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from backend.db import engine, SessionLocal, get_db, User, BasicAuthDirectory
from backend import basic_auth_api, infrastructures
from backend import migrations as database_migrations
from backend.settings import settings
from backend.core import APP_VERSION, AgentUpdateRequired, admin, audit, current_user, operator, ph
from backend.routes import auth, agents, instances, configuration, monitoring, certificates, checks, https

# Re-exported for scripts and tests that address the application module directly.
import httpx  # noqa: F401,E402
from backend import core  # noqa: F401,E402
from backend.haproxy_config import import_config  # noqa: F401,E402

if len(settings.session_secret) < 32:
    raise RuntimeError('Session secret must contain at least 32 characters')


@asynccontextmanager
async def lifespan(app):
    for attempt in range(30):
        try:
            await asyncio.to_thread(database_migrations.upgrade, engine)
            break
        except Exception:
            if attempt == 29:
                raise
            await asyncio.sleep(2)
    with SessionLocal() as db:
        if not db.scalar(select(User.id).limit(1)):
            db.add(User(username=settings.admin_username, password_hash=ph.hash(settings.admin_password), role='admin'))
            audit(db, 'system', 'admin.bootstrap', settings.admin_username)
            db.commit()
        if not db.get(BasicAuthDirectory, 1):
            db.add(BasicAuthDirectory(id=1))
            db.commit()
    task = asyncio.create_task(monitoring.metric_loop())
    yield
    task.cancel()


app = FastAPI(
    title='HAProxy Control', version=APP_VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
)


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    # Model-level validation otherwise echoes the complete request, including
    # DNS tokens. Field locations and messages are sufficient for the UI.
    return JSONResponse(
        {'detail': [{k: v for k, v in e.items() if k in ('loc', 'msg', 'type')} for e in error.errors()]},
        status_code=422,
    )


app.include_router(basic_auth_api.router(admin, operator, audit))
app.include_router(infrastructures.router(current_user, admin, audit))
for module in (auth, agents, instances, configuration, monitoring, certificates, checks, https):
    app.include_router(module.router)
collect_metrics = monitoring.collect_metrics  # used by tests and scripts


@app.exception_handler(AgentUpdateRequired)
async def agent_update_error(request, error):
    # Keep detail textual for browsers that still run a previously loaded UI.
    return JSONResponse({'detail': error.detail, 'code': 'agent_update_required'}, status_code=error.status_code)


@app.middleware('http')
async def security_headers(request, call_next):
    if request.url.path.startswith('/api') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin and origin != settings.app_origin.rstrip('/'):
            return JSONResponse({'detail': 'Nicht erlaubter Ursprung.'}, status_code=403)
        if request.method != 'DELETE' and request.headers.get('content-type', '').split(';')[0] != 'application/json':
            return JSONResponse({'detail': 'JSON erforderlich.'}, status_code=415)
    length = request.headers.get('content-length', '0')
    if not length.isdigit() or int(length) > 2 * 1024 * 1024:
        return JSONResponse({'detail': 'Anfrage zu groß.'}, status_code=413)
    if request.method in ('POST', 'PUT', 'PATCH') and len(await request.body()) > 2 * 1024 * 1024:
        return JSONResponse({'detail': 'Anfrage zu groß.'}, status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    if request.url.path.startswith('/api'):
        response.headers['Cache-Control'] = 'no-store'
    elif response.headers.get('content-type', '').startswith('text/html'):
        response.headers['Cache-Control'] = 'no-cache'
    return response


@app.get('/api/health')
def health(db=Depends(get_db)):
    db.execute(select(1))
    return {'status': 'ok', 'database': 'MariaDB'}


DIST = Path(__file__).resolve().parent.parent / 'frontend' / 'dist'
if DIST.exists():
    app.mount('/assets', StaticFiles(directory=DIST / 'assets'), name='assets')

    @app.get('/{path:path}')
    def frontend(path: str):
        if path.startswith('api/'):
            raise HTTPException(404)
        candidate = (DIST / path).resolve()
        if candidate.is_relative_to(DIST) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / 'index.html')
