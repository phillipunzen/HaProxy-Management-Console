"""Scalar history stays bounded and migration preserves peaks and availability."""
import json
import os
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace

# CI has no deployment .env. Never override local integration credentials.
if not Path('.env').exists():
    from cryptography.fernet import Fernet
    for key,value in {'DB_PASSWORD':'test','ENCRYPTION_KEY':Fernet.generate_key().decode(),
        'SESSION_SECRET':'s'*48,'ADMIN_PASSWORD':'test-password'}.items():os.environ.setdefault(key,value)

import pytest
from sqlalchemy import create_engine,select,func,event,delete
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from backend import metrics as metrics
from backend.db import Base,Instance,Metric,MetricLatest,MetricBucket

AT=datetime(2026,10,8,12)
CFG=SimpleNamespace(metrics_interval=30,metrics_raw_hours=2,metrics_fine_days=1,metrics_retention_days=7)

@pytest.fixture
def database():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    @event.listens_for(engine,'connect')
    def foreign_keys(connection,record):connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine);factory=sessionmaker(engine,expire_on_commit=False)
    with factory() as db:
        instance=Instance(name='Test',agent_url='http://192.0.2.1:9101',profile='lab',token_cipher='unused',document={})
        db.add(instance);db.commit();id=instance.id
    yield factory,id
    engine.dispose()


def sample(rate=100,sessions=20,online=True):
    return dict(online=online,request_rate=rate,sessions=sessions,bytes_in=1000,bytes_out=2000,
        requests=5000,errors_5xx=3,version='3.2.25',uptime='1h',
        info={'huge':'unused'*10000},rows=[{'pxname':'backend','svname':'server','unused':'x'*3000}]*200,
        proxies=[{'unused':'x'*3000}]*200)


def count(db,model):return db.scalar(select(func.count()).select_from(model))


def test_compact_summary_has_no_runtime_arrays_or_arbitrary_metadata():
    full=sample();full['error']='x'*2000
    result=metrics.compact(full)
    assert len(json.dumps(full))>1_000_000 and len(json.dumps(result))<650
    assert 'rows' not in result and 'proxies' not in result and 'info' not in result
    assert result['request_rate']==100 and len(result['error'])==300
    offline=metrics.compact(sample(online=False))
    assert offline['request_rate'] is None and offline['sessions'] is None

@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1,True,'n/a',2**64,None])
def test_invalid_numeric_measurements_are_not_stored(value):assert metrics.number(value) is None


def test_large_integer_counters_keep_precision():
    assert metrics.number(str(2**64-1))==2**64-1
    assert metrics.number(2**64-1)==2**64-1


def test_persistent_deduplication_latest_and_all_three_tiers(database):
    factory,id=database
    with factory() as db:
        assert metrics.record(db,id,sample(),AT+timedelta(seconds=5),CFG);db.commit()
        assert not metrics.record(db,id,sample(9999),AT+timedelta(seconds=20),CFG);db.commit()
    # Opening a new session simulates a collector/app restart.
    with factory() as db:
        assert not metrics.record(db,id,sample(9999),AT+timedelta(seconds=29),CFG)
        assert metrics.record(db,id,sample(300),AT+timedelta(seconds=35),CFG);db.commit()
        assert count(db,Metric)==0 and count(db,MetricLatest)==1 and count(db,MetricBucket)==4
        latest=db.get(MetricLatest,id);assert latest.data['request_rate']==300 and 'rows' not in latest.data
        coarse=db.get(MetricBucket,(id,3600,AT));assert coarse.samples==2 and coarse.rate_peak==300 and coarse.rate_sum==400


def test_averages_peaks_missing_fields_and_outages(database):
    factory,id=database;cfg=SimpleNamespace(**(vars(CFG)|{'metrics_interval':10}))
    with factory() as db:
        for offset,data in [(1,sample(100,20)),(11,sample(300,40)),(21,sample(online=False)),(31,sample(online=False))]:
            assert metrics.record(db,id,data,AT+timedelta(seconds=offset),cfg);db.commit()
        points=metrics.history(db,id,1,cfg,AT+timedelta(seconds=59))
        assert len(points)==2
        assert points[0]['request_rate']==200 and points[0]['request_rate_peak']==300
        assert points[0]['sessions']==30 and points[0]['sessions_peak']==40
        assert points[0]['availability']==pytest.approx(2/3) and points[0]['samples']==3
        assert points[1]['online'] is False and points[1]['request_rate'] is None


