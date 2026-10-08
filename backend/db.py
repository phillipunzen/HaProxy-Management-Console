from datetime import datetime, timezone
from sqlalchemy import create_engine, String, Text, Integer, DateTime, Boolean, JSON, ForeignKey, Index
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
    __tablename__ = 'metrics'
    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey('instances.id', ondelete='CASCADE'))
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    data: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (Index('ix_metrics_instance_time', 'instance_id', 'collected_at'), Index('ix_metrics_time', 'collected_at'))

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
