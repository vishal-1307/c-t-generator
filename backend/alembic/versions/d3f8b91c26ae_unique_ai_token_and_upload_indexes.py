"""match the declared uniqueness of the ai token and upload id indexes

Both models declare these columns ``unique=True, index=True``, which is a
unique index. The Phase 12 migration instead created a ``UNIQUE`` table
constraint and, separately, a plain index on the same column. Phase 0 noticed
the difference while adding its own indexes and left it for a revision of its
own; this is that revision.

**Nothing was unenforced.** The table constraint has always prevented a
duplicate token or upload id, which is what matters for a single-use
confirmation: redeeming one still identifies exactly one row, and always did.
What existed was a plain index doing no work the constraint's own index was
not already doing, and a schema that did not match the models - so every
autogenerate run proposed the same change again, which is how a real drift
later on gets waved past.

This makes the named index unique, so the schema alembic builds and the schema
the models declare agree. No data can violate it: the constraint that has been
rejecting duplicates all along is still there, and is left in place because it
is unnamed and dropping it on SQLite would mean rebuilding the table for no
gain.

Revision ID: d3f8b91c26ae
Revises: c7e21a5f4b88
Create Date: 2026-09-01

"""
from typing import Sequence, Union

from alembic import op

revision: str = "d3f8b91c26ae"
down_revision: Union[str, Sequence[str], None] = "c7e21a5f4b88"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEXES = [
    ("ix_ai_confirmation_token", "ai_confirmation", "token"),
    ("ix_ai_upload_upload_id", "ai_upload", "upload_id"),
]


def upgrade() -> None:
    for name, table, column in _INDEXES:
        op.drop_index(name, table_name=table)
        op.create_index(name, table, [column], unique=True)


def downgrade() -> None:
    for name, table, column in _INDEXES:
        op.drop_index(name, table_name=table)
        op.create_index(name, table, [column], unique=False)
