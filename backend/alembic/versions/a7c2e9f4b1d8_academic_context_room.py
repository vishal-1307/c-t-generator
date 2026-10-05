"""the rooms one intake's timetable may use

Rooms are institution-wide, and every intake could use every room on record.
On the deployment that put 40 of the department's 52 periods into rooms from
an earlier dataset that are not in its room list at all. The two-file import
now records which rooms its `Infra.xlsx` named, and that intake's timetable
uses only those.

Additive: a new table and nothing else. An intake with no rows here keeps
using every room, so every existing timetable behaves exactly as before.

Revision ID: a7c2e9f4b1d8
Revises: f1a9c3e6d7b2
Create Date: 2026-09-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7c2e9f4b1d8"
down_revision = "f1a9c3e6d7b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "academic_context_room",
        sa.Column("academic_context_id", sa.Integer(), nullable=False),
        sa.Column("room_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["academic_context_id"], ["academic_context.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["room_id"], ["room.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("academic_context_id", "room_id"),
    )


def downgrade() -> None:
    op.drop_table("academic_context_room")
