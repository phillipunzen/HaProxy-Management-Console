from datetime import datetime, timezone
from sqlalchemy import create_engine, String, Text, Integer, SmallInteger, Double, DateTime, Boolean, JSON, ForeignKey, Index
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from backend.settings import settings

def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

engine = create_engine(settings.database_url, pool_pre_ping=True, pool_recycle=1800, hide_parameters=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default='admin')
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class LoginSession(Base):
    __tablename__ = 'sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    expires_at: Mapped[datetime] = mapped_column(DateTime)

class BasicAuthUser(Base):
    __tablename__ = 'basic_auth_users'
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80),unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean,default=True)
    version: Mapped[int] = mapped_column(Integer,default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime,default=now)

class BasicAuthDirectory(Base):
    __tablename__ = 'basic_auth_directory'
    id: Mapped[int] = mapped_column(primary_key=True,default=1)
    version: Mapped[int] = mapped_column(Integer,default=0)

class BasicAuthGroup(Base):
    __tablename__ = 'basic_auth_groups'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120),unique=True)
    realm: Mapped[str] = mapped_column(String(80),default='Restricted')
    description: Mapped[str] = mapped_column(String(500),default='')
    version: Mapped[int] = mapped_column(Integer,default=0)

class BasicAuthMembership(Base):
    __tablename__ = 'basic_auth_memberships'
    user_id: Mapped[int] = mapped_column(ForeignKey('basic_auth_users.id',ondelete='CASCADE'),primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey('basic_auth_groups.id',ondelete='CASCADE'),primary_key=True)

class BasicAuthDeployment(Base):
    __tablename__ = 'basic_auth_deployments'
    instance_id: Mapped[int] = mapped_column(ForeignKey('instances.id',ondelete='CASCADE'),primary_key=True)
    metadata_json: Mapped[dict] = mapped_column(JSON,default=dict)
    applied_at: Mapped[datetime] = mapped_column(DateTime,default=now)

class Instance(Base):
    __tablename__ = 'instances'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    agent_url: Mapped[str] = mapped_column(String(500))
    profile: Mapped[str] = mapped_column(String(80))
    token_cipher: Mapped[str] = mapped_column(Text)
    allow_http: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String(20), default='unknown')
    notes: Mapped[str] = mapped_column(Text, default='')
    document: Mapped[dict] = mapped_column(JSON, default=dict)
    document_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class Infrastructure(Base):
    __tablename__ = 'infrastructures'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    name_key: Mapped[str] = mapped_column(String(360),unique=True)
    description: Mapped[str] = mapped_column(String(500),default='')
    version: Mapped[int] = mapped_column(Integer,default=0)

class InstanceInfrastructure(Base):
    __tablename__ = 'instance_infrastructures'
    instance_id: Mapped[int] = mapped_column(ForeignKey('instances.id',ondelete='CASCADE'),primary_key=True)
    infrastructure_id: Mapped[int] = mapped_column(ForeignKey('infrastructures.id',ondelete='RESTRICT'),index=True)

class InstanceMetadata(Base):
    __tablename__ = 'instance_metadata'
    instance_id: Mapped[int] = mapped_column(ForeignKey('instances.id',ondelete='CASCADE'),primary_key=True)
    tags: Mapped[list] = mapped_column(JSON,default=list)
    location: Mapped[str] = mapped_column(String(120),default='')
    version: Mapped[int] = mapped_column(Integer,default=0)

class Revision(Base):
    __tablename__ = 'revisions'
    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey('instances.id', ondelete='CASCADE'))
    config: Mapped[str] = mapped_column(Text().with_variant(MEDIUMTEXT(), 'mysql'))
    base_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default='draft')
    message: Mapped[str] = mapped_column(String(500), default='')
    author: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime)

class Metric(Base):
    # Legacy snapshots are migrated in bounded transactions by backend.metrics.
    __tablename__ = 'metrics'
    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey('instances.id', ondelete='CASCADE'))
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    data: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (Index('ix_metrics_instance_time', 'instance_id', 'collected_at'), Index('ix_metrics_time', 'collected_at'))

class MetricLatest(Base):
    __tablename__ = 'metric_latest'
    instance_id: Mapped[int] = mapped_column(ForeignKey('instances.id',ondelete='CASCADE'),primary_key=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime)
    data: Mapped[dict] = mapped_column(JSON)

class MetricBucket(Base):
    __tablename__ = 'metric_buckets'
    instance_id: Mapped[int] = mapped_column(ForeignKey('instances.id',ondelete='CASCADE'),primary_key=True)
    resolution: Mapped[int] = mapped_column(SmallInteger,primary_key=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime,primary_key=True)
    samples: Mapped[int] = mapped_column(Integer,default=0)
    online_samples: Mapped[int] = mapped_column(Integer,default=0)
    rate_sum: Mapped[float] = mapped_column(Double,default=0)
    rate_count: Mapped[int] = mapped_column(Integer,default=0)
    rate_peak: Mapped[float | None] = mapped_column(Double)
    sessions_sum: Mapped[float] = mapped_column(Double,default=0)
    sessions_count: Mapped[int] = mapped_column(Integer,default=0)
    sessions_peak: Mapped[float | None] = mapped_column(Double)
    last_at: Mapped[datetime] = mapped_column(DateTime)
    last_data: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (Index('ix_metric_bucket_expiry','resolution','bucket_start'),)

class Audit(Base):
    __tablename__ = 'audit'
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(150), default='')
    detail: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

def get_db():
    with SessionLocal() as db:
        yield db
