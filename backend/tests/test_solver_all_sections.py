"""Phase 3 part 2 (updated for the Phase 4 schema): all sections solved
simultaneously.

The point of this file is the failure mode single-section testing cannot reach.
With one section, HC3 (a section cannot be in two places) already forbids
simultaneous classes, so HC1 (faculty clash) and HC2 (room clash) are satisfied
vacuously - they would pass even if the constraints were deleted.

They only bite when two *different* sections compete for the same teacher or the
same lab, so the fixture here deliberately forces that competition:

* one faculty is the ONLY person able to teach a subject every section takes,
* one lab is the ONLY room matching the lab type a shared practical requires.

Theory rooms are now drawn from a shared eligible pool (no more per-section
home rooms), so this file is also where cross-section theory-room contention
is proven to stay collision-free.
"""
from __future__ import annotations

from collections import defaultdict

import pytest

from app.grid import build_grid
from app.models import Assignment, Faculty, Room, Section, Subject
from app.solver.data import load
from app.solver.run import generate

from constraint_checker import check_all, one_faculty_per_pair


@pytest.fixture
def multi_section(db_session, context):
    """Four sections that must share one teacher and one lab."""
    db = db_session

    subjects = {}
    for name, code, type_, length, spw, lab_type in [
        ("Data Structures", "CS201", "theory", 1, 4, None),
        ("Operating Systems", "CS202", "theory", 1, 3, None),
        ("Shared Theory", "SH301", "theory", 1, 3, None),       # only one eligible teacher
        ("Shared Lab", "SH351", "practical", 2, 1, "SPECIALIZED"),  # only one eligible lab
    ]:
        s = Subject(
            name=name, code=code, type=type_,
            session_length_hours=length, sessions_per_week=spw,
            required_lab_type=lab_type,
        )
        db.add(s)
        subjects[code] = s
    db.flush()

    rooms = {}
    for number, cap, room_type, lab_type in [
        ("A101", 70, "theory", None),
        ("A102", 70, "theory", None),
        ("A103", 70, "theory", None),
        ("A104", 70, "theory", None),
        ("LAB1", 70, "lab", "SPECIALIZED"),   # the single shared lab
    ]:
        r = Room(room_number=number, block="A", floor="1", capacity=cap, room_type=room_type, lab_type=lab_type)
        db.add(r)
        rooms[number] = r
    db.flush()

    faculty = {}
    for fname, fcode, teaches in [
        ("Dr. Solo", "SOLO", ["SH301"]),          # sole teacher of a shared subject
        ("Dr. Lab", "LABF", ["SH351"]),           # sole teacher of the shared lab
        ("Dr. A", "FAC01", ["CS201", "CS202"]),
        ("Dr. B", "FAC02", ["CS201", "CS202"]),
        ("Dr. C", "FAC03", ["CS201", "CS202"]),
    ]:
        f = Faculty(name=fname, faculty_code=fcode)
        f.subjects = [subjects[c] for c in teaches]
        db.add(f)
        faculty[fcode] = f

    sections = {}
    for number in ("CSE-3A", "CSE-3B", "CSE-3C", "CSE-3D"):
        sec = Section(academic_context_id=context.id, section_number=number, strength=60)
        sec.subjects = list(subjects.values())
        db.add(sec)
        sections[number] = sec

    slots = build_grid()
    for s in slots:
        if s.period_index == 4:
            s.is_lunch = True
    db.add_all(slots)
    db.commit()

    return {"db": db, "context": context, "subjects": subjects, "rooms": rooms,
            "faculty": faculty, "sections": sections}


def solve_all(fixture, **kwargs):
    kwargs.setdefault("soft", False)
    kwargs.setdefault("max_seconds", 20)
    result, run = generate(fixture["db"], academic_context_id=fixture["context"].id, **kwargs)
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.message
    return run, fixture["db"].query(Assignment).filter(
        Assignment.run_id == run.id
    ).all()


# ------------------------------------------------------------------- baseline


def test_all_sections_solve_together(multi_section):
    run, rows = solve_all(multi_section)
    # 4 sections x (4 + 3 + 3 theory + 2 practical periods) = 4 x 12 = 48
    assert len(rows) == 48
    per_section = defaultdict(int)
    for a in rows:
        per_section[a.section.section_number] += 1
    assert set(per_section.values()) == {12}, dict(per_section)


