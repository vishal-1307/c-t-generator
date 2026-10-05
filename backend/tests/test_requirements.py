"""College requirements, checked against TIMETABLE_LOGIC_SPEC.md.

Phase 4 closed most of the gaps this file used to pin as ``xfail``. Per the
project's own rule, a requirement that now passes keeps its test - the xfail
marker is removed, not the test - so this file remains the living record of
"real college rule -> does the code actually do it", not a snapshot of what was
missing in an earlier phase.

Requirements already covered in depth by passing tests elsewhere (multi-section
clashes, shared faculty/rooms, capacity, availability derivation, infeasibility
diagnostics, persistence via SectionSubjectAssignment, locking) are exercised
here only enough to prove the *specific spec line* is satisfied - see
tests/test_solver_all_sections.py, tests/test_infeasible.py,
tests/test_generate_api.py and tests/test_solver_single_section.py for the
full behavioral coverage.

Two genuine gaps remain and stay ``xfail(strict=True)`` below: a manual
single-assignment move endpoint, and a frontend room-timetable page.
"""
from __future__ import annotations

import pytest

from app.grid import DEFAULT_DAYS, build_grid
from app.models import AcademicContext, Faculty, FacultyUnavailability, Room, Section, Subject
from app.solver.data import load
from app.solver.run import generate


def mk_context(db, year="2026-27", semester=1, program="BCA", department="CSE"):
    ctx = AcademicContext(academic_year=year, semester=semester, program=program, department=department)
    db.add(ctx)
    db.flush()
    return ctx


def two_section_two_room_dataset(db):
    """Two sections, two equally-eligible theory rooms, one subject with two
    eligible faculty - the minimal case that exercises the room-pool."""
    ctx = mk_context(db)
    subject = Subject(name="Data Structures", code="CS201", type="theory",
                       session_length_hours=1, sessions_per_week=3)
    db.add(subject)
    db.flush()

    room_a = Room(room_number="A101", block="A", floor="1", capacity=70, room_type="theory")
    room_b = Room(room_number="A102", block="A", floor="1", capacity=70, room_type="theory")
    db.add_all([room_a, room_b])
    db.flush()

    fac_a = Faculty(name="Dr. A", faculty_code="FAC01")
    fac_b = Faculty(name="Dr. B", faculty_code="FAC02")
    fac_a.subjects = fac_b.subjects = [subject]
    db.add_all([fac_a, fac_b])

    sec_a = Section(academic_context_id=ctx.id, section_number="S-A", strength=60)
    sec_b = Section(academic_context_id=ctx.id, section_number="S-B", strength=60)
    sec_a.subjects = sec_b.subjects = [subject]
    db.add_all([sec_a, sec_b])

    db.add_all(build_grid(days=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]))
    db.commit()
    return {"context": ctx, "subject": subject, "rooms": (room_a, room_b), "sections": (sec_a, sec_b)}


def symmetric_persistence_dataset(db):
    """2 sections x 3 subjects, 2 faculty each eligible for every subject -
    enough genuine symmetry that CP-SAT's parallel workers would not converge
    on the same tie-break twice if nothing pinned the choice. That is exactly
    what makes this the right fixture to prove persistence actually holds: if
    it held only by accident (e.g. a trivially small search space), this test
    would pass for the wrong reason."""
    ctx = mk_context(db)
    subjects = {}
    for name, code, type_, length, spw in [
        ("Data Structures", "CS201", "theory", 1, 3),
        ("Operating Systems", "CS202", "theory", 1, 3),
        ("DS Lab", "CS251", "practical", 2, 1),
    ]:
        s = Subject(name=name, code=code, type=type_,
                    session_length_hours=length, sessions_per_week=spw)
        db.add(s)
        subjects[code] = s
    db.flush()

    for number, room_type, pin in [
        ("A101", "theory", None), ("A102", "theory", None),
        ("LAB1", "lab", "CS251"), ("LAB2", "lab", "CS251"),
    ]:
        db.add(Room(room_number=number, block="A", floor="1", capacity=70, room_type=room_type,
                     fixed_subject_id=subjects[pin].id if pin else None))
    db.flush()

    for fname, fcode in [("Dr. A", "FAC01"), ("Dr. B", "FAC02")]:
        f = Faculty(name=fname, faculty_code=fcode)
        f.subjects = list(subjects.values())   # both eligible for everything
        db.add(f)

    sections = {}
    for number in ("S-A", "S-B"):
        sec = Section(academic_context_id=ctx.id, section_number=number, strength=60)
        sec.subjects = list(subjects.values())
        db.add(sec)
        sections[number] = sec

    db.add_all(build_grid(days=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]))
    db.commit()
    return {"context": ctx, "subjects": subjects, "sections": sections}


