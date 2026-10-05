"""phase 6: session types on classes and decisions, plus run warnings

A subject can have both a lecture and a practical component, scheduled
independently and usually in different kinds of room. A scheduled class
therefore has to say which component it is, and so does the persisted
faculty/room decision behind it - otherwise a subject's lecture and its
practical would be forced to share one room.

BACKFILL AND LOCKS. Every existing row is mapped one-to-one:

* a class of a `practical` subject becomes 'P'; everything else becomes 'L'
* the persisted decision behind it is mapped the same way

That mapping is total, and it is total *because* Phase 3 blocked generation for
any subject with two components. No timetable in existence was produced from a
mixed subject, so no existing row is ambiguous and **no lock can be lost here**.
A lock that cannot be honoured later is reported rather than dropped in silence
- see `solver/data.py::_reproduce_lock`.

REVERSIBILITY. Downgrading is safe only while at most one session type exists
per (context, section, subject) - that is, before the first mixed-subject
timetable is generated. After that, restoring the narrower uniqueness would
have to discard one of the two decisions, so it fails instead. Take a dump
before applying (see OPERATIONS.md).

Revision ID: c7e21a5f4b88
Revises: a1d4f2b7c903
Create Date: 2026-08-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7e21a5f4b88"
down_revision: Union[str, Sequence[str], None] = "a1d4f2b7c903"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# A practical subject's classes are practicals; everything else is a lecture.
# Written as one UPDATE per table rather than a join so it reads the same on
# both dialects.
_BACKFILL = """
    UPDATE {table}
       SET session_type = 'P'
     WHERE subject_id IN (SELECT id FROM subject WHERE type = 'practical')
"""


def upgrade() -> None:
    with op.batch_alter_table("assignment") as batch:
        batch.add_column(
            sa.Column(
                "session_type",
                sa.String(length=1),
                nullable=False,
                server_default="L",
            )
        )
    op.execute(_BACKFILL.format(table="assignment"))

    with op.batch_alter_table("section_subject_assignment") as batch:
        batch.add_column(
            sa.Column(
                "session_type",
                sa.String(length=1),
                nullable=False,
                server_default="L",
            )
        )
    op.execute(_BACKFILL.format(table="section_subject_assignment"))

    # Widen the uniqueness so a subject's two components are two decisions.
    with op.batch_alter_table("section_subject_assignment") as batch:
        batch.drop_constraint("uq_section_subject_assignment", type_="unique")
        batch.create_unique_constraint(
            "uq_section_subject_assignment",
            ["academic_context_id", "section_id", "subject_id", "session_type"],
        )

    with op.batch_alter_table("timetable_run") as batch:
        batch.add_column(sa.Column("warnings_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("timetable_run") as batch:
        batch.drop_column("warnings_json")

    # Only reversible while no pair has two components decided; otherwise the
    # narrower constraint cannot hold and failing is better than choosing which
    # half of a subject to discard.
    with op.batch_alter_table("section_subject_assignment") as batch:
        batch.drop_constraint("uq_section_subject_assignment", type_="unique")
        batch.create_unique_constraint(
            "uq_section_subject_assignment",
            ["academic_context_id", "section_id", "subject_id"],
        )
        batch.drop_column("session_type")

    with op.batch_alter_table("assignment") as batch:
        batch.drop_column("session_type")
