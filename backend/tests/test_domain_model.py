"""The domain model a real syllabus needs.

Three gaps closed here, each of which made real data inexpressible:

* A subject with both a lecture and a practical component. CAP460 is two
  lectures and four practical periods a week, in different kinds of room. It
  could previously only be modelled as two subjects with two invented codes,
  so the codes in a timetable stopped matching the codes in the syllabus.
* Room numbers that repeat across buildings. 36-101 and 37-101 are both "101".
* Whether a room can host a software practical (power, students' own machines)
  as opposed to needing a specialist lab.

Plus section groups, which are recorded and deliberately not yet scheduled.
"""
from __future__ import annotations

import pytest

from app import domain
from app.models import Room, Section, SectionGroup, Subject


# ------------------------------------------------------------ session specs


def _subject(**kw):
    base = dict(
        name="X", code="X101", type="theory",
        session_length_hours=1, sessions_per_week=1, is_active=True,
    )
    base.update(kw)
    return Subject(**base)


def test_a_theory_subject_yields_one_lecture_component():
    subject = _subject(type="theory", sessions_per_week=3, session_length_hours=1)
    specs = domain.session_specs(subject)
    assert len(specs) == 1
    assert specs[0].session_type == domain.LECTURE
    assert (specs[0].count, specs[0].length) == (3, 1)


def test_a_practical_subject_yields_one_practical_component():
    subject = _subject(type="practical", sessions_per_week=1, session_length_hours=2)
    specs = domain.session_specs(subject)
    assert len(specs) == 1
    assert specs[0].session_type == domain.PRACTICAL
    assert (specs[0].count, specs[0].length) == (1, 2)


def test_a_pure_subject_is_unchanged_by_the_new_model():
    """The regression anchor. Existing data must produce exactly the sessions
    it always produced, or every timetable already generated becomes suspect."""
    subject = _subject(type="theory", sessions_per_week=4, session_length_hours=2)
    assert domain.periods_per_week(subject) == 8 == subject.periods_per_week
    assert len(domain.session_specs(subject)) == 1


def test_a_mixed_subject_yields_both_components_independently():
    """CAP460 as the syllabus actually describes it."""
    subject = _subject(
        code="CAP460", name="Fundamentals of Python", type="mixed",
        lecture_sessions_per_week=2, lecture_session_length=1,
        practical_sessions_per_week=2, practical_session_length=2,
    )
    specs = domain.session_specs(subject)
    assert [s.session_type for s in specs] == [domain.LECTURE, domain.PRACTICAL]
    assert [(s.count, s.length) for s in specs] == [(2, 1), (2, 2)]
    assert subject.periods_per_week == 2 * 1 + 2 * 2 == 6


def test_lectures_want_classrooms_and_practicals_want_labs():
    subject = _subject(type="mixed",
                       lecture_sessions_per_week=2, lecture_session_length=1,
                       practical_sessions_per_week=1, practical_session_length=2)
    assert domain.room_kind_for(subject, domain.LECTURE) == "theory"
    assert domain.room_kind_for(subject, domain.PRACTICAL) == "lab"


# --------------------------------------------------- constraints at the edge