# --------------------------------------------------------------------- working days


def test_default_grid_is_monday_to_friday_only():
    """TIMETABLE_LOGIC_SPEC.md #17: The college's working week is Mon-Fri."""
    assert DEFAULT_DAYS == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def test_build_grid_defaults_to_five_days():
    slots = build_grid()
    days = {s.day for s in slots}
    assert days == {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}
    assert "Saturday" not in days


# ------------------------------------------------------------- room allocation


def test_theory_can_use_any_capacity_eligible_room(db_session):
    """TIMETABLE_LOGIC_SPEC.md #8: theory is placed from a capacity-eligible
    ROOM POOL, not pinned to one per-section home room. Both A101 and A102 are
    theory-typed and capacity-eligible for S-A, so both must be candidates."""
    data = two_section_two_room_dataset(db_session)
    inp = load(db_session, academic_context_id=data["context"].id)
    pair = next(p for p in inp.pairs if p.section_number == "S-A")

    room_a, room_b = data["rooms"]
    assert set(pair.room_ids) == {room_a.id, room_b.id}, (
        f"expected both eligible rooms, got {pair.room_ids}"
    )


def test_lab_eligibility_is_by_type_not_per_subject_pin(db_session):
    """TIMETABLE_LOGIC_SPEC.md #8: labs are matched by lab_type/capability, not
    pinned one-lab-to-one-subject."""
    ctx = mk_context(db_session)
    practical = Subject(name="Physics Lab", code="PH251", type="practical",
                        session_length_hours=2, sessions_per_week=1,
                        required_lab_type="PHYSICS")
    db_session.add(practical)
    db_session.flush()

    lab_a = Room(room_number="LAB1", block="C", floor="1", capacity=70, room_type="lab", lab_type="PHYSICS")
    lab_b = Room(room_number="LAB2", block="C", floor="1", capacity=70, room_type="lab", lab_type="PHYSICS")
    other_type_lab = Room(room_number="LAB3", block="C", floor="1", capacity=70, room_type="lab", lab_type="CHEMISTRY")
    db_session.add_all([lab_a, lab_b, other_type_lab])

    section = Section(academic_context_id=ctx.id, section_number="S-A", strength=60)
    section.subjects = [practical]
    db_session.add(section)
    db_session.add_all(build_grid())
    db_session.commit()

    inp = load(db_session, academic_context_id=ctx.id)
    pair = next(p for p in inp.pairs if p.subject_code == "PH251")
    assert set(pair.room_ids) == {lab_a.id, lab_b.id}, (
        "expected both same-type labs eligible, and the different-type lab excluded"
    )


def test_faculty_rooms_are_excluded_from_scheduling(db_session):
    """TIMETABLE_LOGIC_SPEC.md #8: rooms flagged as faculty rooms are never
    offered to the scheduler, even when their type/capacity would otherwise
    match - the exclusion is a real filter, not just a naming convention."""
    ctx = mk_context(db_session)
    subject = Subject(name="Data Structures", code="CS201", type="theory",
                       session_length_hours=1, sessions_per_week=1)
    db_session.add(subject)
    db_session.flush()

    faculty_room = Room(room_number="F-201", block="A", floor="2", capacity=70, room_type="faculty")
    theory_room = Room(room_number="A101", block="A", floor="1", capacity=70, room_type="theory")
    db_session.add_all([faculty_room, theory_room])

    section = Section(academic_context_id=ctx.id, section_number="S-A", strength=60)
    section.subjects = [subject]
    db_session.add(section)
    db_session.add_all(build_grid())
    db_session.commit()

    inp = load(db_session, academic_context_id=ctx.id)
    pair = next(p for p in inp.pairs if p.subject_code == "CS201")
    assert faculty_room.id not in pair.room_ids
    assert pair.room_ids == [theory_room.id]


