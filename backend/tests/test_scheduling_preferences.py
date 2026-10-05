"""The preferences: what a good timetable looks like among valid ones.

None of these is a rule. Each test shows the preference choosing well when it
has a choice - and, where it matters, that it never refuses a timetable when
it has none.

* Rooms: a section's classes in the same block, else a neighbouring one, and
  a far block only when nothing nearer fits.
* Students: a day without holes in it.
* Teachers: a free period every day is the rule; five periods in a row is
  merely avoided, and allowed when there is no other way.
"""
from __future__ import annotations

from app.models import FacultyUnavailability, TimeSlot
from app.solver.data import load
from app.solver.solve import solve
from tests.test_business_rules import _faculty, _grid, _room, _section, _subject


def _solved(db, context, seconds=30):
    inp = load(db, academic_context_id=context.id)
    result = solve(inp, max_seconds=seconds, soft=True)
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.message
    return inp, result


def _blocks(inp, result) -> dict[str, str]:
    """subject code -> the block its room is in."""
    return {
        p.pair.subject_code: str(inp.rooms[p.room_id].block) for p in result.placements
    }


def _periods(inp, result, **match) -> list[int]:
    """Period indices used by the placements whose pair matches `match`."""
    slot = {s.index: s for s in inp.slots}
    out = []
    for p in result.placements:
        if all(getattr(p.pair, k) == v for k, v in match.items()):
            out.extend(slot[i].period_index for i in p.slot_indices)
    return sorted(out)


# ------------------------------------------------------------------ rooms


def test_a_section_stays_in_the_block_it_already_has_to_use(db_session, context):
    """ECE181 needs charging points and only 33-101 has them, so the section is
    in block 33 anyway. ECE281 could go anywhere - and goes to block 33."""
    _grid(db_session, periods=4)
    sec = _section(db_session, context)
    a, b = _faculty(db_session, "T1"), _faculty(db_session, "T2")
    _subject(db_session, "ECE181", "theory", a, byod=True, section=sec)
    _subject(db_session, "ECE281", "theory", b, section=sec)
    _room(db_session, "101", block="33", byod=True)
    _room(db_session, "501", block="38")
    _room(db_session, "502", block="34")
    _room(db_session, "601", block="38")

    inp, result = _solved(db_session, context)
    assert _blocks(inp, result) == {"ECE181": "33", "ECE281": "33"}
    assert result.penalties["room_block_spread"] == 0


def test_a_nearby_block_is_preferred_to_a_far_one(db_session, context):
    """The section's theory is in block 33. Its lab group's lab can only be in
    block 34 or 38 - so 34, next door. Counted with the parent section, since a
    lab group is the same students."""
    _grid(db_session, periods=4)
    parent = _section(db_session, context, number="2401", strength=60)
    group = _section(db_session, context, number="24011", strength=30)
    group.parent_section_id = parent.id
    db_session.commit()

    a, b = _faculty(db_session, "T1"), _faculty(db_session, "T2")
    _subject(db_session, "ECE181", "theory", a, byod=True, section=parent)
    _subject(db_session, "ECE182", "practical", b, length=2, section=group)
    _room(db_session, "101", block="33", byod=True)
    _room(db_session, "901", block="38", kind="lab", capacity=40)
    _room(db_session, "201", block="34", kind="lab", capacity=40)

    inp, result = _solved(db_session, context)
    assert _blocks(inp, result) == {"ECE181": "33", "ECE182": "34"}


def test_a_far_block_is_used_when_it_is_the_only_room_that_fits(db_session, context):
    """The preference is never a rule: with only block 38 able to take the
    lab, the lab goes to block 38 and the timetable is produced."""
    _grid(db_session, periods=4)
    parent = _section(db_session, context, number="2401", strength=60)
    group = _section(db_session, context, number="24011", strength=30)
    group.parent_section_id = parent.id
    db_session.commit()

    a, b = _faculty(db_session, "T1"), _faculty(db_session, "T2")
    _subject(db_session, "ECE181", "theory", a, byod=True, section=parent)
    _subject(db_session, "ECE182", "practical", b, length=2, section=group)
    _room(db_session, "101", block="33", byod=True)
    _room(db_session, "901", block="38", kind="lab", capacity=40)

    inp, result = _solved(db_session, context)
    assert _blocks(inp, result) == {"ECE181": "33", "ECE182": "38"}
    # Noted, not refused: one extra block, five blocks apart.
    assert result.penalties["room_block_spread"] == 1 + 5