def test_a_mixed_subject_must_declare_both_components(db_session):
    """A row claiming to be mixed with no component loads would leave the
    solver nothing to schedule, so the database refuses it."""
    from sqlalchemy.exc import IntegrityError

    db_session.add(_subject(code="BAD1", type="mixed"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_a_component_cannot_be_longer_than_three_periods(db_session):
    from sqlalchemy.exc import IntegrityError

    db_session.add(_subject(
        code="BAD2", type="mixed",
        lecture_sessions_per_week=1, lecture_session_length=1,
        practical_sessions_per_week=1, practical_session_length=4,
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ------------------------------------------------------------------- rooms


def test_two_blocks_can_have_the_same_room_number(db_session):
    """The constraint that made a multi-building workbook unimportable."""
    db_session.add_all([
        Room(room_number="101", block="36", capacity=70, room_type="theory",
             room_code="36-101", is_active=True),
        Room(room_number="101", block="37", capacity=70, room_type="theory",
             room_code="37-101", is_active=True),
    ])
    db_session.commit()
    assert db_session.query(Room).count() == 2


def test_the_same_number_twice_in_one_block_is_still_refused(db_session):
    from sqlalchemy.exc import IntegrityError

    db_session.add_all([
        Room(room_number="101", block="36", capacity=70, room_type="theory",
             room_code="36-101", is_active=True),
        Room(room_number="101", block="36", capacity=70, room_type="theory",
             room_code="36-101-b", is_active=True),
    ])
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_room_code_is_unique_across_the_institution(db_session):
    from sqlalchemy.exc import IntegrityError

    db_session.add_all([
        Room(room_number="101", block="36", capacity=70, room_type="theory",
             room_code="SHARED", is_active=True),
        Room(room_number="202", block="37", capacity=70, room_type="theory",
             room_code="SHARED", is_active=True),
    ])
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_room_capabilities_default_to_absent(db_session):
    """Defaults must not silently grant a capability - an existing room with no
    stated BYOD support has not acquired one."""
    room = Room(room_number="101", block="36", capacity=70,
                room_type="theory", is_active=True)
    db_session.add(room)
    db_session.commit()
    assert room.byod is False
    assert room.charging is False
    assert room.charging_sockets is None


# ------------------------------------------------------------------ groups


def test_a_section_records_its_lab_groups(db_session, context):
    section = Section(academic_context_id=context.id, section_number="D2402",
                      strength=68, batch="BCA-2024", is_active=True)
    db_session.add(section)
    db_session.commit()
    db_session.add_all([
        SectionGroup(section_id=section.id, group_code="G1", strength=34),
        SectionGroup(section_id=section.id, group_code="G2", strength=34),
    ])
    db_session.commit()
    db_session.refresh(section)

    assert {g.group_code for g in section.groups} == {"G1", "G2"}
    assert sum(g.strength for g in section.groups) == section.strength


def test_a_group_code_is_unique_within_its_section(db_session, context):
    from sqlalchemy.exc import IntegrityError

    section = Section(academic_context_id=context.id, section_number="D2403",
                      strength=60, is_active=True)
    db_session.add(section)
    db_session.commit()
    db_session.add_all([
        SectionGroup(section_id=section.id, group_code="G1", strength=30),
        SectionGroup(section_id=section.id, group_code="G1", strength=30),
    ])
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_groups_are_recorded_but_do_not_yet_change_scheduling(db_session, context):
    """The honest position, pinned.

    A section with two groups of 34 still schedules as one unit of 68, so a
    practical still needs a room seating all of it. When the solver learns to
    split them this test changes - deliberately, and visibly.
    """
    section = Section(academic_context_id=context.id, section_number="D2404",
                      strength=68, is_active=True)
    db_session.add(section)
    db_session.commit()
    db_session.add(SectionGroup(section_id=section.id, group_code="G1", strength=34))
    db_session.commit()
    db_session.refresh(section)

    assert section.strength == 68, (
        "the scheduling strength is still the whole section - groups are data"
    )


# ------------------------------------------------------- generation is gated


def test_a_mixed_subject_is_now_schedulable(client):
    """The temporary guard is gone.

    Phase 3 stored mixed subjects and blocked generating from them, because
    scheduling one as though it were a single component would have been worse
    than refusing. The solver now places the two components independently, so
    the guard has served its purpose and been removed - asserted here so its
    removal is deliberate rather than accidental.
    """
    ctx = client.post("/api/academic-contexts", json={
        "academic_year": "2026-27", "semester": 5,
        "program": "BCA", "department": "CSE",
    }).json()
    subject = client.post("/api/subjects", json={
        "name": "Fundamentals of Python", "code": "CAP460", "type": "mixed",
        "lecture_sessions_per_week": 2, "lecture_session_length": 1,
        "practical_sessions_per_week": 2, "practical_session_length": 2,
        "required_lab_type": "programming",
    }).json()
    section = client.post("/api/sections", json={
        "academic_context_id": ctx["id"], "section_number": "D2401", "strength": 60,
    }).json()
    client.post(f"/api/sections/{section['id']}/subjects",
                json={"subject_id": subject["id"]})

    report = client.get(f"/api/validate?academic_context_id={ctx['id']}").json()
    assert not any(
        c["id"] == "mixed_subjects_not_schedulable_yet" for c in report["checks"]
    ), "the temporary guard should no longer exist"


# ----------------------------------------------- the room whitelist, at last


def _room(client, number, block="36", room_type="theory", **kw):
    body = {"room_number": number, "block": block, "capacity": 70,
            "room_type": room_type}
    body.update(kw)
    resp = client.post("/api/rooms", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_a_subject_can_finally_be_restricted_to_specific_rooms(client):
    """The solver has always read this whitelist as its second-priority room
    source, ahead of capability matching - but nothing could write to it. It
    was documented as supported while being unreachable."""
    subject = client.post("/api/subjects", json={
        "name": "Data Structures", "code": "CAP3101", "type": "theory",
        "sessions_per_week": 3, "session_length_hours": 1,
    }).json()
    a = _room(client, "101")
    b = _room(client, "102")
    _room(client, "103")

    resp = client.put(f"/api/subjects/{subject['id']}/allowed-rooms",
                      json={"room_ids": [a["id"], b["id"]]})
    assert resp.status_code == 200, resp.text
    assert {r["room_number"] for r in resp.json()} == {"101", "102"}

    listed = client.get(f"/api/subjects/{subject['id']}/allowed-rooms").json()
    assert {r["room_number"] for r in listed} == {"101", "102"}


def test_clearing_the_whitelist_restores_capability_matching(client):
    subject = client.post("/api/subjects", json={
        "name": "DS", "code": "CAP3102", "type": "theory",
        "sessions_per_week": 1, "session_length_hours": 1,
    }).json()
    room = _room(client, "201")
    client.put(f"/api/subjects/{subject['id']}/allowed-rooms",
               json={"room_ids": [room["id"]]})

    assert client.put(f"/api/subjects/{subject['id']}/allowed-rooms",
                      json={"room_ids": []}).json() == []


def test_a_faculty_room_cannot_be_whitelisted(client):
    """A faculty room is never teaching space. Allowing one would produce a
    subject whose only permitted room the solver refuses to use - an
    unschedulable subject with no obvious cause."""
    subject = client.post("/api/subjects", json={
        "name": "DS", "code": "CAP3103", "type": "theory",
        "sessions_per_week": 1, "session_length_hours": 1,
    }).json()
    office = _room(client, "F1", room_type="faculty")

    resp = client.put(f"/api/subjects/{subject['id']}/allowed-rooms",
                      json={"room_ids": [office["id"]]})
    assert resp.status_code == 400
    assert "faculty room" in resp.json()["detail"]


def test_whitelisting_an_unknown_room_is_a_404_not_a_silent_skip(client):
    subject = client.post("/api/subjects", json={
        "name": "DS", "code": "CAP3104", "type": "theory",
        "sessions_per_week": 1, "session_length_hours": 1,
    }).json()
    resp = client.put(f"/api/subjects/{subject['id']}/allowed-rooms",
                      json={"room_ids": [999_999]})
    assert resp.status_code == 404


def test_the_whitelist_actually_narrows_what_the_solver_may_use(client, db_session):
    """The write path is only worth having if the solver honours it, so this
    asserts against the eligibility function itself rather than the API."""
    from app.models import Room as RoomModel, Section, Subject
    from app.solver.data import _eligible_rooms

    subject = client.post("/api/subjects", json={
        "name": "DS", "code": "CAP3105", "type": "theory",
        "sessions_per_week": 1, "session_length_hours": 1,
    }).json()
    only = _room(client, "301")
    _room(client, "302")
    _room(client, "303")
    client.put(f"/api/subjects/{subject['id']}/allowed-rooms",
               json={"room_ids": [only["id"]]})

    ctx = client.post("/api/academic-contexts", json={
        "academic_year": "2026-27", "semester": 5,
        "program": "BCA", "department": "CSE",
    }).json()
    section_id = client.post("/api/sections", json={
        "academic_context_id": ctx["id"], "section_number": "D2401", "strength": 60,
    }).json()["id"]

    db_subject = db_session.get(Subject, subject["id"])
    db_section = db_session.get(Section, section_id)
    rooms = db_session.query(RoomModel).all()

    eligible = _eligible_rooms(db_subject, db_section, rooms)
    assert [r.room_number for r in eligible] == ["301"]


# ------------------------------------------- room identity is block-scoped


def test_two_buildings_may_each_have_a_room_101(client):
    """Room identity became `(block, room_number)` when rooms gained a block.

    The database has said so since that migration; the create endpoint still
    enforced `room_number` alone, so an institution where every building has a
    101 could add exactly one of them - and was told it "already exists", which
    reads as a data-entry mistake rather than a bug in the product.
    """
    from test_crud import mk_room

    first = client.post("/api/rooms", json={
        "room_number": "101", "block": "36", "capacity": 70,
        "room_type": "theory", "fixed_subject_id": None,
    })
    assert first.status_code == 201, first.text

    second = client.post("/api/rooms", json={
        "room_number": "101", "block": "37", "capacity": 70,
        "room_type": "theory", "fixed_subject_id": None,
    })
    assert second.status_code == 201, (
        f"a room 101 in a different building is a different room: {second.text}"
    )


def test_the_same_number_twice_in_one_building_is_still_refused(client):
    """The other half: block-scoping identity must not mean abandoning it."""
    client.post("/api/rooms", json={
        "room_number": "101", "block": "36", "capacity": 70,
        "room_type": "theory", "fixed_subject_id": None,
    })
    duplicate = client.post("/api/rooms", json={
        "room_number": "101", "block": "36", "capacity": 70,
        "room_type": "theory", "fixed_subject_id": None,
    })
    assert duplicate.status_code == 409
    assert "block" in duplicate.text and "room_number" in duplicate.text, (
        "the message should name both halves of the identity that clashed"
    )


def test_moving_a_room_to_a_block_that_already_has_that_number_is_refused(client):
    """An update naming only part of a composite key must be checked against
    the row's other half, not against a blank."""
    a = client.post("/api/rooms", json={
        "room_number": "101", "block": "36", "capacity": 70,
        "room_type": "theory", "fixed_subject_id": None,
    }).json()
    client.post("/api/rooms", json={
        "room_number": "101", "block": "37", "capacity": 70,
        "room_type": "theory", "fixed_subject_id": None,
    })

    moved = client.put(f"/api/rooms/{a['id']}", json={"block": "37"})
    assert moved.status_code == 409, "37-101 already exists"


def test_a_room_can_still_be_edited_without_touching_its_identity(client):
    """The regression the merge guards against: an update that changes only
    the capacity must not compare the number against a missing block and
    conclude the room clashes with itself."""
    room = client.post("/api/rooms", json={
        "room_number": "101", "block": "36", "capacity": 70,
        "room_type": "theory", "fixed_subject_id": None,
    }).json()

    edited = client.put(f"/api/rooms/{room['id']}", json={"capacity": 90})
    assert edited.status_code == 200, edited.text
    assert edited.json()["capacity"] == 90