# --------------------------------------------------------------- availability


def test_faculty_unavailability_is_respected(db_session):
    """TIMETABLE_LOGIC_SPEC.md #11: faculty can have declared unavailability,
    and the solver's candidate slots must exclude it."""
    ctx = mk_context(db_session)
    subject = Subject(name="X", code="X1", type="theory",
                      session_length_hours=1, sessions_per_week=1)
    db_session.add(subject)
    db_session.flush()
    room = Room(room_number="A101", block="A", floor="1", capacity=70, room_type="theory")
    db_session.add(room)
    db_session.flush()
    faculty = Faculty(name="Dr. A", faculty_code="FAC01")
    faculty.subjects = [subject]
    db_session.add(faculty)
    db_session.flush()
    section = Section(academic_context_id=ctx.id, section_number="S-A", strength=60)
    section.subjects = [subject]
    db_session.add(section)
    slots = build_grid(days=["Monday"])
    db_session.add_all(slots)
    db_session.flush()

    blocked_slot = slots[0]
    db_session.add(FacultyUnavailability(faculty_id=faculty.id, timeslot_id=blocked_slot.id))
    db_session.commit()

    inp = load(db_session, academic_context_id=ctx.id)
    blocked_index = next(s.index for s in inp.slots if s.id == blocked_slot.id)
    assert blocked_index in inp.blocked_faculty_slots.get(faculty.id, set())


# ------------------------------------------------------------- persistence


def test_faculty_assignment_is_stable_across_regeneration(db_session):
    """TIMETABLE_LOGIC_SPEC.md #12/#9: once a section-subject has a persisted
    assignment, regeneration must reproduce it exactly - not by solver luck,
    but because SectionSubjectAssignment pre-filters the pair's candidates
    before the model is even built. Sampling 5 regenerations (not 2) is the
    same discipline the earlier probabilistic version of this test used:
    with genuine symmetry in the fixture, a 2-sample match could happen by
    chance even without a real persistence mechanism."""
    symmetric_persistence_dataset(db_session)
    ctx_id = db_session.query(AcademicContext).one().id

    snapshots = []
    for _ in range(5):
        _, run = generate(db_session, academic_context_id=ctx_id, soft=False, max_seconds=15)
        snapshots.append(
            {(a.section_id, a.subject_id): a.faculty_id for a in run.assignments}
        )

    assert all(snap == snapshots[0] for snap in snapshots), (
        "faculty assignment varied across regenerations of identical input"
    )


# ------------------------------------------------------------------- locking


def test_locked_assignment_survives_regeneration(db_session):
    """TIMETABLE_LOGIC_SPEC.md #14: locking a section-subject's persisted
    assignment (faculty + room + slot) must make regeneration reproduce it
    exactly, until explicitly unlocked."""
    from app.models import SectionSubjectAssignment

    data = two_section_two_room_dataset(db_session)
    ctx_id = data["context"].id
    _, run1 = generate(db_session, academic_context_id=ctx_id, soft=False, max_seconds=15)

    locked_assignment = next(iter(run1.assignments))
    ssa = (
        db_session.query(SectionSubjectAssignment)
        .filter(
            SectionSubjectAssignment.academic_context_id == ctx_id,
            SectionSubjectAssignment.section_id == locked_assignment.section_id,
            SectionSubjectAssignment.subject_id == locked_assignment.subject_id,
        )
        .one()
    )
    ssa.locked = True
    db_session.commit()

    before = (
        locked_assignment.faculty_id, locked_assignment.room_id, locked_assignment.timeslot_id,
    )
    _, run2 = generate(db_session, academic_context_id=ctx_id, soft=False, max_seconds=15)
    same = [
        a for a in run2.assignments
        if a.section_id == locked_assignment.section_id
        and a.subject_id == locked_assignment.subject_id
    ]
    after = {(a.faculty_id, a.room_id, a.timeslot_id) for a in same}
    assert before in after, (before, after)


