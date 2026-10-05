"""CP-SAT hard constraints, verified on a single section.

These tests assert properties of the *generated timetable*, not that the solver
returned something. Every one of them would fail on an empty or wrong result.
Verification goes through ``constraint_checker``, which re-derives every hard
constraint from the persisted rows without importing the solver.
"""
from __future__ import annotations

from collections import defaultdict

import pytest

from app.grid import build_grid
from app.models import Assignment, Faculty, Room, Section, Subject, TimeSlot
from app.solver.data import load
from app.solver.model import UnschedulablePair, build
from app.solver.run import generate

from constraint_checker import check_all, one_faculty_per_pair


@pytest.fixture
def dataset(db_session, context):
    """One section, seven subjects (two of them 2-hour practicals), a
    Monday-Friday grid of 9 x 50min periods with P5 marked as lunch, plus one
    faculty room that must never be used."""
    db = db_session

    subjects = {}
    for name, code, type_, length, spw, lab_type in [
        ("Data Structures", "CS201", "theory", 1, 4, None),
        ("Operating Systems", "CS202", "theory", 1, 4, None),
        ("Database Systems", "CS203", "theory", 1, 3, None),
        ("Computer Networks", "CS204", "theory", 1, 3, None),
        ("Engineering Mathematics", "MA201", "theory", 1, 4, None),
        ("Data Structures Lab", "CS251", "practical", 2, 1, "COMPUTING"),
        ("Database Lab", "CS253", "practical", 2, 1, "COMPUTING"),
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
        ("LAB1", 70, "lab", "COMPUTING"),
        ("LAB3", 70, "lab", "COMPUTING"),
        ("F-201", 5, "faculty", None),  # must never be used
    ]:
        r = Room(
            room_number=number, block="A", capacity=cap, room_type=room_type,
            lab_type=lab_type,
        )
        db.add(r)
        rooms[number] = r
    db.flush()

    for fname, fcode, teaches in [
        ("Dr. A. Sharma", "FAC01", ["CS201", "CS251"]),
        ("Dr. B. Iyer", "FAC02", ["CS202"]),
        ("Dr. C. Nair", "FAC03", ["CS203", "CS253"]),
        ("Dr. D. Rao", "FAC04", ["CS204"]),
        ("Dr. E. Menon", "FAC05", ["MA201"]),
    ]:
        f = Faculty(name=fname, faculty_code=fcode)
        f.subjects = [subjects[c] for c in teaches]
        db.add(f)

    section = Section(
        academic_context_id=context.id, section_number="CSE-3A", strength=62
    )
    section.subjects = list(subjects.values())
    db.add(section)

    slots = build_grid()
    for s in slots:
        if s.period_index == 4:      # P5, 12:50-13:40
            s.is_lunch = True
    db.add_all(slots)
    db.commit()

    return {
        "db": db, "context": context, "section": section,
        "subjects": subjects, "rooms": rooms,
    }


def _generate(dataset, **kwargs):
    """Hard-constraint verification does not need the objective - soft=False
    by default keeps these tests fast; the objective itself is covered in
    test_soft_constraints.py. Pass soft=True explicitly to override."""
    kwargs.setdefault("soft", False)
    kwargs.setdefault("max_seconds", 15)
    return generate(
        dataset["db"],
        academic_context_id=dataset["context"].id,
        section_ids=[dataset["section"].id],
        **kwargs,
    )


# ------------------------------------------------------------------ feasibility


def test_solver_finds_a_solution(dataset):
    result, run = _generate(dataset)
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.message
    assert run is not None
    assert result.placements, "solver returned success but placed nothing"


def test_no_hard_constraint_is_violated(dataset):
    """The headline assertion: an independent re-derivation of every hard
    constraint over the persisted rows finds nothing wrong."""
    db = dataset["db"]
    _, run = _generate(dataset)
    violations = check_all(db, run.id)
    assert violations == [], "\n".join(violations)


