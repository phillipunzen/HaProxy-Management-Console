"""Monitoring API routes."""

import asyncio
import threading
import time

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from backend.db import SessionLocal, get_db, now, Instance
from backend import metrics as metric_store
from backend import topology as topology_store
from backend import proxy_health as proxy_health_store
from backend.settings import settings
from backend.schemas import Document
from backend.haproxy_config import enrich_stats
from fastapi import APIRouter
from backend import core
from backend.core import admin, audit, current_user, instance, logger

router = APIRouter()


collection_lock = threading.Lock()


def collect_metrics():
    if not collection_lock.acquire(blocking=False):
        return
    try:
        with SessionLocal() as db:
            ids = list(db.scalars(select(Instance.id)))
        for id in ids:
            with SessionLocal() as db:
                i = db.get(Instance, id)
                if not i:
                    continue
            # Do not hold a MariaDB transaction during network I/O.
            try:
                data = core.agent(i, '/stats', timeout=8)
            except HTTPException as e:
                data = {'online': False, 'error': str(e.detail)}
            with SessionLocal() as db:
                metric_store.record(db, id, data, now(), settings)
                db.commit()
        metric_store.maintain(SessionLocal, settings)
    finally:
        collection_lock.release()


async def metric_loop():
    while True:
        try:
            await asyncio.to_thread(collect_metrics)
        except Exception:
            logger.exception('Metrics collection failed')
        await asyncio.sleep(max(settings.metrics_interval, 10))


@router.get('/api/instances/{id}/stats')
def stats(id: int, user=Depends(current_user), db=Depends(get_db)):
    i = instance(db, id)
    data = core.agent(i, '/stats')
    try:
        bundle = core.agent(i, '/config-bundle')
        data = enrich_stats(data, bundle['config'], bundle['maps'])
    except HTTPException:
        try:
            data = enrich_stats(data, core.agent(i, '/config')['config'])
        except HTTPException:
            data = enrich_stats(data)
    # Live detail responses never create history rows; only the collector writes.
    return data | {
        'history_policy': {
            'raw_hours': settings.metrics_raw_hours,
            'fine_days': settings.metrics_fine_days,
            'total_days': settings.metrics_retention_days,
        }
    }


@router.get('/api/instances/{id}/topology')
def topology(id: int, user=Depends(current_user), db=Depends(get_db)):
    i = instance(db, id)
    data = core.agent(i, '/stats')
    captured_at = now().isoformat() + 'Z'
    # Only routing inputs are cached, bounded in memory. No runtime or graph rows
    # enter MariaDB. Identity changes invalidate an existing instance's cache.
    identity = (i.agent_url, i.profile, i.token_cipher)
    with core.topology_cache_lock:
        cached = core.topology_cache.get(id)
    config = None
    maps = []
    warning = None
    if cached and cached[0] == identity and time.monotonic() - cached[1] < 30:
        config, maps = cached[2:]
    else:
        try:
            bundle = core.agent(i, '/config-bundle')
            config = bundle['config']
            maps = bundle.get('maps', [])
        except HTTPException:
            try:
                config = core.agent(i, '/config')['config']
            except HTTPException:
                warning = 'Aktive Konfiguration konnte nicht gelesen werden.'
        if config is not None:
            with core.topology_cache_lock:
                core.topology_cache.pop(id, None)
                if len(core.topology_cache) >= 128:
                    core.topology_cache.pop(next(iter(core.topology_cache)))
                core.topology_cache[id] = (identity, time.monotonic(), config, maps)
    graph = topology_store.build(config, maps, data)
    graph['captured_at'] = captured_at
    if warning:
        graph['warnings'].append(warning)
    return graph


@router.get('/api/instances/{id}/proxy-health')
def proxy_health(id: int, user=Depends(current_user), db=Depends(get_db)):
    i = instance(db, id)
    draft = Document.model_validate(i.document or {})
    captured_at = now().isoformat() + 'Z'
    try:
        data = core.agent(i, '/stats')
    except HTTPException:
        data = {'online': False, 'rows': []}
    config = None
    maps = []
    if data.get('online'):
        try:
            bundle = core.agent(i, '/config-bundle')
            config = bundle['config']
            maps = bundle.get('maps', [])
        except HTTPException as error:
            if error.status_code in (404, 405):
                try:
                    config = core.agent(i, '/config')['config']
                except HTTPException:
                    pass
    # Polls read the current runtime and config; no metrics, audits or draft writes.
    return proxy_health_store.build(draft, config, maps, data, captured_at) | {'document_version': i.document_version}


@router.get('/api/instances/{id}/metrics')
def metrics(id: int, hours: int = 1, user=Depends(current_user), db=Depends(get_db)):
    instance(db, id)
    if hours not in (1, 6, 24, 168):
        raise HTTPException(422, 'Zeitraum: 1, 6, 24 oder 168 Stunden.')
    return metric_store.history(db, id, hours, settings)


@router.get('/api/metrics/storage')
def metric_storage(user=Depends(admin), db=Depends(get_db)):
    return metric_store.storage(db, settings)


@router.post('/api/metrics/storage/compact')
def compact_metric_storage(user=Depends(admin), db=Depends(get_db)):
    if not collection_lock.acquire(blocking=False):
        raise HTTPException(409, 'Metriksammlung läuft gerade. Erneut versuchen.')
    try:
        try:
            metric_store.optimize_legacy(db)
        except ValueError as error:
            raise HTTPException(409, str(error))
        except (SQLAlchemyError, RuntimeError) as error:
            db.rollback()
            logger.exception('Legacy metric table optimization failed')
            raise HTTPException(
                422, 'MariaDB konnte die alte Metriktabelle nicht optimieren. Datenbankrechte und Serverlog prüfen.'
            ) from error
        audit(db, user.username, 'metrics.storage.compacted')
        db.commit()
        return metric_store.storage(db, settings)
    finally:
        collection_lock.release()
