"""BYOD on a lecture row: the room must have it.

The department confirmed what BYOD means - charging points under the benches,
so students can power their own devices. That is a property of the room, and
it matters to a lecture as much as to a lab, so a lecture marked BYOD goes only
to a room that has them. That is the default.

It is still a setting, because it used to be off (BYOD was first read as a lab
requirement only), and an institution whose BYOD mark means something weaker
can switch it back. Both positions are pinned here, because the value of a
switch is that both of its positions work.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.grid import build_grid
from app.models import Faculty, Room, Section, Subject
from app.solver.data import _eligible_rooms


@pytest.fixture
def rooms(db_session):
    made = [
        Room(room_number="301", block="36", room_code="36-301", capacity=72,
             room_type="theory", is_active=True, byod=True, charging=True),
        Room(room_number="302", block="36", room_code="36-302", capacity=72,
             room_type="theory", is_active=True, byod=False, charging=False),
    ]
    db_session.add_all(made)
    db_session.commit()
    return {r.room_code: r for r in made}


@pytest.fixture
def lecture(db_session, context):
    subject = Subject(
        name="Embedded systems", code="ECE181", type="theory",
        sessions_per_week=4, session_length_hours=1, byod_required=True,
    )
    section = Section(academic_context_id=context.id, section_number="2401",
                      strength=68, is_active=True)
    db_session.add_all([subject, section])
    db_session.commit()
    return subject, section


@pytest.fixture(autouse=True)
def restore_setting():
    before = settings.enforce_byod_on_lectures
    yield
    settings.enforce_byod_on_lectures = before


def test_by_default_a_byod_lecture_needs_a_room_with_charging_points(
    db_session, rooms, lecture
):
    """The confirmed rule, and so the default."""
    from app.config import Settings

    assert Settings().enforce_byod_on_lectures is True
    subject, section = lecture

    eligible = _eligible_rooms(subject, section, {r.id: r for r in rooms.values()}, "L")

    assert {r.room_code for r in eligible} == {"36-301"}


def test_switched_off_a_lecture_ignores_byod(db_session, rooms, lecture):
    """The old reading, kept available for an institution that wants it."""
    settings.enforce_byod_on_lectures = False
    subject, section = lecture

    eligible = _eligible_rooms(subject, section, {r.id: r for r in rooms.values()}, "L")

    assert {r.room_code for r in eligible} == {"36-301", "36-302"}


def test_switched_on_a_lecture_needs_a_byod_room(db_session, rooms, lecture):
    settings.enforce_byod_on_lectures = True
    subject, section = lecture

    eligible = _eligible_rooms(subject, section, {r.id: r for r in rooms.values()}, "L")

    assert {r.room_code for r in eligible} == {"36-301"}


def test_a_practical_always_needs_one_either_way(db_session, context, rooms):
    """The switch is about lectures. A practical's capability filter is not in
    question and must not move with it."""
    lab_byod = Room(room_number="101", block="33", room_code="33-101", capacity=40,
                    room_type="lab", is_active=True, byod=True, charging=True)
    lab_plain = Room(room_number="102", block="33", room_code="33-102", capacity=40,
                     room_type="lab", is_active=True, byod=False, charging=False)
    db_session.add_all([lab_byod, lab_plain])
    subject = Subject(name="Embedded systems lab", code="ECE182", type="practical",
                      sessions_per_week=4, session_length_hours=2, byod_required=True)
    section = Section(academic_context_id=context.id, section_number="24011",
                      strength=36, is_active=True)
    db_session.add_all([subject, section])
    db_session.commit()
    pool = {r.id: r for r in (lab_byod, lab_plain)}

    for value in (False, True):
        settings.enforce_byod_on_lectures = value
        eligible = _eligible_rooms(subject, section, pool, "P")
        assert {r.room_code for r in eligible} == {"33-101"}, (
            f"a practical's BYOD requirement changed with the lecture switch "
            f"set to {value}"
        )


def test_a_subject_that_does_not_ask_for_byod_is_unaffected(db_session, rooms, context):
    """Switching it on must narrow only the subjects that actually asked."""
    settings.enforce_byod_on_lectures = True
    subject = Subject(name="Micro controllers", code="ECE322", type="theory",
                      sessions_per_week=4, session_length_hours=1,
                      byod_required=False)
    section = Section(academic_context_id=context.id, section_number="2503",
                      strength=72, is_active=True)
    db_session.add_all([subject, section])
    db_session.commit()

    eligible = _eligible_rooms(subject, section, {r.id: r for r in rooms.values()}, "L")

    assert {r.room_code for r in eligible} == {"36-301", "36-302"}


def test_the_departments_data_stays_feasible_if_it_is_switched_on(db_session, context):
    """The measurement behind saying this is safe to enable.

    The two BYOD lectures in the real file are 2401 (68 students) and 2403
    (71). Both need a BYOD classroom that seats them, and Block 36 has six -
    so turning this on narrows the pool without emptying it. If a future room
    list made that untrue, this fails rather than a generation quietly
    becoming infeasible.
    """
    settings.enforce_byod_on_lectures = True
    block36 = [
        Room(room_number=str(n), block="36", room_code=f"36-{n}", capacity=cap,
             room_type="theory", is_active=True, byod=byod, charging=False)
        for n, cap, byod in [
            (301, 72, True), (302, 60, False), (303, 72, True), (304, 60, False),
            (305, 72, True), (306, 72, False), (307, 60, True), (308, 72, False),
            (309, 120, True), (406, 72, True), (408, 72, True),
        ]
    ]
    db_session.add_all(block36)
    subject = Subject(name="Communication Systems", code="ECE101", type="theory",
                      sessions_per_week=4, session_length_hours=1, byod_required=True)
    section = Section(academic_context_id=context.id, section_number="2403",
                      strength=71, is_active=True)
    db_session.add_all([subject, section])
    db_session.commit()

    eligible = _eligible_rooms(
        subject, section, {r.id: r for r in block36}, "L"
    )
    assert len(eligible) == 6, [r.room_code for r in eligible]
    assert all(r.byod and r.capacity >= 71 for r in eligible)