def test_every_subject_gets_exactly_its_weekly_demand(dataset):
    """Guards against a 'valid' but empty or partial timetable, which would
    satisfy every clash constraint trivially."""
    db = dataset["db"]
    _, run = _generate(dataset)
    rows = db.query(Assignment).filter(Assignment.run_id == run.id).all()

    got = defaultdict(int)
    for a in rows:
        got[a.subject.code] += 1

    expected = {
        s.code: s.session_length_hours * s.sessions_per_week
        for s in dataset["subjects"].values()
    }
    assert got == expected
    assert len(rows) == sum(expected.values()) == 22


# --------------------------------------------------------- individual hard rules


def test_hc1_no_faculty_is_double_booked(dataset):
    db = dataset["db"]
    _, run = _generate(dataset)
    rows = db.query(Assignment).filter(Assignment.run_id == run.id).all()
    seen = defaultdict(list)
    for a in rows:
        seen[(a.faculty_id, a.timeslot_id)].append(a.subject.code)
    assert {k: v for k, v in seen.items() if len(v) > 1} == {}


def test_hc2_no_room_is_double_booked(dataset):
    db = dataset["db"]
    _, run = _generate(dataset)
    rows = db.query(Assignment).filter(Assignment.run_id == run.id).all()
    seen = defaultdict(list)
    for a in rows:
        seen[(a.room_id, a.timeslot_id)].append(a.subject.code)
    assert {k: v for k, v in seen.items() if len(v) > 1} == {}


def test_hc3_section_has_at_most_one_class_per_slot(dataset):
    db = dataset["db"]
    _, run = _generate(dataset)
    rows = db.query(Assignment).filter(Assignment.run_id == run.id).all()
    seen = defaultdict(list)
    for a in rows:
        seen[a.timeslot_id].append(a.subject.code)
    assert {k: v for k, v in seen.items() if len(v) > 1} == {}


def test_hc4_every_room_seats_the_section(dataset):
    db = dataset["db"]
    _, run = _generate(dataset)
    strength = dataset["section"].strength
    for a in db.query(Assignment).filter(Assignment.run_id == run.id).all():
        assert a.room.capacity >= strength, f"{a.room.room_number} seats {a.room.capacity}"


def test_practicals_only_in_lab_rooms_of_the_right_type(dataset):
    db = dataset["db"]
    _, run = _generate(dataset)
    rows = db.query(Assignment).filter(Assignment.run_id == run.id).all()
    practicals = [a for a in rows if a.subject.type == "practical"]
    assert practicals, "no practicals in the result"
    for a in practicals:
        assert a.room.room_type == "lab"
        assert a.room.lab_type == "COMPUTING"


def test_theory_only_in_theory_rooms(dataset):
    db = dataset["db"]
    _, run = _generate(dataset)
    theory = [
        a
        for a in db.query(Assignment).filter(Assignment.run_id == run.id).all()
        if a.subject.type == "theory"
    ]
    assert theory
    assert all(a.room.room_type == "theory" for a in theory)


def test_faculty_room_is_never_used(dataset):
    """The faculty room (F-201) must never be offered, let alone chosen."""
    db = dataset["db"]
    _, run = _generate(dataset)
    rows = db.query(Assignment).filter(Assignment.run_id == run.id).all()
    assert all(a.room.room_number != "F-201" for a in rows)
    assert all(a.room.room_type != "faculty" for a in rows)


def test_hc7_nothing_is_scheduled_during_lunch(dataset):
    db = dataset["db"]
    _, run = _generate(dataset)
    lunch_ids = {
        s.id for s in db.query(TimeSlot).filter(TimeSlot.is_lunch.is_(True)).all()
    }
    assert len(lunch_ids) == 5, "expected one lunch slot per day, 5 working days"
    used = {
        a.timeslot_id
        for a in db.query(Assignment).filter(Assignment.run_id == run.id).all()
    }
    assert used & lunch_ids == set()


def test_hc8_faculty_only_teach_subjects_they_are_mapped_to(dataset):
    db = dataset["db"]
    _, run = _generate(dataset)
    for a in db.query(Assignment).filter(Assignment.run_id == run.id).all():
        allowed = {f.id for f in a.subject.faculties}
        assert a.faculty_id in allowed, f"{a.faculty.name} not mapped to {a.subject.code}"


