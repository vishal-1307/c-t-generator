"""phase 0: indexes for grid joins, lock history, and room eligibility

Three indexes for queries the application already runs on every request or
every solve, and which were doing sequential scans:

* ``assignment.timeslot_id`` - the only foreign key on that table without an
  index, yet every grid view joins on it and the grid-replacement impact count
  filters on it.
* ``assignment(run_id, section_id, subject_id)`` - the exact lookup the solver
  performs per locked pair when reproducing a lock from the previous run.
* ``room(is_active, room_type, lab_type)`` - the room-eligibility filter, run
  for every pair of every solve.

Index-only, so this is safe to apply and to reverse. Autogenerate additionally
proposed making ``ix_ai_confirmation_token`` and ``ix_ai_upload_upload_id``
unique - real drift between the models and the original migration, but
unrelated to this change and potentially failing on existing rows, so it is
deliberately left for its own revision.

Revision ID: 7cc1628eabd2
Revises: b4c1e7a92d30
Create Date: 2026-08-30 23:55:36.741739

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7cc1628eabd2'
down_revision: Union[str, Sequence[str], None] = 'b4c1e7a92d30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_assignment_timeslot_id", "assignment", ["timeslot_id"], unique=False
    )
    op.create_index(
        "ix_assignment_run_section_subject",
        "assignment",
        ["run_id", "section_id", "subject_id"],
        unique=False,
    )
    op.create_index(
        "ix_room_active_type",
        "room",
        ["is_active", "room_type", "lab_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_room_active_type", table_name="room")
    op.drop_index("ix_assignment_run_section_subject", table_name="assignment")
    op.drop_index("ix_assignment_timeslot_id", table_name="assignment")
