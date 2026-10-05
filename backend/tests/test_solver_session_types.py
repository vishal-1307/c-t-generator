"""Scheduling a subject that has both a lecture and a practical.

CAP460 is two lectures and four practical periods a week, the lectures in a
classroom and the practicals in a programming lab. Until now the solver's unit
of work was a (section, subject) pair with one weekly load and one kind of
room, so such a subject could only be modelled as two subjects with two
invented codes - and the codes in a timetable stopped matching the syllabus.

The unit is now (section, subject, component). Two properties matter most:

* a subject with one component produces exactly the problem it always did -
  the regression anchor, and why existing timetables are unaffected;
* the two components of a mixed subject are placed independently, in the kind
  of room each actually needs.
"""
from __future__ import annotations

import pytest

from app import domain
from app.models import Assignment, Room, Section, SectionSubjectAssignment, Subject
from app.solver.data import _eligible_rooms, load
from app.solver.run import generate


# --------------------------------------------------------------- fixtures


def _grid(db_session):
    from app.grid import build_grid

    db_session.add_all(build_grid(periods=6))
    db_session.commit()


def _rooms(db_session):
    rooms = [
        Room(room_number="101", block="36", room_code="36-101", capacity=70,
             room_type="theory", is_active=True, byod=True, charging=True),
        Room(room_number="102", block="36", room_code="36-102", capacity=70,
             room_type="theory", is_active=True, byod=False, charging=False),
        Room(room_number="201", block="36", room_code="36-201", capacity=70,
             room_type="lab", lab_type="programming", is_active=True,
             byod=True, charging=True),
        Room(room_number="202", block="36", room_code="36-202", capacity=70,
             room_type="lab", lab_type="electronics", is_active=True),
        Room(room_number="F1", block="36", room_code="36-F1", capacity=90,
             room_type="faculty", is_active=True),
    ]
    db_session.add_all(rooms)
    db_session.commit()
    return {r.room_code: r for r in rooms}


def _mixed_subject(db_session, **kw):
    base = dict(
        name="Fundamentals of Python", code="CAP460", type="mixed",
        session_length_hours=1, sessions_per_week=1,
        lecture_sessions_per_week=2, lecture_session_length=1,
        practical_sessions_per_week=2, practical_session_length=2,
        required_lab_type="programming", is_active=True,
    )
    base.update(kw)
    subject = Subject(**base)
    db_session.add(subject)
    db_session.commit()
    return subject


@pytest.fixture
def scheduled(db_session, context):
    """A section taking one mixed subject, with a teacher who can teach it."""
    from app.models import Faculty

    _grid(db_session)
    rooms = _rooms(db_session)
    subject = _mixed_subject(db_session)
    faculty = Faculty(name="Dr Rao", faculty_code="T-001", department="CSE",
                      is_active=True)
    section = Section(academic_context_id=context.id, section_number="D2401",
                      strength=60, is_active=True)
    db_session.add_all([faculty, section])
    db_session.commit()
    faculty.subjects.append(subject)
    section.subjects.append(subject)
    db_session.commit()
    return {"context": context, "subject": subject, "section": section,
            "faculty": faculty, "rooms": rooms, "db": db_session}


# ------------------------------------------------- the regression anchor


def test_a_single_component_subject_produces_exactly_one_obligation(
    db_session, context
):
    """Existing data must produce the problem it always produced.

    If this changes, every timetable already generated becomes suspect, so it
    is asserted before anything about mixed subjects.
    """
    from app.models import Faculty

    _grid(db_session)
    _rooms(db_session)
    subject = Subject(name="Data Structures", code="CAP3101", type="theory",
                      session_length_hours=1, sessions_per_week=3, is_active=True)
    faculty = Faculty(name="Dr Iyer", faculty_code="T-002", is_active=True)
    section = Section(academic_context_id=context.id, section_number="D2402",
                      strength=60, is_active=True)
    db_session.add_all([subject, faculty, section])
    db_session.commit()
    faculty.subjects.append(subject)
    section.subjects.append(subject)
    db_session.commit()

    inp = load(db_session, context.id)
    assert len(inp.pairs) == 1
    pair = inp.pairs[0]
    assert pair.session_type == domain.LECTURE
    assert (pair.sessions, pair.length) == (3, 1)


# ------------------------------------------------------ two components


