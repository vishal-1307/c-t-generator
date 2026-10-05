"""phase 3: room capabilities, subject components, section groups

The schema a real institution's data needs, and which the current one cannot
express:

* **Rooms gain capabilities.** ``byod`` and ``charging`` decide whether a
  software practical can run in an ordinary classroom instead of consuming a
  scarce specialist lab. ``charging_sockets`` is recorded but deliberately not
  constrained - see the model comment.

* **Room numbers become unique per block, not globally.** Two buildings can
  each have a "101". The global constraint forced the second building's rooms
  to be renamed before they could be imported at all. ``room_code`` (e.g.
  "36-101") takes over as the institution-wide unique label and is backfilled
  from block and number.

* **Subjects can be 'mixed'.** A subject with both a lecture and a practical
  component - two lectures and four practical periods a week, in different
  kinds of room - previously had to be split into two subjects with two
  invented codes, so the codes in a timetable stopped matching the codes in the
  syllabus. The per-component load columns are NULL for pure subjects, so there
  is never a second, disagreeing copy of an existing subject's load.

* **Section groups (G1/G2) are recorded.** Not yet scheduled: the solver still
  treats a section as indivisible. Storing them first means the data can be
  imported and shown honestly before the solver understands it.

REVERSIBILITY. The room uniqueness change is the one direction that is not
freely reversible: once two blocks legitimately share a room number, restoring
a global unique on ``room_number`` will fail. Take a dump before applying (see
OPERATIONS.md). Everything else here is additive and drops cleanly.

Revision ID: a1d4f2b7c903
Revises: 7cc1628eabd2
Create Date: 2026-08-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1d4f2b7c903"
down_revision: Union[str, Sequence[str], None] = "7cc1628eabd2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# SQLite cannot ALTER a constraint, so alembic rebuilds the table. An unnamed
# UNIQUE needs a predictable name to be dropped during that rebuild; PostgreSQL
# named the same constraint itself when the table was created. Hence the two
# spellings below rather than one.
SQLITE_NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
PG_ROOM_NUMBER_UNIQUE = "room_room_number_key"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # ---------------------------------------------------------------- rooms
    with op.batch_alter_table("room") as batch:
        batch.add_column(sa.Column("room_code", sa.String(length=40), nullable=True))
        batch.add_column(
            sa.Column(
                "byod", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch.add_column(
            sa.Column(
                "charging", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch.add_column(sa.Column("charging_sockets", sa.Integer(), nullable=True))

    # Backfill the new label before making it unique, so existing rooms keep
    # working and the constraint has real values to check.
    op.execute(
        """
        UPDATE room
           SET room_code = CASE
                 WHEN block IS NULL OR block = '' THEN room_number
                 ELSE block || '-' || room_number
               END
         WHERE room_code IS NULL
        """
    )

    if _is_postgres():
        op.execute(
            f'ALTER TABLE room DROP CONSTRAINT IF EXISTS "{PG_ROOM_NUMBER_UNIQUE}"'
        )
        op.create_unique_constraint(
            "uq_room_block_number", "room", ["block", "room_number"]
        )
        op.create_unique_constraint("uq_room_code", "room", ["room_code"])
        op.create_check_constraint(
            "ck_room_sockets", "room", "charging_sockets IS NULL OR charging_sockets >= 0"
        )
    else:
        with op.batch_alter_table("room", naming_convention=SQLITE_NAMING) as batch:
            batch.drop_constraint("uq_room_room_number", type_="unique")
            batch.create_unique_constraint(
                "uq_room_block_number", ["block", "room_number"]
            )
            batch.create_unique_constraint("uq_room_code", ["room_code"])
            batch.create_check_constraint(
                "ck_room_sockets", "charging_sockets IS NULL OR charging_sockets >= 0"
            )

    # ------------------------------------------------------------- subjects
    with op.batch_alter_table("subject") as batch:
        batch.add_column(
            sa.Column("lecture_sessions_per_week", sa.Integer(), nullable=True)
        )
        batch.add_column(
            sa.Column("lecture_session_length", sa.Integer(), nullable=True)
        )
        batch.add_column(
            sa.Column("practical_sessions_per_week", sa.Integer(), nullable=True)
        )
        batch.add_column(
            sa.Column("practical_session_length", sa.Integer(), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "byod_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "charging_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    # Widen the type vocabulary and require a mixed subject to declare both of
    # its components. Autogenerate cannot see a changed CHECK expression, so
    # this is written out rather than detected.
    with op.batch_alter_table("subject") as batch:
        batch.drop_constraint("ck_subject_type", type_="check")
        batch.create_check_constraint(
            "ck_subject_type", "type IN ('theory','practical','mixed')"
        )
        batch.create_check_constraint(
            "ck_subject_mixed_has_both_loads",
            "type <> 'mixed' OR ("
            " lecture_sessions_per_week IS NOT NULL"
            " AND lecture_session_length IS NOT NULL"
            " AND practical_sessions_per_week IS NOT NULL"
            " AND practical_session_length IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_subject_lecture_len",
            "lecture_session_length IS NULL OR lecture_session_length BETWEEN 1 AND 3",
        )
        batch.create_check_constraint(
            "ck_subject_practical_len",
            "practical_session_length IS NULL"
            " OR practical_session_length BETWEEN 1 AND 3",
        )

    # ------------------------------------------------------------- sections
    with op.batch_alter_table("section") as batch:
        batch.add_column(sa.Column("batch", sa.String(length=40), nullable=True))

    op.create_table(
        "section_group",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=False),
        sa.Column("group_code", sa.String(length=20), nullable=False),
        sa.Column("strength", sa.Integer(), nullable=False),
        sa.CheckConstraint("strength > 0", name="ck_section_group_strength"),
        sa.ForeignKeyConstraint(["section_id"], ["section.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("section_id", "group_code", name="uq_section_group_code"),
    )
    op.create_index(
        "ix_section_group_section_id", "section_group", ["section_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_section_group_section_id", table_name="section_group")
    op.drop_table("section_group")

    with op.batch_alter_table("section") as batch:
        batch.drop_column("batch")

    with op.batch_alter_table("subject") as batch:
        batch.drop_constraint("ck_subject_practical_len", type_="check")
        batch.drop_constraint("ck_subject_lecture_len", type_="check")
        batch.drop_constraint("ck_subject_mixed_has_both_loads", type_="check")
        batch.drop_constraint("ck_subject_type", type_="check")
        batch.create_check_constraint(
            "ck_subject_type", "type IN ('theory','practical')"
        )
        batch.drop_column("charging_required")
        batch.drop_column("byod_required")
        batch.drop_column("practical_session_length")
        batch.drop_column("practical_sessions_per_week")
        batch.drop_column("lecture_session_length")
        batch.drop_column("lecture_sessions_per_week")

    # Restoring a global unique on room_number fails if two blocks have come to
    # share a number. That is a real possibility once a multi-building workbook
    # has been imported, and is why a dump is taken before this migration.
    if _is_postgres():
        op.execute("ALTER TABLE room DROP CONSTRAINT IF EXISTS ck_room_sockets")
        op.execute("ALTER TABLE room DROP CONSTRAINT IF EXISTS uq_room_code")
        op.execute("ALTER TABLE room DROP CONSTRAINT IF EXISTS uq_room_block_number")
        op.create_unique_constraint(PG_ROOM_NUMBER_UNIQUE, "room", ["room_number"])
    else:
        with op.batch_alter_table("room", naming_convention=SQLITE_NAMING) as batch:
            batch.drop_constraint("ck_room_sockets", type_="check")
            batch.drop_constraint("uq_room_code", type_="unique")
            batch.drop_constraint("uq_room_block_number", type_="unique")
            batch.create_unique_constraint("uq_room_room_number", ["room_number"])

    with op.batch_alter_table("room") as batch:
        batch.drop_column("charging_sockets")
        batch.drop_column("charging")
        batch.drop_column("byod")
        batch.drop_column("room_code")
