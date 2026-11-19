"""Startup migrations: fresh databases, legacy create_all databases and model drift."""

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from backend import migrations
from backend.db import Base


def engine(tmp_path, name):
    return create_engine('sqlite:///' + str(tmp_path / name))


def test_fresh_database_is_created_by_migrations_and_matches_models(tmp_path):
    db = engine(tmp_path, 'fresh.db')
    assert migrations.upgrade(db) == migrations.head_revision()
    with db.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == [], 'backend/db.py changed without a migration: alembic revision --autogenerate'
    assert migrations.upgrade(db) == migrations.head_revision()  # idempotent


def test_legacy_database_without_history_is_stamped_not_recreated(tmp_path):
    db = engine(tmp_path, 'legacy.db')
    Base.metadata.create_all(db)
    with db.begin() as connection:
        # Columns added after the baseline did not exist in create_all installations.
        connection.exec_driver_sql('ALTER TABLE instances DROP COLUMN applied_document')
        connection.exec_driver_sql(
            "INSERT INTO users (username, password_hash, role, must_change_password, created_at) VALUES ('keep', 'x', 'admin', 0, '2026-01-01')"
        )
    assert migrations.upgrade(db) == migrations.head_revision()
    with db.connect() as connection:
        assert 'alembic_version' in inspect(connection).get_table_names()
        assert connection.exec_driver_sql('SELECT username FROM users').scalar() == 'keep'