def test_a_mixed_subject_becomes_two_independent_obligations(scheduled):
    inp = load(scheduled["db"], scheduled["context"].id)
    assert len(inp.pairs) == 2

    by_type = {p.session_type: p for p in inp.pairs}
    assert set(by_type) == {domain.LECTURE, domain.PRACTICAL}
    assert (by_type[domain.LECTURE].sessions, by_type[domain.LECTURE].length) == (2, 1)
    assert (by_type[domain.PRACTICAL].sessions, by_type[domain.PRACTICAL].length) == (2, 2)


def test_each_component_draws_from_the_kind_of_room_it_needs(scheduled):
    inp = load(scheduled["db"], scheduled["context"].id)
    by_type = {p.session_type: p for p in inp.pairs}
    rooms = {r.id: r for r in scheduled["db"].query(Room).all()}

    lecture_rooms = {rooms[i].room_type for i in by_type[domain.LECTURE].room_ids}
    practical_rooms = {rooms[i].room_type for i in by_type[domain.PRACTICAL].room_ids}
    assert lecture_rooms == {"theory"}
    assert practical_rooms == {"lab"}

    practical_labs = {rooms[i].lab_type for i in by_type[domain.PRACTICAL].room_ids}
    assert practical_labs == {"programming"}, "the wrong kind of lab is not eligible"


def test_generating_places_both_components_in_the_right_rooms(scheduled):
    db = scheduled["db"]
    result, run = generate(db, scheduled["context"].id, max_seconds=20)
    assert result.status in ("OPTIMAL", "FEASIBLE"), result.message
    assert run is not None

    rows = db.query(Assignment).filter_by(run_id=run.id).all()
    assert len(rows) == 2 * 1 + 2 * 2, "two lecture periods and four practical"

    lectures = [r for r in rows if r.session_type == domain.LECTURE]
    practicals = [r for r in rows if r.session_type == domain.PRACTICAL]
    assert len(lectures) == 2
    assert len(practicals) == 4
    assert all(r.room.room_type == "theory" for r in lectures)
    assert all(r.room.room_type == "lab" for r in practicals)
    assert all(r.room.lab_type == "programming" for r in practicals)


def test_the_two_components_are_decided_separately(scheduled):
    """Each component gets its own persisted faculty/room decision - one row
    for the pair would force a lecture and a practical to share a room."""
    db = scheduled["db"]
    generate(db, scheduled["context"].id, max_seconds=20)

    rows = db.query(SectionSubjectAssignment).all()
    assert {r.session_type for r in rows} == {domain.LECTURE, domain.PRACTICAL}
    assert len({r.room_id for r in rows}) == 2, (
        "a lecture and a practical cannot sensibly share one room"
    )


def test_a_multi_period_practical_stays_contiguous_on_one_day(scheduled):
    db = scheduled["db"]
    _result, run = generate(db, scheduled["context"].id, max_seconds=20)

    practicals = [
        r for r in db.query(Assignment).filter_by(run_id=run.id).all()
        if r.session_type == domain.PRACTICAL
    ]
    by_block: dict[int, list] = {}
    for row in practicals:
        by_block.setdefault(row.block_id, []).append(row.timeslot)

    assert len(by_block) == 2, "two practical sessions a week"
    for slots in by_block.values():
        assert len(slots) == 2
        assert len({s.day_index for s in slots}) == 1, "a block cannot span two days"
        periods = sorted(s.period_index for s in slots)
        assert periods[1] - periods[0] == 1, "a block must be contiguous"


# ------------------------------------------------- the independent checker


def test_the_checker_accepts_a_correct_mixed_timetable(scheduled):
    """The regression this file was missing.

    Every unit test above asserts what the solver produced. None of them asked
    the independent checker whether it agreed, and it did not: it measured a
    subject's weekly demand from the single legacy load, so a correct mixed
    timetable - six periods where the old columns say four - was reported as a
    hard-constraint violation. The checker gates manual edits, so this made
    every mixed subject unable to be edited.
    """
    from app.timetable_checker import check_all, one_faculty_per_pair

    db = scheduled["db"]
    _result, run = generate(db, scheduled["context"].id, max_seconds=20)

    assert check_all(db, run.id) == []
    assert one_faculty_per_pair(db, run.id) == []


def test_the_checker_still_notices_a_missing_practical(scheduled):
    """The counterpart: a checker that accepts a correct timetable is only
    useful if it still rejects a wrong one. Deleting the practical leaves the
    subject's total looking plausible against the legacy load."""
    from app.timetable_checker import check_all

    db = scheduled["db"]
    _result, run = generate(db, scheduled["context"].id, max_seconds=20)

    for row in db.query(Assignment).filter_by(run_id=run.id).all():
        if row.session_type == domain.PRACTICAL:
            db.delete(row)
    db.commit()

    problems = check_all(db, run.id)
    assert any("CAP460" in p for p in problems), problems


