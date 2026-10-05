"""Two spreadsheets in, a checked timetable out.

The whole point of the product, asserted end to end: upload the department's
two files, validate, generate, and then verify the result against the rules
*from the rows*, not from anything the solver reported about itself.

Deliberately not a happy-path smoke test. The assertions at the bottom are
re-derived here - demand, contiguity, capacity, room kind, BYOD, the
parent/group overlap, theory once a day, the faculty free period and
the working week - because a timetable that is wrong in any of those ways
still looks complete, and `check_all` agreeing with the solver would prove only
that two pieces of code share an assumption.
"""
from __future__ import annotations

from collections import defaultdict

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import jobs, timetable_checker
from app.grid import build_grid
from app.models import Assignment, Room, Section, Subject, TimeSlot
from tests.test_teacher_import import (
    CONTEXT,
    INFRA_HEADERS,
    INFRA_ROWS,
    LOAD_HEADERS,
    LOAD_ROWS,
    _xlsx,
)


@pytest.fixture
def workspace():
    """A client whose background solve thread shares the test database.

    `jobs` runs a solve on its own thread with its own session, so without
    pointing its factory at this engine the thread would query the real
    database - and generation could never be tested end to end.
    """
    from fastapi.testclient import TestClient

    from app.auth import hash_password
    from app.database import Base, get_db
    from app.main import app
    from app.models import User

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    db.add(User(username="admin", hashed_password=hash_password("pw12345678"),
                role="admin", is_active=True))
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    previous = jobs.session_factory
    jobs.use_session_factory(Session)
    client = TestClient(app)
    token = client.post(
        "/api/auth/login", json={"username": "admin", "password": "pw12345678"}
    ).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"

    # A week set up the old way, with a common lunch at P5. The department's
    # rule is that there is none, and the import has to say so and remove it -
    # which this test then checks from the rows.
    slots = build_grid(periods=9)
    for slot in slots:
        if slot.period_index == 4:
            slot.is_lunch = True
    db.add_all(slots)
    db.commit()

    yield {"client": client, "db": db}

    app.dependency_overrides.clear()
    jobs.use_session_factory(previous)
    db.close()