def test_time_bins_are_utc_and_windows_choose_bounded_tier():
    assert metrics.bucket_time(AT+timedelta(minutes=4,seconds=59),300)==AT
    assert [metrics.resolution_for(hours,CFG) for hours in (1,6,24,168)]==[30,300,300,3600]
    custom=SimpleNamespace(**(vars(CFG)|{'metrics_raw_hours':24,'metrics_fine_days':7}))
    assert metrics.resolution_for(24,custom)==300 and metrics.resolution_for(168,custom)==3600


def test_tier_pruning_keeps_other_tiers(database):
    factory,id=database
    with factory() as db:
        for age in (timedelta(minutes=10),timedelta(hours=3),timedelta(days=2),timedelta(days=8)):
            metrics.record(db,id,sample(),AT-age,CFG);db.commit()
        # Records submitted out of order are correctly rejected by latest guard.
        # Seed explicit old bins to exercise cleanup independently of sampling.
        for seconds in metrics.RESOLUTIONS:
            for age in (timedelta(hours=3),timedelta(days=2),timedelta(days=8)):
                start=metrics.bucket_time(AT-age,seconds)
                if db.get(MetricBucket,(id,seconds,start)) is None:db.add(metrics.empty_bucket(id,seconds,start))
        db.commit();metrics.prune_batch(db,CFG,AT)
        tiers={seconds:db.scalar(select(func.count()).select_from(MetricBucket).where(MetricBucket.resolution==seconds)) for seconds in metrics.RESOLUTIONS}
        assert tiers=={30:1,300:2,3600:3}


def test_migration_projects_scalars_and_commits_idempotently(database):
    factory,id=database
    with factory() as db:
        db.add_all([Metric(instance_id=id,collected_at=AT-timedelta(minutes=20),data=sample(10)),
                    Metric(instance_id=id,collected_at=AT-timedelta(minutes=10),data=sample(500)),
                    Metric(instance_id=id,collected_at=AT-timedelta(minutes=5),data=sample(online=False))]);db.commit()
        assert 'metrics.data,' not in str(metrics.project_legacy())
        assert metrics.migrate_batch(db,CFG,AT,batch_size=2)==2
        assert count(db,Metric)==1
        assert metrics.migrate_batch(db,CFG,AT,batch_size=2)==1
        assert metrics.migrate_batch(db,CFG,AT,batch_size=2)==0
        coarse=db.get(MetricBucket,(id,3600,AT-timedelta(hours=1)))
        assert coarse.samples==3 and coarse.online_samples==2 and coarse.rate_peak==500
        assert coarse.rate_sum==510 and coarse.rate_count==2
        assert db.get(MetricLatest,id).collected_at==AT-timedelta(minutes=5)
        assert count(db,Metric)==0


def test_migration_failure_rolls_back_buckets_and_original_deletion(database,monkeypatch):
    factory,id=database
    with factory() as db:db.add(Metric(instance_id=id,collected_at=AT-timedelta(minutes=1),data=sample()));db.commit()
    with factory() as db:
        def failed_commit():raise RuntimeError('interrupted')
        monkeypatch.setattr(db,'commit',failed_commit)
        with pytest.raises(RuntimeError):metrics.migrate_batch(db,CFG,AT)
        db.rollback()
    with factory() as db:
        assert count(db,Metric)==1 and count(db,MetricBucket)==0
        assert metrics.migrate_batch(db,CFG,AT)==1
        assert all(b.samples==1 for b in db.scalars(select(MetricBucket)))


