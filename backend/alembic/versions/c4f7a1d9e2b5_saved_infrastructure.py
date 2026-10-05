"""the room list is saved once and reused

Infra.xlsx describes rooms, which change rarely; Load.xlsx describes a
semester's teaching, which changes every time. They were uploaded together
every time, so a new teaching load meant uploading the unchanged rooms again.
The room list is now its own saved record, which every new teaching load is
scheduled against, and each dataset records which one it was built on.

Additive: two new tables and a nullable column. Existing datasets have no
infrastructure recorded and keep the room lists they already have.

Revision ID: c4f7a1d9e2b5
Revises: b8e4d2a6c1f3
Create Date: 2026-09-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4f7a1d9e2b5"
down_revision = "b8e4d2a6c1f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "infrastructure",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.Column("uploaded_by", sa.String(length=80), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "infrastructure_room",
        sa.Column("infrastructure_id", sa.Integer(), nullable=False),
        sa.Column("room_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["infrastructure_id"], ["infrastructure.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["room_id"], ["room.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("infrastructure_id", "room_id"),
    )
    with op.batch_alter_table("academic_context") as batch:
        batch.add_column(sa.Column("infrastructure_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_academic_context_infrastructure",
            "infrastructure",
            ["infrastructure_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("academic_context") as batch:
        batch.drop_constraint("fk_academic_context_infrastructure", type_="foreignkey")
        batch.drop_column("infrastructure_id")
    op.drop_table("infrastructure_room")
    op.drop_table("infrastructure")
