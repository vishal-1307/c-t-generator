"""section parent, so a lab group can be a section

A department that splits section 2401 into 24011 and 24012 gives each group its
own strength, lab subject and teacher. Recording the group as a section makes
room capacity, demand counting and locking correct for it without adding any
solver variables - all that is missing is the one fact that a group's students
are also the parent's students, which is what this column carries.

Nullable, defaulting to NULL, which means "a section in its own right". Every
existing row gets that, so behaviour before and after this migration is
identical until something writes a parent.

Revision ID: e5a71c4b09d2
Revises: d3f8b91c26ae
Create Date: 2026-09-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5a71c4b09d2"
down_revision = "d3f8b91c26ae"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # batch_alter_table for SQLite, which cannot add a column with a foreign
    # key in place. The other migrations in this directory use the same shape.
    with op.batch_alter_table("section", schema=None) as batch_op:
        batch_op.add_column(sa.Column("parent_section_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_section_parent_section",
            "section",
            ["parent_section_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_section_parent", ["parent_section_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("section", schema=None) as batch_op:
        batch_op.drop_index("ix_section_parent")
        batch_op.drop_constraint("fk_section_parent_section", type_="foreignkey")
        batch_op.drop_column("parent_section_id")
