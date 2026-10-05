"""phase 12: ai audit log, shared confirmations, shared uploads

Three tables that move AI state out of process memory:

* ``ai_audit_log``    - durable record of every tool call (was log-only)
* ``ai_confirmation`` - pending previews (were an in-process dict)
* ``ai_upload``       - staged import files (were an in-process dict)

Purely additive: no existing table is touched, so this is safe to apply to a
live database and safe to leave un-downgraded.

Revision ID: b4c1e7a92d30
Revises: 9b58a55146a3
Create Date: 2026-08-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b4c1e7a92d30"
down_revision = "9b58a55146a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_table("ai_upload")
    op.drop_table("ai_confirmation")
    op.drop_table("ai_audit_log")
