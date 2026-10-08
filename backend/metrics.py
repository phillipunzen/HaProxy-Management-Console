"""Bounded scalar history, persistent time buckets, and legacy snapshot migration.

Only the collector writes new measurements. GET /stats returns live proxy rows
without storing them. Each measurement contributes once to every retained tier.
Legacy rows are projected on the database server; their large runtime arrays are
never transferred into the application during migration.
"""
import math
import time
from datetime import datetime,timedelta,timezone

from sqlalchemy import select,delete,func,tuple_,text
from backend.db import Metric,MetricLatest,MetricBucket,Instance,LoginSession,now

NUMBERS=('request_rate','sessions','bytes_in','bytes_out','requests','errors_5xx')
TEXT_LIMITS={'version':100,'uptime':100,'error':300}
RESOLUTIONS=(30,300,3600)


def number(value):
    if isinstance(value,bool) or value is None:return None
    try:
        if isinstance(value,int) or isinstance(value,str) and value.isdigit():
            value=int(value)
            return value if 0<=value<=2**64-1 else None
        value=float(value)
        if not math.isfinite(value) or value<0 or value>2**64-1:return None
        return int(value) if value.is_integer() else value
    except (ValueError,TypeError,OverflowError):return None


def compact(data):
    # Accept SQL-projected JSON booleans as well as agent booleans.
    online=data.get('online') in (True,1,'true','1')
    result={'online':online}
    for key in NUMBERS:result[key]=number(data.get(key)) if online else None
    for key,limit in TEXT_LIMITS.items():
        if isinstance(data.get(key),str):result[key]=data[key][:limit]
    return result


def bucket_time(at,seconds):
    # DB timestamps are naive UTC. Never depend on the host's local timezone.
    stamp=at.replace(tzinfo=timezone.utc).timestamp()
    return datetime.fromtimestamp(math.floor(stamp/seconds)*seconds,timezone.utc).replace(tzinfo=None)


def retentions(config):
    return {30:timedelta(hours=config.metrics_raw_hours),300:timedelta(days=config.metrics_fine_days),3600:timedelta(days=config.metrics_retention_days)}


def empty_bucket(instance_id,resolution,start):
    return MetricBucket(instance_id=instance_id,resolution=resolution,bucket_start=start,
        samples=0,online_samples=0,rate_sum=0,rate_count=0,rate_peak=None,
        sessions_sum=0,sessions_count=0,sessions_peak=None,last_at=start,last_data={})


def add(bucket,data,at):
    bucket.samples+=1;bucket.online_samples+=int(data['online'])
    for field,prefix in (('request_rate','rate'),('sessions','sessions')):
        value=data[field]
        if value is not None:
            setattr(bucket,prefix+'_sum',getattr(bucket,prefix+'_sum')+value)
            setattr(bucket,prefix+'_count',getattr(bucket,prefix+'_count')+1)
            peak=getattr(bucket,prefix+'_peak')
            setattr(bucket,prefix+'_peak',value if peak is None else max(peak,value))
    if at>=bucket.last_at:
        bucket.last_at=at;bucket.last_data={key:data[key] for key in ('online',*NUMBERS)}


def merge(bucket,incoming):
    for key in ('samples','online_samples','rate_sum','rate_count','sessions_sum','sessions_count'):
        setattr(bucket,key,getattr(bucket,key)+getattr(incoming,key))
    for key in ('rate_peak','sessions_peak'):
        values=[value for value in (getattr(bucket,key),getattr(incoming,key)) if value is not None]
        setattr(bucket,key,max(values) if values else None)
    if incoming.last_at>=bucket.last_at:
        bucket.last_at=incoming.last_at;bucket.last_data=incoming.last_data


def record(db,instance_id,data,at,config):
    # Serialize collector writes across restarts/replicas using the instance row.
    # A persisted time-slot check stops repeated measurements in one interval.
    if db.scalar(select(Instance.id).where(Instance.id==instance_id).with_for_update()) is None:return False
    latest=db.get(MetricLatest,instance_id)
    slot=bucket_time(at,config.metrics_interval)
    if latest and latest.collected_at>=slot:return False
    data=compact(data)
    if latest is None:db.add(MetricLatest(instance_id=instance_id,collected_at=at,data=data))
    else:latest.collected_at=at;latest.data=data
    for seconds in RESOLUTIONS:
        key=(instance_id,seconds,bucket_time(at,seconds));bucket=db.get(MetricBucket,key)
        if bucket is None:bucket=empty_bucket(*key);db.add(bucket)
        add(bucket,data,at)
    return True


