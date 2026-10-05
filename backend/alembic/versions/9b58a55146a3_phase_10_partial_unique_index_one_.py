"""phase 10 partial unique index: one published run per context

Revision ID: 9b58a55146a3
Revises: 9896c514f228
Create Date: 2026-08-28 19:30:00.000000

Enforces "at most one PUBLISHED TimetableRun per academic_context_id" at the
database itself (see models.py's TimetableRun docstring and
routers/generate.py::publish_run for the concurrency reasoning this closes -
Phase 10 PART 18). A partial/filtered unique index, supported by both
SQLite and PostgreSQL. Safe to apply against real data: two published runs
in the same context was already an invariant violation, never a state the
application itself produces - this only makes that invariant enforced, not
newly true.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b58a55146a3'
down_revision: Union[str, Sequence[str], None] = '9896c514f228'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        'uq_one_published_run_per_context',
        'timetable_run',
        ['academic_context_id'],
        unique=True,
        sqlite_where=sa.text("publish_status = 'PUBLISHED'"),
        postgresql_where=sa.text("publish_status = 'PUBLISHED'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_one_published_run_per_context', table_name='timetable_run')
