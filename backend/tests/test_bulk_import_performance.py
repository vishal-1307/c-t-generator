"""Phase 9 PART 21: bulk room import must scale via bulk lookups, not one
query per row. Asserts a query-count ceiling that does not grow linearly with
row count, and a wall-clock sanity bound - not a formal benchmark, but enough
to catch an accidental N+1 regression."""
from __future__ import annotations

import io
import time

import pytest
from sqlalchemy import event

from app.models import Room


def rooms_csv(n: int) -> str:
    lines = ["block,floor,room_number,capacity,room_type,lab_type,is_active"]
    for i in range(n):
        block = chr(ord("A") + (i // 100) % 26)
        lines.append(f"{block},{i % 10},{block}-{i:04d},60,theory,,true")
    return "\n".join(lines) + "\n"


def _apply(client, csv_text: str):
    return client.post(
        "/api/bulk-import/rooms/apply",
        files={"file": ("bulk.csv", io.BytesIO(csv_text.encode()), "text/csv")},
        data={"mode": "add_update"},
    )


@pytest.mark.parametrize("n", [100, 500, 1000])
def test_large_room_import_applies_correctly(client, db_session, n):
    start = time.perf_counter()
    resp = _apply(client, rooms_csv(n))
    elapsed = time.perf_counter() - start

    assert resp.status_code == 200
    body = resp.json()
    assert body["committed"] is True
    assert body["counts"]["create"] == n
    assert db_session.query(Room).count() == n
    # Generous ceiling - this is a regression guard against an accidental
    # N+1 rewrite, not a tight performance SLA.
    assert elapsed < 15.0, f"{n}-row import took {elapsed:.2f}s"


def test_query_count_does_not_scale_linearly_with_row_count(client, db_session):
    """The defining N+1 symptom: query count roughly triples the row count
    (one lookup + one write + one flush per row) instead of staying flat.
    Bulk import uses exactly one upfront SELECT to build the existing-rooms
    map (see bulk_import.RoomAdapter.load_existing), so query count should be
    dominated by ORM inserts/updates, not per-row lookups."""
    counts: dict[int, int] = {}
    for n in (50, 500):
        engine = db_session.get_bind()
        queries = {"n": 0}

        def _count(*args, **kwargs):
            queries["n"] += 1

        event.listen(engine, "before_cursor_execute", _count)
        try:
            resp = _apply(client, rooms_csv(n))
            assert resp.status_code == 200
            assert resp.json()["counts"]["create"] == n
        finally:
            event.remove(engine, "before_cursor_execute", _count)
        counts[n] = queries["n"]

        # Reset for the next iteration - full sync + confirm clears the slate
        # by deactivating everything, then a fresh apply repopulates it. To
        # keep query-count measurement clean per iteration, just delete rows
        # directly between runs (this is a query-count probe, not a
        # behavioural test of any particular import mode).
        db_session.query(Room).delete()
        db_session.commit()

    # 10x more rows should not come anywhere near 10x more queries - a
    # per-row round trip would push this close to that ratio.
    ratio = counts[500] / counts[50]
    assert ratio < 5, f"query count scaled {ratio:.1f}x for a 10x row increase: {counts}"


# ===========================================================================
# Reference resolution must not scale with row count.
#
# Four adapters resolved each row's references with their own point queries, so
# a 5,000-row section-subject file issued about 15,000 of them - and twice, as
# `apply` re-runs `analyse`. The existing test above covers rooms, which is the
# one adapter that never queried during validation, so the regression it was
# written to catch could not actually reach it.
# ===========================================================================


def _count_queries(db_session, fn):
    from sqlalchemy import event

    statements: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return statements


def _seed_referables(client, n_subjects: int):
    ctx = client.post("/api/academic-contexts", json={
        "academic_year": "2026-27", "semester": 5,
        "program": "BCA", "department": "CSE",
    }).json()
    client.post("/api/sections", json={
        "academic_context_id": ctx["id"], "section_number": "D2401", "strength": 60,
    })
    for i in range(n_subjects):
        client.post("/api/subjects", json={
            "name": f"Subject {i}", "code": f"SUB{i:03d}", "type": "theory",
            "session_length_hours": 1, "sessions_per_week": 1,
        })
    return ctx


def _section_subject_csv(n: int) -> bytes:
    header = "academic_year,semester,program,department,section_number,subject_code\n"
    rows = "".join(
        f"2026-27,5,BCA,CSE,D2401,SUB{i:03d}\n" for i in range(n)
    )
    return (header + rows).encode()


def test_reference_resolution_does_not_query_per_row(client, db_session):
    """The query count for ten rows and for eighty must be the same shape.

    This is the property that makes a real import viable; a per-row lookup is
    invisible at demo size and quadratic-feeling at institution size.
    """
    _seed_referables(client, 80)
    from app import bulk_import as bi

    def analyse(n):
        bi.analyse(db_session, "section_subjects", _section_subject_csv(n), "s.csv")

    small = _count_queries(db_session, lambda: analyse(10))
    large = _count_queries(db_session, lambda: analyse(80))

    assert len(small) == len(large), (
        f"query count grew with row count: {len(small)} -> {len(large)}"
    )


def test_a_row_can_reference_something_an_earlier_row_created(client, db_session):
    """Within one file, later rows may name an entity an earlier row creates.

    The resolver caches the database, so a newly created row has to be written
    back into that cache or the second reference is wrongly reported unknown -
    correct about the database as it was, wrong about the import as a whole.
    """
    from app.bulk_import.resolver import DbResolver
    from app.models import Faculty

    refs = DbResolver(db_session)
    assert refs.faculty_by_code("T-NEW") is None

    created = Faculty(
        name="Dr New", faculty_code="T-NEW", department="CSE", is_active=True
    )
    refs.remember(created)
    assert refs.faculty_by_code("T-NEW") is created
    assert refs.faculty_by_code("t-new ") is created, "lookup must normalise"