def test_hc9_multi_hour_blocks_are_contiguous(dataset):
    """A 2-hour block must be two consecutive periods, same day, same room,
    same faculty - and must not straddle the lunch slot."""
    db = dataset["db"]
    _, run = _generate(dataset)
    rows = db.query(Assignment).filter(Assignment.run_id == run.id).all()

    blocks = defaultdict(list)
    for a in rows:
        blocks[a.block_id].append(a)

    multi = [g for g in blocks.values() if g[0].subject.session_length_hours > 1]
    assert len(multi) == 2, "expected one block each for CS251 and CS253"

    for group in multi:
        group.sort(key=lambda a: a.timeslot.period_index)
        subject = group[0].subject
        assert len(group) == subject.session_length_hours
        assert len({a.timeslot.day_index for a in group}) == 1, "spans days"
        periods = [a.timeslot.period_index for a in group]
        assert periods == list(range(periods[0], periods[0] + len(periods))), periods
        assert len({a.room_id for a in group}) == 1, "changes room mid-block"
        assert len({a.faculty_id for a in group}) == 1, "changes faculty mid-block"
        assert not any(a.timeslot.is_lunch for a in group)


def test_one_faculty_teaches_all_sessions_of_a_pair(dataset):
    """Agreed during planning: a section sees one teacher per subject."""
    db = dataset["db"]
    _, run = _generate(dataset)
    assert one_faculty_per_pair(db, run.id) == []

    rows = db.query(Assignment).filter(Assignment.run_id == run.id).all()
    by_subject = defaultdict(set)
    for a in rows:
        by_subject[a.subject.code].add(a.faculty.name)
    # CS201 has 4 sessions; all four must share one teacher.
    assert len(by_subject["CS201"]) == 1


def test_one_room_used_for_all_sessions_of_a_pair(dataset):
    """HC15: every session of a pair uses the same room for the week, not
    just within one block."""
    db = dataset["db"]
    _, run = _generate(dataset)
    rows = db.query(Assignment).filter(Assignment.run_id == run.id).all()
    by_subject = defaultdict(set)
    for a in rows:
        by_subject[a.subject.code].add(a.room.room_number)
    assert len(by_subject["CS201"]) == 1, by_subject["CS201"]


# ------------------------------------------------------- structural guarantees


def test_lunch_and_contiguity_are_structural_not_incidental(dataset):
    """HC7 (no lunch) and HC9 (contiguity) are enforced by the variable domain.
    Prove the domain itself excludes every illegal window, so no solution
    could violate them."""
    inp = load(dataset["db"], academic_context_id=dataset["context"].id,
               section_ids=[dataset["section"].id])
    from app.solver.data import _legal_starts

    for length in (1, 2, 3):
        starts = _legal_starts(inp.slots, length)
        for start_index, covered in starts.items():
            block = [inp.slots[i] for i in covered]
            assert len(block) == length
            assert not any(s.is_lunch for s in block), "lunch inside a legal window"
            assert len({s.day_index for s in block}) == 1, "window spans days"
            periods = [s.period_index for s in block]
            assert periods == list(range(periods[0], periods[0] + length))

    # 9 periods/day with P5 lunch -> runs of 4 and 4.
    assert len(_legal_starts(inp.slots, 1)) == 40   # 5 days x 8 teachable
    assert len(_legal_starts(inp.slots, 2)) == 30   # 5 days x (3 + 3)
    assert len(_legal_starts(inp.slots, 3)) == 20   # 5 days x (2 + 2)


def test_room_domain_is_prefiltered_by_type_and_capability(dataset):
    """HC4/type/capability are enforced by never offering an ineligible room -
    the faculty room and mismatched-type rooms never even appear."""
    inp = load(dataset["db"], academic_context_id=dataset["context"].id,
               section_ids=[dataset["section"].id])
    by_code = {p.subject_code: p for p in inp.pairs}
    rooms = dataset["rooms"]

    assert set(by_code["CS201"].room_ids) == {rooms["A101"].id, rooms["A102"].id}
    assert set(by_code["CS251"].room_ids) == {rooms["LAB1"].id, rooms["LAB3"].id}
    assert rooms["F-201"].id not in by_code["CS201"].room_ids
    assert rooms["F-201"].id not in by_code["CS251"].room_ids