def test_blocks_that_are_not_numbers_are_only_same_or_different(db_session, context):
    """"Main" and "Annex" say nothing about which is near which, so there is no
    distance to minimise - only whether a second block is used at all."""
    _grid(db_session, periods=4)
    sec = _section(db_session, context)
    a, b = _faculty(db_session, "T1"), _faculty(db_session, "T2")
    _subject(db_session, "ECE181", "theory", a, byod=True, section=sec)
    _subject(db_session, "ECE281", "theory", b, section=sec)
    _room(db_session, "101", block="Main", byod=True)
    _room(db_session, "102", block="Annex")

    inp, result = _solved(db_session, context)
    # Main-101 can take both (at different times), so one block is used.
    assert _blocks(inp, result) == {"ECE181": "Main", "ECE281": "Main"}
    assert result.penalties["room_block_spread"] == 0


# --------------------------------------------------------------- students


def test_a_sections_day_has_no_holes_when_it_can_avoid_them(db_session, context):
    """Four classes in a seven-period day: P1-P4 together, not P1, P3, P5, P7."""
    _grid(db_session, periods=7)
    sec = _section(db_session, context)
    for i, code in enumerate(("MTH101", "ECE201", "HSS110", "CSE210")):
        _subject(db_session, code, "theory", _faculty(db_session, f"T{i}"), section=sec)
    _room(db_session, "101", block="33")

    inp, result = _solved(db_session, context)
    taught = _periods(inp, result)
    assert len(taught) == 4
    assert taught[-1] - taught[0] + 1 == 4, f"holes in the day: {taught}"
    assert result.penalties["gaps"] == 0


def test_only_holes_inside_the_day_count_not_a_late_start(db_session, context):
    """The first two periods are unavailable to the section's only teacher, so
    the day starts at P3. That is not a gap: the measure is the time between
    the first class and the last, less the classes taught."""
    _grid(db_session, periods=6)
    sec = _section(db_session, context)
    t = _faculty(db_session, "T1")
    for code in ("MTH101", "ECE201", "HSS110"):
        _subject(db_session, code, "theory", t, section=sec)
    _room(db_session, "101", block="33")
    for slot in db_session.query(TimeSlot).filter(TimeSlot.period_index < 2):
        db_session.add(FacultyUnavailability(faculty_id=t.id, timeslot_id=slot.id))
    db_session.commit()

    inp, result = _solved(db_session, context)
    taught = _periods(inp, result)
    assert taught[0] >= 2
    assert taught[-1] - taught[0] + 1 == len(taught)
    assert result.penalties["gaps"] == 0


# --------------------------------------------------------------- teachers


def test_five_periods_in_a_row_are_allowed_when_there_is_no_other_way(db_session, context):
    """A teacher with five classes and only P1-P5 available has to teach five in
    a row. That leaves P6 and P7 free, so the break rule holds - and the
    preference against long runs notes it rather than refusing the timetable."""
    _grid(db_session, periods=7)
    t = _faculty(db_session, "T1")
    subject = None
    for i in range(5):
        sec = _section(db_session, context, number=f"24{i:02d}", strength=40)
        if subject is None:
            subject = _subject(db_session, "ECE181", "theory", t, section=sec)
        else:
            sec.subjects.append(subject)
            db_session.commit()
    _room(db_session, "101", block="33")
    for slot in db_session.query(TimeSlot).filter(TimeSlot.period_index >= 5):
        db_session.add(FacultyUnavailability(faculty_id=t.id, timeslot_id=slot.id))
    db_session.commit()

    inp, result = _solved(db_session, context)
    assert _periods(inp, result) == [0, 1, 2, 3, 4]
    assert result.penalties["long_faculty_runs"] > 0
