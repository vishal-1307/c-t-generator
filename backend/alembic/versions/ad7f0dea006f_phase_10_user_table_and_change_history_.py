"""phase 10 user table and change_history user fields

Revision ID: ad7f0dea006f
Revises: cbdc61792243
Create Date: 2026-08-28 18:03:38.273166

Written by hand (same reason as the Phase 9 import_history migration): the
local dev database already has these tables/columns from
``Base.metadata.create_all`` running at app import time, so autogenerate's
live-DB diff sees no change to propose. Mirrors app/models.py::User and the
additive ChangeHistory.user_id/user_role columns exactly.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ad7f0dea006f'
down_revision: Union[str, Sequence[str], None] = 'cbdc61792243'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('user',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=60), nullable=False),
    sa.Column('email', sa.String(length=200), nullable=True),
    sa.Column('hashed_password', sa.String(length=200), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('faculty_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_login_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint("role IN ('admin','scheduler','faculty','viewer')", name='ck_user_role'),
    sa.ForeignKeyConstraint(['faculty_id'], ['faculty.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username'),
    )

    # Additive: existing rows get NULL for both new columns, which is exactly
    # the "no authenticated identity recorded for this pre-auth change" state
    # they're meant to represent.
    with op.batch_alter_table('change_history', schema=None) as batch_op:
        batch_op.add_column(sa.Column('user_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('user_role', sa.String(length=20), nullable=True))
        batch_op.create_foreign_key(
            'fk_change_history_user', 'user', ['user_id'], ['id'], ondelete='SET NULL'
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('change_history', schema=None) as batch_op:
        batch_op.drop_constraint('fk_change_history_user', type_='foreignkey')
        batch_op.drop_column('user_role')
        batch_op.drop_column('user_id')

    op.drop_table('user')
