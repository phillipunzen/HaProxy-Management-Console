"""Apply Alembic migrations at startup.

Installations created before migrations existed already contain the baseline
schema (created via ``create_all``). They are stamped with the baseline revision
once, so later revisions can add columns to existing tables.
"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

ROOT = Path(__file__).resolve().parent.parent
BASELINE = '0001'
logger = logging.getLogger('haproxy-control')


def alembic_config():
    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('script_location', str(ROOT / 'migrations'))
    return config


def head_revision():
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(connection):
    return MigrationContext.configure(connection).get_current_revision()


def upgrade(engine):
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        tables = set(inspect(connection).get_table_names())
        if 'alembic_version' not in tables and 'users' in tables:
            logger.info('Existing database without migration history: stamping baseline %s', BASELINE)
            command.stamp(config, BASELINE)
        before = current_revision(connection)
        command.upgrade(config, 'head')
        after = current_revision(connection)
    if before != after:
        logger.info('Database migrated from %s to %s', before or 'empty', after)
    return after