def test_no_hard_constraint_violated_across_all_sections(multi_section):
    db = multi_section["db"]
    run, _ = solve_all(multi_section)
    violations = check_all(db, run.id) + one_faculty_per_pair(db, run.id)
    assert violations == [], "\n".join(violations)


# ------------------------------------------------- HC1 across sections (the point)


def test_shared_faculty_is_never_in_two_sections_at_once(multi_section):
    """Dr. Solo is the ONLY teacher of SH301, which all four sections take.
    Every one of those 12 periods must land in a distinct slot."""
    db = multi_section["db"]
    run, rows = solve_all(multi_section)

    solo = [a for a in rows if a.faculty.faculty_code == "SOLO"]
    assert {a.section.section_number for a in solo} == {
        "CSE-3A", "CSE-3B", "CSE-3C", "CSE-3D",
    }, "fixture failed to force cross-section sharing"
    assert len(solo) == 12, "4 sections x 3 sessions of SH301"

    slots = [a.timeslot_id for a in solo]
    assert len(set(slots)) == len(slots), "sole teacher double-booked across sections"


def test_no_faculty_anywhere_is_double_booked(multi_section):
    run, rows = solve_all(multi_section)
    seen = defaultdict(list)
    for a in rows:
        seen[(a.faculty_id, a.timeslot_id)].append(
            f"{a.section.section_number}/{a.subject.code}"
        )
    clashes = {k: v for k, v in seen.items() if len(v) > 1}
    assert clashes == {}, clashes


def test_fixture_actually_shares_faculty_across_sections(multi_section):
    """Guards the guard: if every faculty served one section, the tests above
    would pass vacuously."""
    run, rows = solve_all(multi_section)
    spread = defaultdict(set)
    for a in rows:
        spread[a.faculty.faculty_code].add(a.section.section_number)
    shared = {f: s for f, s in spread.items() if len(s) > 1}
    assert shared, "no faculty spans sections - HC1 would be untested"
    assert len(spread["SOLO"]) == 4


# --------------------------------------------------- HC2 across sections (the point)


def test_shared_lab_is_never_double_booked(multi_section):
    """LAB1 is the only room matching SH351's required lab type, and all four
    sections take it."""
    db = multi_section["db"]
    run, rows = solve_all(multi_section)

    lab_rows = [a for a in rows if a.room.room_number == "LAB1"]
    assert len(lab_rows) == 8, "4 sections x one 2-hour session"
    assert {a.section.section_number for a in lab_rows} == {
        "CSE-3A", "CSE-3B", "CSE-3C", "CSE-3D",
    }

    slots = [a.timeslot_id for a in lab_rows]
    assert len(set(slots)) == len(slots), "shared lab double-booked"


def test_no_room_anywhere_is_double_booked(multi_section):
    run, rows = solve_all(multi_section)
    seen = defaultdict(list)
    for a in rows:
        seen[(a.room_id, a.timeslot_id)].append(
            f"{a.section.section_number}/{a.subject.code}"
        )
    assert {k: v for k, v in seen.items() if len(v) > 1} == {}


def test_theory_rooms_stay_within_the_eligible_pool(multi_section):
    """Theory rooms are no longer pinned per section - they come from a shared
    pool of 4 rooms. Every theory placement must still land in one of them,
    and no two sections' theory classes may collide on the same room+slot
    (already proven generally by test_no_room_anywhere_is_double_booked; this
    just confirms the pool is actually being used, not silently narrowed)."""
    run, rows = solve_all(multi_section)
    theory_rooms = {"A101", "A102", "A103", "A104"}
    used = {a.room.room_number for a in rows if a.subject.type == "theory"}
    assert used <= theory_rooms
    assert len(used) > 1, "fixture failed to exercise the shared theory pool"


# ------------------------------------------------------------ fault injection
#
# Removing a constraint only *permits* a violation; it does not force one. To
# prove the checker actually detects cross-section clashes, corrupt the stored
# rows and confirm each is caught.