def test_two_spreadsheets_become_a_checked_timetable(workspace):
    client, db = workspace["client"], workspace["db"]

    # ---------------------------------------------------------- 1. import
    resp = client.post(
        "/api/teacher/apply",
        files={
            "load": ("Load.xlsx", _xlsx(LOAD_HEADERS, LOAD_ROWS), "application/vnd.ms-excel"),
            "infra": ("Infra.xlsx", _xlsx(INFRA_HEADERS, INFRA_ROWS), "application/vnd.ms-excel"),
        },
        data=CONTEXT,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["committed"] is True
    assert resp.json()["readiness"]["ready"] is True
    assert any("no common lunch" in w for w in resp.json()["warnings"])

    # ---------------------------------------------------------- 2. validate
    context = client.get("/api/academic-contexts").json()[0]
    report = client.get(f"/api/validate?academic_context_id={context['id']}").json()
    blockers = [c for c in report["checks"] if not c["ok"] and c["severity"] == "blocker"]
    assert report["ready"] is True, blockers
    assert not blockers

    # The group-size shortfall is reported, and does not block.
    warnings = [c["label"] for c in report["checks"]
                if not c["ok"] and c["severity"] == "warning"]
    assert "Lab group sizes match their section" in warnings

    # ---------------------------------------------------------- 3. generate
    started = client.post(
        "/api/generate", json={"academic_context_id": context["id"], "max_seconds": 60}
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]
    jobs.wait(run_id, timeout=300)
    db.expire_all()

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] in {"OPTIMAL", "FEASIBLE"}, run

    # -------------------------------------------------- 4. check the rows
    assert timetable_checker.check_all(db, run_id) == []

    rows = db.query(Assignment).filter(Assignment.run_id == run_id).all()
    sections = {s.id: s for s in db.query(Section).all()}
    subjects = {s.id: s for s in db.query(Subject).all()}
    rooms = {r.id: r for r in db.query(Room).all()}
    slots = {s.id: s for s in db.query(TimeSlot).all()}

    # 9 obligations x 4 sessions x their own duration.
    assert len(rows) == 52

    # Every obligation gets exactly the periods its subject asks for. This is
    # what would break if "No of Hrs/Duration" were ever read as a frequency.
    placed = defaultdict(int)
    for a in rows:
        placed[(a.section_id, a.subject_id)] += 1
    for (section_id, subject_id), got in placed.items():
        subject = subjects[subject_id]
        want = subject.sessions_per_week * subject.session_length_hours
        assert got == want, (
            f"{sections[section_id].section_number}/{subject.code} got {got} "
            f"periods, needs {want}"
        )
    assert len(placed) == 9

    # Every block sits on one day, in consecutive periods.
    blocks = defaultdict(list)
    for a in rows:
        blocks[a.block_id].append(slots[a.timeslot_id])
    for block_id, block in blocks.items():
        block.sort(key=lambda s: (s.day_index, s.period_index))
        assert len({s.day_index for s in block}) == 1, f"block {block_id} spans days"
        periods = [s.period_index for s in block]
        assert periods == list(range(periods[0], periods[0] + len(periods)))

    # A three-period lab really was placed as three consecutive periods, four
    # times. This is the shape "duration 3, four times a week" has to produce,
    # and the one that would silently become twelve single periods if duration
    # were ever read as a frequency.
    ece102 = next(s for s in subjects.values() if s.code == "ECE102")
    ece102_blocks = {a.block_id for a in rows if a.subject_id == ece102.id}
    assert len(ece102_blocks) == 4, "ECE102 should be four sessions"
    for block_id in ece102_blocks:
        assert len(blocks[block_id]) == 3, "each ECE102 session is three periods"

    # A group and its section never share a period.
    for section in sections.values():
        if section.parent_section_id is None:
            continue
        mine = {a.timeslot_id for a in rows if a.section_id == section.id}
        theirs = {a.timeslot_id for a in rows if a.section_id == section.parent_section_id}
        assert not (mine & theirs), (
            f"{section.section_number} overlaps the section it belongs to"
        )

    # No common lunch is left in the week.
    assert not [s for s in slots.values() if s.is_lunch]

    # Nothing in a room too small, of the wrong kind, or without the BYOD
    # charging points its class needs - lecture or lab.
    assert not [a for a in rows
                if rooms[a.room_id].capacity < sections[a.section_id].strength]
    assert not [a for a in rows if rooms[a.room_id].room_type == "faculty"]
    for a in rows:
        want = "lab" if subjects[a.subject_id].type == "practical" else "theory"
        assert rooms[a.room_id].room_type == want
        if subjects[a.subject_id].byod_required:
            assert rooms[a.room_id].byod, (
                f"{subjects[a.subject_id].code} needs BYOD and got "
                f"{rooms[a.room_id].room_code}"
            )

    # Monday to Friday only.
    assert {slots[a.timeslot_id].day for a in rows} <= {
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}

    # A theory subject meets a section at most once a day.
    per_day = defaultdict(set)
    for a in rows:
        if subjects[a.subject_id].type != "practical":
            per_day[(a.section_id, a.subject_id, slots[a.timeslot_id].day)].add(a.block_id)
    repeats = {k: v for k, v in per_day.items() if len(v) > 1}
    assert not repeats, f"a theory subject met a section twice in a day: {repeats}"

    # Every teacher has a free period on each day they teach.
    periods_per_day = defaultdict(set)
    for slot in slots.values():
        periods_per_day[slot.day].add(slot.period_index)
    teaching = defaultdict(set)
    for a in rows:
        slot = slots[a.timeslot_id]
        teaching[(a.faculty_id, slot.day)].add(slot.period_index)
    for (faculty_id, day), periods in teaching.items():
        assert periods_per_day[day] - periods, (
            f"faculty {faculty_id} taught every period of {day} with no break"
        )

    # The result page's "hard conflicts" figure, from its own endpoint.
    checked = client.get(f"/api/runs/{run_id}/check").json()
    assert {k: checked[k] for k in ("run_id", "violations", "count")} == {
        "run_id": run_id, "violations": [], "count": 0,
    }
    assert 0 < checked["longest_faculty_run"] <= checked["max_consecutive"] == 6

    # ------------------------------------------------- 5. and it exports
    csv = client.get(f"/api/export/master.csv?academic_context_id={context['id']}")
    assert csv.status_code == 200
    assert len(csv.text.strip().splitlines()) == 52 + 1  # + header