# ------------------------------------------------------- capabilities


def test_a_practical_needing_devices_and_power_only_gets_rooms_that_have_them(
    db_session, context
):
    """The point of recording BYOD and charging: a software practical can use
    a room that supports it, and must not be given one that cannot."""
    from app.models import Faculty

    _grid(db_session)
    db_session.add_all([
        Room(room_number="301", block="37", room_code="37-301", capacity=70,
             room_type="lab", lab_type="programming", is_active=True,
             byod=False, charging=False),
        Room(room_number="302", block="37", room_code="37-302", capacity=70,
             room_type="lab", lab_type="programming", is_active=True,
             byod=True, charging=True),
    ])
    subject = Subject(
        name="Databases", code="CAP3102", type="practical",
        session_length_hours=2, sessions_per_week=1,
        required_lab_type="programming",
        byod_required=True, charging_required=True, is_active=True,
    )
    faculty = Faculty(name="Dr Bose", faculty_code="T-003", is_active=True)
    section = Section(academic_context_id=context.id, section_number="D2403",
                      strength=60, is_active=True)
    db_session.add_all([subject, faculty, section])
    db_session.commit()
    faculty.subjects.append(subject)
    section.subjects.append(subject)
    db_session.commit()

    inp = load(db_session, context.id)
    rooms = {r.id: r for r in db_session.query(Room).all()}
    eligible = {rooms[i].room_code for i in inp.pairs[0].room_ids}
    assert eligible == {"37-302"}, "the room without power or BYOD is not eligible"


def test_capabilities_restrict_a_lecture_too(db_session, context, monkeypatch):
    """BYOD is charging points at the benches - a property of the room - so a
    lecture that needs it is restricted exactly as its practical is.

    This used to assert the opposite ("a lecture needs a room, not a bench").
    The department confirmed BYOD's meaning and reversed that; switched off,
    the old behaviour still holds, and that position is pinned below.
    """
    from app.config import settings

    _grid(db_session)
    _rooms(db_session)
    subject = _mixed_subject(db_session, byod_required=True, charging_required=True)
    section = Section(academic_context_id=context.id, section_number="D2404",
                      strength=60, is_active=True)
    db_session.add(section)
    db_session.commit()

    rooms = {r.id: r for r in db_session.query(Room).all()}
    lecture = _eligible_rooms(subject, section, rooms, domain.LECTURE)
    assert {r.room_code for r in lecture} == {"36-101"}, (
        "only the classroom with charging points may host a BYOD lecture"
    )

    practical = _eligible_rooms(subject, section, rooms, domain.PRACTICAL)
    assert {r.room_code for r in practical} == {"36-201"}

    monkeypatch.setattr(settings, "enforce_byod_on_lectures", False)
    lecture = _eligible_rooms(subject, section, rooms, domain.LECTURE)
    assert {r.room_code for r in lecture} == {"36-101", "36-102"}


def test_a_faculty_room_is_never_eligible_whatever_the_component(
    db_session, context
):
    _grid(db_session)
    rooms_by_code = _rooms(db_session)
    subject = _mixed_subject(db_session)
    section = Section(academic_context_id=context.id, section_number="D2405",
                      strength=60, is_active=True)
    db_session.add(section)
    db_session.commit()

    rooms = {r.id: r for r in db_session.query(Room).all()}
    office = rooms_by_code["36-F1"].id
    for component in (domain.LECTURE, domain.PRACTICAL):
        eligible = _eligible_rooms(subject, section, rooms, component)
        assert office not in {r.id for r in eligible}


def test_an_explicit_room_whitelist_still_overrides_capability_matching(
    db_session, context
):
    """`allowed_rooms` is an administrator's instruction, and outranks the
    capability rules - but never the universal ones, so it still cannot put a
    class in an office or a room too small for the section."""
    _grid(db_session)
    rooms_by_code = _rooms(db_session)
    subject = _mixed_subject(db_session)
    subject.allowed_rooms = [rooms_by_code["36-102"]]
    section = Section(academic_context_id=context.id, section_number="D2406",
                      strength=60, is_active=True)
    db_session.add(section)
    db_session.commit()

    rooms = {r.id: r for r in db_session.query(Room).all()}
    for component in (domain.LECTURE, domain.PRACTICAL):
        eligible = _eligible_rooms(subject, section, rooms, component)
        assert {r.room_code for r in eligible} == {"36-102"}