def test_checker_detects_injected_cross_section_faculty_clash(multi_section):
    db = multi_section["db"]
    run, rows = solve_all(multi_section)
    assert check_all(db, run.id) == []

    a = next(r for r in rows if r.section.section_number == "CSE-3A")
    b = next(
        r for r in rows
        if r.section.section_number == "CSE-3B" and r.timeslot_id != a.timeslot_id
    )
    original = b.timeslot_id, b.faculty_id
    # Put B's class on A's slot with A's teacher: one person, two sections, one time.
    b.timeslot_id, b.faculty_id = a.timeslot_id, a.faculty_id
    db.commit()

    violations = check_all(db, run.id)
    assert any(v.startswith("HC1") for v in violations), violations

    b.timeslot_id, b.faculty_id = original
    db.commit()


def test_checker_detects_injected_cross_section_room_clash(multi_section):
    db = multi_section["db"]
    run, rows = solve_all(multi_section)

    a = next(r for r in rows if r.section.section_number == "CSE-3A")
    b = next(
        r for r in rows
        if r.section.section_number == "CSE-3B" and r.timeslot_id != a.timeslot_id
    )
    original = b.timeslot_id, b.room_id
    b.timeslot_id, b.room_id = a.timeslot_id, a.room_id
    db.commit()

    violations = check_all(db, run.id)
    assert any(v.startswith("HC2") for v in violations), violations

    b.timeslot_id, b.room_id = original
    db.commit()


# ------------------------------------------------------------------- capacity


def test_solves_at_exact_capacity_with_no_slack(multi_section):
    """Squeeze the grid to exactly the demand and confirm it still solves cleanly.

    Each section needs 12 periods and only 12 teachable slots remain, so every
    section's timetable is completely full. Dr. Solo owes 3 periods to each of
    the 4 sections - 12 in total - so he must occupy every single slot, each
    with a different section. There is zero slack anywhere: any clash at all
    makes it infeasible, which is what makes this a real test of HC1/HC2.
    """
    db = multi_section["db"]
    context = multi_section["context"]
    from app.models import TimeSlot

    # Keep only 3 teachable periods/day across 4 days = 12.
    for s in db.query(TimeSlot).all():
        s.is_lunch = not (s.day_index < 4 and s.period_index < 3)
    db.commit()

    assert len([s for s in load(db, academic_context_id=context.id).slots if not s.is_lunch]) == 12

    result, run = generate(db, academic_context_id=context.id, soft=False, max_seconds=20)
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.message
    assert check_all(db, run.id) == []

    rows = db.query(Assignment).filter(Assignment.run_id == run.id).all()
    assert len(rows) == 48, "4 sections x 12 periods"

    # Every section fills all 12 slots exactly once.
    for section in ("CSE-3A", "CSE-3B", "CSE-3C", "CSE-3D"):
        slots = [a.timeslot_id for a in rows if a.section.section_number == section]
        assert len(slots) == 12 and len(set(slots)) == 12, section

    # The sole teacher occupies all 12 slots, each in a different section.
    solo = [a for a in rows if a.faculty.faculty_code == "SOLO"]
    assert len(solo) == 12
    assert len({a.timeslot_id for a in solo}) == 12, "sole teacher clashed"
    assert len({a.section.section_number for a in solo}) == 4


def test_solver_metadata_recorded_for_full_run(multi_section):
    run, _ = solve_all(multi_section)
    assert run.status in {"OPTIMAL", "FEASIBLE"}
    assert run.solve_time_seconds >= 0


# --------------------------------------------------------------- persistence


def test_faculty_and_room_persist_across_sections_on_regeneration(multi_section):
    """The whole point of SectionSubjectAssignment: once a multi-section run
    has decided who teaches what where, regenerating must reproduce it
    exactly - not just usually, every single pair."""
    db = multi_section["db"]
    context = multi_section["context"]

    run1, rows1 = solve_all(multi_section)
    placements1 = {
        (a.section_id, a.subject_id): (a.faculty_id, a.room_id) for a in rows1
    }

    run2, rows2 = solve_all(multi_section)
    placements2 = {
        (a.section_id, a.subject_id): (a.faculty_id, a.room_id) for a in rows2
    }

    assert placements1 == placements2
    assert run2.version == run1.version + 1
    assert run2.academic_context_id == context.id