def test_undersized_room_is_reported_not_silently_used(dataset):
    """Shrink both labs below the section strength: the pair has no eligible
    room, and the solver says so instead of returning a bad timetable."""
    db = dataset["db"]
    dataset["rooms"]["LAB1"].capacity = 10
    dataset["rooms"]["LAB3"].capacity = 10
    db.commit()

    inp = load(db, academic_context_id=dataset["context"].id,
               section_ids=[dataset["section"].id])
    with pytest.raises(UnschedulablePair) as exc:
        build(inp)
    assert "CS251" in str(exc.value)
    assert "no eligible room" in str(exc.value)

    result, run = _generate(dataset)
    assert result.status == "INFEASIBLE"
    # The failed run is persisted with its explanation so it shows up in the
    # run history rather than vanishing, but it schedules nothing.
    assert run is not None and run.status == "INFEASIBLE"
    assert db.query(Assignment).filter(Assignment.run_id == run.id).count() == 0
    assert result.report is not None
    assert any("CS251" in b.detail for b in result.report.blockers), result.message


def test_unmapped_subject_is_reported(dataset):
    """A subject nobody can teach must name itself, not fail cryptically."""
    db = dataset["db"]
    dataset["subjects"]["CS204"].faculties = []
    db.commit()

    inp = load(db, academic_context_id=dataset["context"].id,
               section_ids=[dataset["section"].id])
    with pytest.raises(UnschedulablePair) as exc:
        build(inp)
    assert "CS204" in str(exc.value)
    assert "no faculty" in str(exc.value)


def test_three_hour_block_impossible_when_lunch_splits_the_day(dataset):
    """With P5 lunch the day splits into runs of 4 and 4, so a 5-hour block has
    nowhere to go. (3 fits; 5 does not.) Exercises the domain boundary."""
    db = dataset["db"]
    inp = load(db, academic_context_id=dataset["context"].id,
               section_ids=[dataset["section"].id])
    from app.solver.data import _legal_starts

    assert len(_legal_starts(inp.slots, 4)) == 10   # 5 days x 2 runs of exactly 4
    assert _legal_starts(inp.slots, 5) == {}        # no run of 5 survives the split


def test_persisted_rows_carry_block_ids_for_cell_merging(dataset):
    db = dataset["db"]
    _, run = _generate(dataset)
    rows = db.query(Assignment).filter(Assignment.run_id == run.id).all()

    blocks = defaultdict(list)
    for a in rows:
        blocks[a.block_id].append(a)

    # 5 theory subjects: 4+4+3+3+4 = 18 one-period blocks, plus 2 two-period blocks.
    assert len(blocks) == 20
    assert sorted(len(g) for g in blocks.values()) == [1] * 18 + [2, 2]


def test_run_records_solver_metadata(dataset):
    db = dataset["db"]
    result, run = _generate(dataset)
    assert run.status == result.status
    assert run.solve_time_seconds is not None and run.solve_time_seconds >= 0
    assert run.id is not None
    assert run.academic_context_id == dataset["context"].id
    assert run.version == 1


# ------------------------------------------------------------------ persistence


def test_faculty_and_room_persist_across_regeneration(dataset):
    """The point of the whole persistence model: identical input, regenerated,
    keeps the same faculty and room for every pair."""
    db = dataset["db"]
    _, run1 = _generate(dataset)
    rows1 = {
        (a.section_id, a.subject_id): (a.faculty_id, a.room_id)
        for a in db.query(Assignment).filter(Assignment.run_id == run1.id).all()
    }

    _, run2 = _generate(dataset)
    rows2 = {
        (a.section_id, a.subject_id): (a.faculty_id, a.room_id)
        for a in db.query(Assignment).filter(Assignment.run_id == run2.id).all()
    }

    assert rows1 == rows2
    assert run2.version == run1.version + 1
