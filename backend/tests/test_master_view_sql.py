"""The master view is answered in SQL, and must answer identically.

It used to load every assignment of a run, apply ten of its filters in a Python
loop, sort in Python, and slice - so page forty cost what page one did, and an
export re-ran the whole thing once per two thousand rows.

Rewriting that in SQL is only safe if the answers do not move. These tests
re-derive each expected result from the run's rows independently of the query
under test, then compare. They also pin the cost, because a rewrite that is
correct but still loads everything has not fixed the problem it was for.
"""
from __future__ import annotations

from datetime import time

import pytest
from sqlalchemy import event

from app import timetable_views as views
from app.models import (
    Assignment,
    Faculty,
    Room,
    Section,
    SectionSubjectAssignment,
    Subject,
    TimeSlot,
    TimetableRun,
)


# --------------------------------------------------------------- a fixture run


@pytest.fixture
def run_with_rows(db_session, context):
    """A run spanning two days, two sections, two rooms in different blocks.

    Deliberately varied so every filter has something to include and exclude.
    """
    slots = [
        TimeSlot(
            day=day, day_index=di, period_index=pi,
            start_time=time(9 + pi, 0), end_time=time(10 + pi, 0), is_lunch=False,
        )
        for di, day in enumerate(["Monday", "Tuesday"])
        for pi in range(3)
    ]
    faculty = [
        Faculty(name="Dr Rao", faculty_code="T-001", department="CSE", is_active=True),
        Faculty(name="Dr Iyer", faculty_code="T-002", department="ECE", is_active=True),
    ]
    subjects = [
        Subject(name="Python", code="CAP460", type="theory",
                session_length_hours=1, sessions_per_week=1, is_active=True),
        Subject(name="IoT", code="ECE282", type="practical", required_lab_type="iot",
                session_length_hours=1, sessions_per_week=1, is_active=True),
    ]
    rooms = [
        Room(room_number="101", block="36", floor="1", capacity=70,
             room_type="theory", is_active=True),
        Room(room_number="204", block="37", floor="2", capacity=70,
             room_type="lab", lab_type="iot", is_active=True),
    ]
    sections = [
        Section(academic_context_id=context.id, section_number="D2401",
                strength=64, is_active=True),
        Section(academic_context_id=context.id, section_number="D2402",
                strength=68, is_active=True),
    ]
    db_session.add_all(slots + faculty + subjects + rooms + sections)
    db_session.commit()

    run = TimetableRun(
        academic_context_id=context.id, version=1,
        status="OPTIMAL", publish_status="DRAFT",
    )
    db_session.add(run)
    db_session.commit()

    block = 0
    for si, section in enumerate(sections):
        for xi, (subject, room, fac) in enumerate(zip(subjects, rooms, faculty)):
            for slot in slots[xi * 2: xi * 2 + 2]:
                block += 1
                db_session.add(Assignment(
                    run_id=run.id, section_id=section.id, subject_id=subject.id,
                    faculty_id=fac.id, room_id=room.id, timeslot_id=slot.id,
                    block_id=block,
                ))
    # One pinned pair, so the `locked` filter has both sides to distinguish.
    db_session.add(SectionSubjectAssignment(
        academic_context_id=context.id, section_id=sections[0].id,
        subject_id=subjects[0].id, faculty_id=faculty[0].id,
        room_id=rooms[0].id, locked=True,
    ))
    db_session.commit()
    return run


def _expected(db_session, run, **kw):
    """The same answer, derived in Python from the run's rows.

    Intentionally a separate implementation: comparing the query against itself
    would prove nothing.
    """
    rows = db_session.query(Assignment).filter(Assignment.run_id == run.id).all()
    ctx = run.academic_context
    pinned = views.locked_pairs(db_session, run.academic_context_id)

    def keep(a):
        checks = {
            "section_id": lambda v: a.section_id == v,
            "faculty_id": lambda v: a.faculty_id == v,
            "subject_id": lambda v: a.subject_id == v,
            "room_id": lambda v: a.room_id == v,
            "subject_code": lambda v: a.subject.code == v,
            "block": lambda v: a.room.block == v,
            "floor": lambda v: a.room.floor == v,
            "room_type": lambda v: a.room.room_type == v,
            "day_index": lambda v: a.timeslot.day_index == v,
            "locked": lambda v: ((a.section_id, a.subject_id, a.session_type) in pinned) == v,
            "academic_year": lambda v: ctx.academic_year == v,
            "semester": lambda v: ctx.semester == v,
            "program": lambda v: ctx.program == v,
            "department": lambda v: ctx.department == v,
        }
        return all(checks[k](v) for k, v in kw.items() if v is not None)

    kept = [a for a in rows if keep(a)]
    kept.sort(key=lambda a: (
        a.timeslot.day_index, a.timeslot.period_index, a.section.section_number
    ))
    return [a.id for a in kept]


# ------------------------------------------------------------- equivalence


FILTER_CASES = [
    {},
    {"day_index": 0},
    {"day_index": 1},
    {"room_type": "lab"},
    {"room_type": "theory"},
    {"block": "36"},
    {"block": "37"},
    {"floor": "2"},
    {"subject_code": "CAP460"},
    {"locked": True},
    {"locked": False},
    {"block": "36", "day_index": 0},
    {"room_type": "lab", "locked": False},
    # A filter that matches nothing must return nothing, not everything.
    {"block": "99"},
    {"subject_code": "NOPE"},
]