def project_legacy():
    return select(Metric.id,Metric.instance_id,Metric.collected_at,
        *[Metric.data[key].as_string().label(key) for key in ('online',*NUMBERS,*TEXT_LIMITS)]).order_by(Metric.id.desc())


def migrate_batch(db,config,at=None,batch_size=500):
    """Aggregate and remove a batch atomically; a crash cannot double-count it."""
    at=at or now()
    keys=list(db.execute(select(Metric.id,Metric.instance_id).order_by(Metric.id.desc()).limit(batch_size)))
    if not keys:return 0
    # Lock parent instances in deterministic order, also protecting FK deletion.
    ids=sorted({row.instance_id for row in keys})
    alive=set(db.scalars(select(Instance.id).where(Instance.id.in_(ids)).order_by(Instance.id).with_for_update()))
    # Parent locks come first, matching the collector and ON DELETE CASCADE.
    rows=db.execute(project_legacy().where(Metric.id.in_([key.id for key in keys])).with_for_update()).mappings().all()
    if not rows:return 0
    groups={};latest_values={};limits=retentions(config)
    for row in rows:
        id=row['instance_id'];collected=row['collected_at']
        if id not in alive or collected<at-limits[3600]:continue
        data=compact(dict(row))
        if id not in latest_values or collected>=latest_values[id][0]:latest_values[id]=(collected,data)
        for seconds,retention in limits.items():
            if collected<at-retention:continue
            key=(id,seconds,bucket_time(collected,seconds))
            if key not in groups:groups[key]=empty_bucket(*key)
            add(groups[key],data,collected)
    if groups:
        existing={ (b.instance_id,b.resolution,b.bucket_start):b for b in db.scalars(select(MetricBucket).where(tuple_(MetricBucket.instance_id,MetricBucket.resolution,MetricBucket.bucket_start).in_(list(groups)))) }
        for key,incoming in groups.items():
            if key in existing:merge(existing[key],incoming)
            else:db.add(incoming)
    if latest_values:
        existing={m.instance_id:m for m in db.scalars(select(MetricLatest).where(MetricLatest.instance_id.in_(latest_values)))}
        for id,(collected,data) in latest_values.items():
            latest=existing.get(id)
            if latest is None:db.add(MetricLatest(instance_id=id,collected_at=collected,data=data))
            elif collected>latest.collected_at:latest.collected_at=collected;latest.data=data
    db.execute(delete(Metric).where(Metric.id.in_([row['id'] for row in rows])))
    db.commit()
    return len(rows)


def prune_batch(db,config,at=None,batch_size=1000):
    at=at or now();removed=0
    for seconds,retention in retentions(config).items():
        keys=list(db.execute(select(MetricBucket.instance_id,MetricBucket.resolution,MetricBucket.bucket_start)
            .where(MetricBucket.resolution==seconds,MetricBucket.bucket_start<bucket_time(at-retention,seconds))
            .order_by(MetricBucket.bucket_start).limit(batch_size)))
        if keys:
            db.execute(delete(MetricBucket).where(tuple_(MetricBucket.instance_id,MetricBucket.resolution,MetricBucket.bucket_start).in_(keys)))
            db.commit();removed+=len(keys)
    return removed


def maintain(session_factory,config,budget_seconds=2):
    # Small commits and a time budget keep large existing installations responsive.
    deadline=time.monotonic()+budget_seconds
    with session_factory() as db:
        prune_batch(db,config)
        db.execute(delete(LoginSession).where(LoginSession.expires_at<now()));db.commit()
    while time.monotonic()<deadline:
        with session_factory() as db:
            if not migrate_batch(db,config):break