# ------------------------------------------------------------- year/semester


def test_sections_are_scoped_to_an_academic_year_and_semester(db_session):
    """TIMETABLE_LOGIC_SPEC.md #3/#12: a section belongs to exactly one
    academic year/semester/program/department, via a normalized
    AcademicContext rather than duplicated flat columns - the requirement is
    that a 2026-27/Sem-1 section and a 2026-27/Sem-3 section can never be
    confused, which this proves directly."""
    ctx1 = mk_context(db_session, semester=1)
    ctx3 = mk_context(db_session, semester=3)
    sec1 = Section(academic_context_id=ctx1.id, section_number="D2402", strength=60)
    sec3 = Section(academic_context_id=ctx3.id, section_number="D2402", strength=60)
    db_session.add_all([sec1, sec3])
    db_session.commit()

    assert sec1.id != sec3.id
    assert sec1.academic_context.semester == 1
    assert sec3.academic_context.semester == 3
    # Same section_number is legal in two different semesters precisely
    # because the scope is the (context, number) pair, not the number alone.
    assert sec1.section_number == sec3.section_number


# ------------------------------------------------------------------ master view


def test_master_timetable_endpoint_exists(client):
    """TIMETABLE_LOGIC_SPEC.md #17: a master/school view, filterable by year,
    semester, program, section, faculty, subject, block, room, day - full
    filter behavior is covered in test_generate_api.py's master-view tests."""
    r = client.get("/api/timetable/master")
    # 404 here means "no run has been generated yet", not "endpoint missing" -
    # the endpoint itself existing is the point of this test.
    assert r.status_code in (200, 404)
    assert r.status_code != 404 or "generated" in r.json()["detail"].lower()


# --------------------------------------------------------------- manual changes


def test_manual_move_is_validated_against_hard_constraints(client, db_session):
    """TIMETABLE_LOGIC_SPEC.md #15/Phase 6: an admin can move a single
    assignment, validated against the same hard constraints as generation -
    POST /api/assignments/{id}/move, backed by app/manual_edit.py, reusing
    _eligible_rooms/availability tables/tests/timetable_checker.check_all the
    same way the solver does."""
    data = two_section_two_room_dataset(db_session)
    _, run = generate(db_session, academic_context_id=data["context"].id, soft=False, max_seconds=15)
    assignment = next(iter(run.assignments))

    # Attempt to move it onto a slot already occupied by another class in the
    # same section - this must be rejected with a reason (409), not silently
    # applied and not a 404 saying the endpoint does not exist.
    other = next(
        a for a in run.assignments
        if a.section_id == assignment.section_id and a.id != assignment.id
    )
    r = client.post(
        f"/api/assignments/{assignment.id}/move",
        json={"target_timeslot_id": other.timeslot_id},
    )
    assert r.status_code == 409, (
        f"expected a validated rejection (409), got {r.status_code}"
    )
    assert "section" in r.json()["detail"].lower()


# -------------------------------------------------------------------- room view


def test_room_timetable_page_exists():
    """TIMETABLE_LOGIC_SPEC.md #17/Phase 8: the room grid has a frontend page,
    reachable from /timetables/rooms. The API endpoint it renders
    (GET /api/timetable/room/{id}) has existed since Phase 3."""
    import pathlib

    page = (
        pathlib.Path(__file__).resolve().parents[2]
        / "frontend" / "app" / "timetable" / "room" / "[id]" / "page.tsx"
    )
    assert page.exists(), f"expected {page}"
