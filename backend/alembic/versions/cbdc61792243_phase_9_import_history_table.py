"""Phase 9: import_history table

Revision ID: cbdc61792243
Revises: c3a4030b454d
Create Date: 2026-08-28 16:41:06.510472

Written by hand rather than via --autogenerate: the local dev database already
had this table from `Base.metadata.create_all` (main.py runs it at import
time), so autogenerate's live-DB diff saw no change to propose. The columns
below mirror app/models.py::ImportHistory exactly - see that class's
docstring for the audit trail it exists to answer.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cbdc61792243'
down_revision: Union[str, Sequence[str], None] = 'c3a4030b454d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('import_history',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('entity', sa.String(length=40), nullable=False),
    sa.Column('filename', sa.String(length=300), nullable=False),
    sa.Column('mode', sa.String(length=20), nullable=False),
    sa.Column('rows_processed', sa.Integer(), nullable=False),
    sa.Column('rows_created', sa.Integer(), nullable=False),
    sa.Column('rows_updated', sa.Integer(), nullable=False),
    sa.Column('rows_unchanged', sa.Integer(), nullable=False),
    sa.Column('rows_rejected', sa.Integer(), nullable=False),
    sa.Column('rows_deactivated', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('actor', sa.String(length=120), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("status IN ('applied','rejected')", name='ck_import_history_status'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('import_history', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_import_history_entity'), ['entity'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('import_history', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_import_history_entity'))

    op.drop_table('import_history')