def resolution_for(hours,config):
    if hours<=config.metrics_raw_hours and math.ceil(hours*3600/30)+1<=500:return 30
    if hours<=config.metrics_fine_days*24 and math.ceil(hours*3600/300)+1<=500:return 300
    return 3600


def history(db,instance_id,hours,config,at=None):
    at=at or now();seconds=resolution_for(hours,config)
    cutoff=max(at-timedelta(hours=hours),at-timedelta(days=config.metrics_retention_days))
    # Chart queries touch one tier only, never large legacy payloads.
    values=list(db.scalars(select(MetricBucket).where(MetricBucket.instance_id==instance_id,
        MetricBucket.resolution==seconds,MetricBucket.bucket_start>=bucket_time(cutoff,seconds),MetricBucket.bucket_start<=at)
        .order_by(MetricBucket.bucket_start)))
    if not values:return []
    # Explicit missing bins stop a chart from joining across collection gaps.
    observed={b.bucket_start:b for b in values};filled=[];start=values[0].bucket_start
    while start<=bucket_time(at,seconds):
        filled.append(observed.get(start) or empty_bucket(instance_id,seconds,start))
        start+=timedelta(seconds=seconds)
    values=filled
    # At most 500 points leave the server; merge means with their sample weights.
    factor=max(1,math.ceil(len(values)/500))
    points=[]
    for offset in range(0,len(values),factor):
        parts=values[offset:offset+factor]
        bucket=empty_bucket(instance_id,seconds*len(parts),parts[0].bucket_start)
        for part in parts:merge(bucket,part)
        points.append({
            'time':bucket.bucket_start.isoformat()+'Z','online':bucket.online_samples>0,
            'samples':bucket.samples,'online_samples':bucket.online_samples,
            'availability':bucket.online_samples/bucket.samples if bucket.samples else None,
            'resolution_seconds':seconds*len(parts),
            'request_rate':bucket.rate_sum/bucket.rate_count if bucket.rate_count else None,
            'request_rate_peak':bucket.rate_peak,
            'sessions':bucket.sessions_sum/bucket.sessions_count if bucket.sessions_count else None,
            'sessions_peak':bucket.sessions_peak,
            **{key:bucket.last_data.get(key) for key in ('bytes_in','bytes_out','requests','errors_5xx')},
        })
    return points


def storage(db,config):
    counts=dict(db.execute(select(MetricBucket.resolution,func.count()).group_by(MetricBucket.resolution)).all())
    sizes=None
    if db.get_bind().dialect.name in ('mysql','mariadb'):
        sizes={name:int(size or 0) for name,size in db.execute(text("SELECT TABLE_NAME, DATA_LENGTH+INDEX_LENGTH FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('metrics','metric_latest','metric_buckets')"))}
    return {'interval_seconds':config.metrics_interval,
        'retention':{'raw_hours':config.metrics_raw_hours,'fine_days':config.metrics_fine_days,'total_days':config.metrics_retention_days},
        'buckets':{str(seconds):counts.get(seconds,0) for seconds in RESOLUTIONS},
        'latest_rows':db.scalar(select(func.count()).select_from(MetricLatest)),
        'legacy_rows':db.scalar(select(func.count()).select_from(Metric)),
        'allocated_bytes':sum(sizes.values()) if sizes is not None else None,
        'legacy_table_bytes':sizes.get('metrics',0) if sizes is not None else None,
        'max_buckets_per_instance':sum(math.ceil(retention.total_seconds()/seconds)+1 for seconds,retention in retentions(config).items())}


def optimize_legacy(db):
    if db.scalar(select(func.count()).select_from(Metric)):
        raise ValueError('Die Übernahme alter Metriken läuft noch. Später erneut versuchen.')
    # Explicit admin action only: DDL can rebuild files and briefly wait for locks.
    # OPTIMIZE preserves rows, including any written by an older app concurrently.
    db.commit()
    rows=db.execute(text('OPTIMIZE TABLE metrics WAIT 5')).mappings().all()
    db.commit()
    if not any(row['Msg_type']=='status' and row['Msg_text']=='OK' for row in rows):
        raise RuntimeError('MariaDB konnte die alte Metriktabelle nicht optimieren. Datenbankrechte und Serverlog prüfen.')