@pytest.mark.parametrize("filters", FILTER_CASES)
def test_sql_filtering_matches_a_python_derivation(db_session, run_with_rows, filters):
    total, rows = views.query_master(
        db_session, run_with_rows, views.MasterFilters(**filters), limit=500, offset=0
    )
    expected = _expected(db_session, run_with_rows, **filters)
    assert [r.assignment_id for r in rows] == expected
    assert total == len(expected)


def test_context_filters_are_all_or_nothing(db_session, run_with_rows):
    """A run belongs to one context, so a context filter either admits every
    row or none. Turning it into a per-row test would silently change what an
    export contains."""
    ctx = run_with_rows.academic_context
    total, rows = views.query_master(
        db_session, run_with_rows,
        views.MasterFilters(program=ctx.program), limit=500, offset=0,
    )
    assert total == len(rows) > 0

    total, rows = views.query_master(
        db_session, run_with_rows,
        views.MasterFilters(program="NOT-THIS-ONE"), limit=500, offset=0,
    )
    assert (total, rows) == (0, [])


def test_ordering_is_day_then_period_then_section(db_session, run_with_rows):
    _, rows = views.query_master(
        db_session, run_with_rows, views.MasterFilters(), limit=500, offset=0
    )
    keys = [(r.day_index, r.period_index, r.section_number) for r in rows]
    assert keys == sorted(keys)


# --------------------------------------------------------------- pagination


def test_paging_walks_the_whole_result_without_gaps_or_repeats(db_session, run_with_rows):
    total, _ = views.query_master(
        db_session, run_with_rows, views.MasterFilters(), limit=1, offset=0
    )
    seen: list[int] = []
    for offset in range(0, total, 3):
        _, page = views.query_master(
            db_session, run_with_rows, views.MasterFilters(), limit=3, offset=offset
        )
        seen.extend(r.assignment_id for r in page)

    assert len(seen) == total
    assert len(set(seen)) == total
    assert seen == _expected(db_session, run_with_rows)


def test_total_is_the_filtered_count_not_the_page_size(db_session, run_with_rows):
    total, rows = views.query_master(
        db_session, run_with_rows, views.MasterFilters(), limit=2, offset=0
    )
    assert len(rows) == 2
    assert total > 2


def test_an_offset_past_the_end_returns_no_rows_but_the_real_total(db_session, run_with_rows):
    total, rows = views.query_master(
        db_session, run_with_rows, views.MasterFilters(), limit=10, offset=10_000
    )
    assert rows == []
    assert total > 0


# --------------------------------------------------------------------- cost


def _count_queries(db_session, fn):
    """Statements issued while `fn` runs.

    Callers warm the run and its context first: `expire_all()` would otherwise
    reload both inside the measured window and inflate every count by two,
    hiding the number that actually matters.
    """
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


def test_a_page_costs_a_fixed_number_of_queries(db_session, run_with_rows):
    """The point of the rewrite. Row count must not drive query count - if it
    does, the eager-loading has regressed and every related object is being
    fetched per row again.
    """
    def fetch(limit):
        views.query_master(
            db_session, run_with_rows, views.MasterFilters(), limit=limit, offset=0
        )

    db_session.expire_all()
    _ = run_with_rows.academic_context.program  # warm; not part of the measurement
    small = _count_queries(db_session, lambda: fetch(1))
    large = _count_queries(db_session, lambda: fetch(500))

    assert len(small) == len(large), (
        f"query count grew with page size: {len(small)} -> {len(large)}\n"
        + "\n".join(large)
    )
    # count + rows + locked pairs, and nothing per row.
    assert len(large) <= 4, "\n".join(large)


def test_deep_pages_cost_what_shallow_pages_cost(db_session, run_with_rows):
    def fetch(offset):
        views.query_master(
            db_session, run_with_rows, views.MasterFilters(), limit=2, offset=offset
        )

    db_session.expire_all()
    _ = run_with_rows.academic_context.program
    first = _count_queries(db_session, lambda: fetch(0))
    later = _count_queries(db_session, lambda: fetch(6))
    assert len(first) == len(later)


def test_export_streams_every_row_in_one_pass(db_session, run_with_rows):
    """`iter_master` must not paginate internally - that was the old export
    behaviour, and it multiplied the work by the number of pages."""
    db_session.expire_all()
    _ = run_with_rows.academic_context.program
    rows: list = []
    statements = _count_queries(
        db_session,
        lambda: rows.extend(
            views.iter_master(db_session, run_with_rows, views.MasterFilters())
        ),
    )
    assert [r.assignment_id for r in rows] == _expected(db_session, run_with_rows)
    assert len(statements) <= 3, "\n".join(statements)


def test_a_grid_does_not_query_per_class(db_session, run_with_rows):
    """Grid cells read five related objects each. Without joined loading, a
    forty-class grid became hundreds of queries."""
    section_id = (
        db_session.query(Assignment.section_id)
        .filter(Assignment.run_id == run_with_rows.id)
        .first()[0]
    )
    db_session.expire_all()
    _ = run_with_rows.academic_context.program
    statements = _count_queries(
        db_session,
        lambda: views.build_grid(
            db_session,
            run_with_rows,
            views.assignments_for(db_session, run_with_rows, section_id=section_id),
            title="t", subtitle="s", perspective="section",
        ),
    )
    assert len(statements) <= 5, "\n".join(statements)
