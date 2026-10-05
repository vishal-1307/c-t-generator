"""phase 10 is_active on faculty, subject, section

Revision ID: 9896c514f228
Revises: ad7f0dea006f
Create Date: 2026-08-28 18:20:00.000000

Additive: same pattern Room.is_active already established. Every existing
row gets server_default '1' (true), so no pre-existing faculty/subject/
section becomes invisible to the scheduler as a side effect of this
migration - this is purely additive capability, not a behavior change until
someone explicitly deactivates a row.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9896c514f228'
down_revision: Union[str, Sequence[str], None] = 'ad7f0dea006f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('faculty', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true())
        )
    with op.batch_alter_table('subject', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true())
        )
    with op.batch_alter_table('section', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('section', schema=None) as batch_op:
        batch_op.drop_column('is_active')
    with op.batch_alter_table('subject', schema=None) as batch_op:
        batch_op.drop_column('is_active')
    with op.batch_alter_table('faculty', schema=None) as batch_op:
        batch_op.drop_column('is_active')
