"""Remember the document that was last applied, for the pending-changes list.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19
"""

import sqlalchemy as sa
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('instances', sa.Column('applied_document', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('instances', 'applied_document')
