"""classes per week belongs to a section's subject, not to the subject

The teaching load says how many times a week each section takes each subject,
and two sections can take one subject a different number of times. Stored on
the subject, the second section's number either overwrote the first or was
refused as a contradiction. It now lives on the section-subject row.

Nullable, so every existing mapping keeps the subject-level count it has always
used; a value here overrides that count for this one section.

Revision ID: b8e4d2a6c1f3
Revises: a7c2e9f4b1d8
Create Date: 2026-09-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8e4d2a6c1f3"
down_revision = "a7c2e9f4b1d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("section_subject") as batch:
        batch.add_column(sa.Column("sessions_per_week", sa.Integer(), nullable=True))
        batch.create_check_constraint(
            "ck_section_subject_spw",
            "sessions_per_week IS NULL OR sessions_per_week >= 1",
        )


def downgrade() -> None:
    with op.batch_alter_table("section_subject") as batch:
        batch.drop_constraint("ck_section_subject_spw", type_="check")
        batch.drop_column("sessions_per_week")
