"""drop the AI assistant's tables

The assistant has been removed from the product: this is a timetable generator
driven by two spreadsheets, and nothing in it asks a model anything. Its three
tables had no reader left - `ai_audit_log`, `ai_confirmation` and `ai_upload`
were written only by `app/ai/`, which is gone, and no other table refers to
them.

What is lost on upgrade is the audit trail of assistant tool calls, pending
confirmation tokens, and staged uploads. The assistant was disabled on the
deployment (`AI_PROVIDER` unset), so on that database these tables are empty;
anywhere it was switched on, export `ai_audit_log` first if the record matters.

Downgrade recreates the three tables exactly as the two migrations that made
them left them - including the unique indexes from d3f8b91c26ae - but empty.

Revision ID: f1a9c3e6d7b2
Revises: e5a71c4b09d2
Create Date: 2026-09-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f1a9c3e6d7b2"
down_revision = "e5a71c4b09d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Dependants first is irrelevant here - none of the three refers to another
    # - but dropping the indexes explicitly keeps SQLite and Postgres identical.
    for table in ("ai_upload", "ai_confirmation", "ai_audit_log"):
        op.drop_table(table)


def downgrade() -> None:
    # As created by b4c1e7a92d30 ...
    op.create_table(
        "ai_audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        # SET NULL rather than CASCADE: deleting a user must not erase the
        # record of what they did.
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(length=120), nullable=True),
        sa.Column("user_role", sa.String(length=20), nullable=True),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("tool_name", sa.String(length=60), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=60), nullable=True),
        sa.Column("entity_type", sa.String(length=40), nullable=True),
        sa.Column("entity_id", sa.String(length=120), nullable=True),
        sa.Column("arguments_json", sa.Text(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_audit_log_user_id", "ai_audit_log", ["user_id"])
    op.create_index("ix_ai_audit_log_session_id", "ai_audit_log", ["session_id"])
    op.create_index("ix_ai_audit_log_tool_name", "ai_audit_log", ["tool_name"])
    op.create_index("ix_ai_audit_log_category", "ai_audit_log", ["category"])
    op.create_index("ix_ai_audit_log_status", "ai_audit_log", ["status"])
    op.create_index("ix_ai_audit_log_reason", "ai_audit_log", ["reason"])
    op.create_index("ix_ai_audit_log_entity_type", "ai_audit_log", ["entity_type"])
    op.create_index("ix_ai_audit_log_created_at", "ai_audit_log", ["created_at"])
    op.create_index("ix_ai_audit_user_created", "ai_audit_log", ["user_id", "created_at"])

    op.create_table(
        "ai_confirmation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("authorizes", sa.String(length=60), nullable=False),
        sa.Column("argument_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # Unique: redemption is one indexed lookup, and a duplicate token can
        # never exist.
        sa.UniqueConstraint("token"),
    )
    op.create_index("ix_ai_confirmation_token", "ai_confirmation", ["token"])
    op.create_index("ix_ai_confirmation_user_id", "ai_confirmation", ["user_id"])
    op.create_index("ix_ai_confirmation_expires_at", "ai_confirmation", ["expires_at"])

    op.create_table(
        "ai_upload",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("upload_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=300), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("upload_id"),
    )
    op.create_index("ix_ai_upload_upload_id", "ai_upload", ["upload_id"])
    op.create_index("ix_ai_upload_user_id", "ai_upload", ["user_id"])
    op.create_index("ix_ai_upload_expires_at", "ai_upload", ["expires_at"])

    # ... and as tightened by d3f8b91c26ae.
    for name, table, column in (
        ("ix_ai_confirmation_token", "ai_confirmation", "token"),
        ("ix_ai_upload_upload_id", "ai_upload", "upload_id"),
    ):
        op.drop_index(name, table_name=table)
        op.create_index(name, table, [column], unique=True)