def test_migration_respects_retention_and_never_overwrites_fresh_latest(database):
    factory,id=database
    with factory() as db:
        metrics.record(db,id,sample(123),AT,CFG);db.commit()
        for age in (timedelta(hours=3),timedelta(days=2),timedelta(days=8)):
            db.add(Metric(instance_id=id,collected_at=AT-age,data=sample(999)))
        db.commit();assert metrics.migrate_batch(db,CFG,AT)==3
        assert count(db,Metric)==0 and db.get(MetricLatest,id).data['request_rate']==123
        assert count(db,MetricBucket)==6
        assert db.get(MetricBucket,(id,30,metrics.bucket_time(AT-timedelta(hours=3),30))) is None
        assert db.get(MetricBucket,(id,300,metrics.bucket_time(AT-timedelta(days=2),300))) is None


def test_deleting_instance_cascades_all_metric_storage(database):
    factory,id=database
    with factory() as db:
        metrics.record(db,id,sample(),AT,CFG);db.commit();db.execute(delete(Instance).where(Instance.id==id));db.commit()
        assert count(db,MetricLatest)==0 and count(db,MetricBucket)==0
        assert metrics.record(db,id,sample(),AT,CFG) is False


def test_stats_page_requests_never_persist_measurements(database,monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import HTTPException
    from backend import main
    factory,id=database
    def get_db():
        with factory() as db:yield db
    main.app.dependency_overrides[main.get_db]=get_db
    main.app.dependency_overrides[main.current_user]=lambda:SimpleNamespace(id=1,username='test',role='viewer')
    def fake_agent(i,path='',**kw):
        if path=='/stats':return sample()
        raise HTTPException(502,'test agent does not expose config')
    monkeypatch.setattr(main,'agent',fake_agent)
    try:
        client=TestClient(main.app)
        for _ in range(12):
            response=client.get(f'/api/instances/{id}/stats');assert response.status_code==200
            assert len(response.json()['rows'])==200
        assert client.get('/api/metrics/storage').status_code==403
        assert client.post('/api/metrics/storage/compact',json={}).status_code==403
        with factory() as db:
            assert count(db,Metric)==0 and count(db,MetricBucket)==0 and count(db,MetricLatest)==0
        # Instance summaries use exactly one compact latest row, not history arrays.
        with factory() as db:metrics.record(db,id,sample(),AT,CFG);db.commit()
        response=client.get('/api/instances');assert response.status_code==200
        assert response.json()[0]['stats']['request_rate']==100 and 'rows' not in response.json()[0]['stats']
        assert client.get(f'/api/instances/{id}/metrics?hours=2').status_code==422
    finally:main.app.dependency_overrides.clear()


def test_storage_bounds_and_settings_validation(database):
    from backend.settings import Settings
    from pydantic import ValidationError
    factory,id=database
    with factory() as db:
        values=metrics.storage(db,CFG);assert values['max_buckets_per_instance']==699 and values['legacy_rows']==0
    with pytest.raises(ValidationError):Settings(_env_file=None,db_password='test',encryption_key='test',session_secret='x'*32,admin_password='test-password',metrics_interval=0)
    with pytest.raises(ValidationError):Settings(_env_file=None,db_password='test',encryption_key='test',session_secret='x'*32,admin_password='test-password',metrics_fine_days=10,metrics_retention_days=7)


def test_missing_bins_stop_interpolation_across_collection_gaps(database):
    factory,id=database
    with factory() as db:
        metrics.record(db,id,sample(),AT,CFG);db.commit()
        metrics.record(db,id,sample(300),AT+timedelta(minutes=1),CFG);db.commit()
        points=metrics.history(db,id,1,CFG,AT+timedelta(minutes=1))
        assert len(points)==3 and points[1]['samples']==0
        assert points[1]['request_rate'] is None and points[1]['availability'] is None


def test_table_optimization_is_blocked_until_legacy_rows_are_migrated(database):
    factory,id=database
    with factory() as db:
        db.add(Metric(instance_id=id,collected_at=AT,data=sample()));db.commit()
        with pytest.raises(ValueError,match='Übernahme'):metrics.optimize_legacy(db)
        assert count(db,Metric)==1
