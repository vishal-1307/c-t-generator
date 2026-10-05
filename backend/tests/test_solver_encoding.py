"""Hard-constraint guarantees re-derived from the solver's own output.

These exist because the room/faculty clash encoding is a tempting thing to
"optimise": it is by far the largest part of the model (on the largest demo
context, the reified room literals are ~82% of all variables), so any attempt
to make generation fit a smaller instance starts here.

A cheaper encoding that quietly dropped a case would look exactly like a
success - smaller model, less memory, faster solve. So the properties below
are asserted from the extracted placements, independently of how the model
was written down, and are the gate any future encoding change has to pass.

An encoding change was measured and rejected while these were written: naming
the per-(pair, slot) occupancy literal once instead of re-materialising
``sum(occupancy lits)`` per candidate resource cut peak memory ~28% but made
the largest context 2.6x slower to solve (48.9s -> 128.9s median over three
seeds), which is the wrong trade against a fixed time budget. See
SOLVER_SCALING_INVESTIGATION.md.
"""
from __future__ import annotations

import pytest

from app.models import Faculty, Room, Section, Subject
from app.solver.data import load
from app.solver.model import build
from app.solver.run import solve


@pytest.fixture
def dataset(db_session, context):
    """Two sections contending for one lab and a shared teaching pool, so the
    room clash and the faculty clash both actually bind."""
    from app.grid import build_grid

    db = db_session
    subjects = {}
    for name, code, type_, length, spw, lab in [
        ("Data Structures", "CS201", "theory", 1, 3, None),
        ("Networks", "CS202", "theory", 1, 2, None),
        ("DS Lab", "CS251", "practical", 2, 1, "COMPUTING"),
    ]:
        s = Subject(name=name, code=code, type=type_, session_length_hours=length,
                    sessions_per_week=spw, required_lab_type=lab)
        db.add(s)
        subjects[code] = s
    db.flush()

    for number, rtype, lab, cap in [
        ("A101", "theory", None, 70), ("A102", "theory", None, 70),
        ("LAB1", "lab", "COMPUTING", 70),
    ]:
        db.add(Room(room_number=number, block="A", floor="1", capacity=cap,
                    room_type=rtype, lab_type=lab))
    db.flush()

    for fname, fcode, teaches in [
        ("Dr. A", "FAC01", ["CS201", "CS202"]),
        ("Dr. B", "FAC02", ["CS201", "CS202"]),
        ("Dr. L", "FAC03", ["CS251"]),
    ]:
        f = Faculty(name=fname, faculty_code=fcode)
        f.subjects = [subjects[c] for c in teaches]
        db.add(f)
    db.flush()

    for number in ("S-A", "S-B"):
        sec = Section(academic_context_id=context.id, section_number=number,
                      strength=60)
        sec.subjects = list(subjects.values())
        db.add(sec)

    slots = build_grid()
    for s in slots:
        if s.period_index == 4:
            s.is_lunch = True
    db.add_all(slots)
    db.commit()
    return db


def test_every_clash_literal_still_exists(dataset, context):
    """The objective reads ``f_occ`` and the diagnostics read ``room_occ``, so
    an encoding that stops materialising either breaks callers, not just
    performance."""
    inp = load(dataset, context.id)
    built = build(inp)
    assert built.room_occ, "room clash must be reified per (pair, room, slot)"
    assert built.f_occ, "faculty clash must be reified per (pair, faculty, slot)"
    for (pi, r, _slot) in built.room_occ:
        assert r in built.inp.pairs[pi].room_ids, "room literal outside the pair's pool"
    for (pi, f, _slot) in built.f_occ:
        assert f in built.inp.pairs[pi].faculty_ids, "faculty literal outside the pool"


def test_no_resource_is_double_booked_in_the_solved_timetable(dataset, context):
    """HC1/HC2/HC3 and HC16, re-derived from the placements themselves."""
    inp = load(dataset, context.id)
    result = solve(inp, soft=False, max_seconds=30)
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.message

    expected = sum(p.sessions * p.length for p in inp.pairs)
    placed = sum(len(p.slot_indices) for p in result.placements)
    assert placed == expected, "every required period must be placed"

    faculty_at: dict[tuple[int, int], str] = {}
    room_at: dict[tuple[int, int], str] = {}
    section_at: dict[tuple[int, int], str] = {}
    for p in result.placements:
        label = f"{p.pair.section_number}/{p.pair.subject_code}"
        assert p.room_id in p.pair.room_ids, f"{label}: ineligible room"
        assert p.faculty_id in p.pair.faculty_ids, f"{label}: ineligible faculty"
        for slot_index in p.slot_indices:
            assert not inp.slot_by_index(slot_index).is_lunch, f"{label}: lunch slot"

            key = (p.faculty_id, slot_index)
            assert key not in faculty_at, (
                f"faculty double-booked at slot {slot_index}: "
                f"{faculty_at.get(key)} and {label}"
            )
            faculty_at[key] = label

            key = (p.room_id, slot_index)
            assert key not in room_at, (
                f"room double-booked at slot {slot_index}: "
                f"{room_at.get(key)} and {label}"
            )
            room_at[key] = label

            key = (p.pair.section_id, slot_index)
            assert key not in section_at, (
                f"section double-booked at slot {slot_index}: "
                f"{section_at.get(key)} and {label}"
            )
            section_at[key] = label


def test_one_room_and_one_faculty_per_pair(dataset, context):
    """HC14/HC15: whatever the encoding, a pair keeps one room and one teacher
    for its whole week."""
    inp = load(dataset, context.id)
    result = solve(inp, soft=False, max_seconds=30)
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.message

    rooms_used: dict[tuple[int, int, str], set[int]] = {}
    faculty_used: dict[tuple[int, int, str], set[int]] = {}
    for p in result.placements:
        rooms_used.setdefault(p.pair.key, set()).add(p.room_id)
        faculty_used.setdefault(p.pair.key, set()).add(p.faculty_id)

    for key, rooms in rooms_used.items():
        assert len(rooms) == 1, f"{key} used {len(rooms)} rooms across its week"
    for key, faculty in faculty_used.items():
        assert len(faculty) == 1, f"{key} used {len(faculty)} teachers across its week"
