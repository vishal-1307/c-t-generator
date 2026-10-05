"""Phase 9 PART 20: after a room import, generation must still be correct -
newly imported rooms are genuinely usable, faculty rooms stay excluded,
capacity/lab rules still hold, and persistent semester assignments survive
a regeneration untouched.
"""
from __future__ import annotations

import io

from app.grid import build_grid
from app.models import Assignment, Faculty, Room, Section, SectionSubjectAssignment, Subject
from app.solver.run import generate

from constraint_checker import check_all


def _import_rooms(client, csv_text: str):
    resp = client.post(
        "/api/bulk-import/rooms/apply",
        files={"file": ("rooms.csv", io.BytesIO(csv_text.encode()), "text/csv")},
        data={"mode": "add_update"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["committed"] is True
    return resp.json()


def test_generation_after_room_import_uses_the_newly_imported_rooms_correctly(
    client, db_session, context
):
    db = db_session

    # Import rooms the way an admin would - CSV, not the CRUD form - before
    # any curriculum exists yet, exactly PART 18's real workflow order.
    _import_rooms(
        client,
        "block,floor,room_number,capacity,room_type,lab_type,is_active\n"
        "A,1,A101,70,theory,,true\n"
        "A,1,A-F1,2,faculty,,true\n"
        "C,1,LAB-C1,70,lab,COMPUTING,true\n"
        "C,1,LAB-E1,70,lab,ELECTRONICS,true\n",
    )

    cs201 = Subject(name="Data Structures", code="CS201", type="theory",
                     session_length_hours=1, sessions_per_week=3)
    cs251 = Subject(name="DS Lab", code="CS251", type="practical",
                     session_length_hours=2, sessions_per_week=1, required_lab_type="COMPUTING")
    db.add_all([cs201, cs251])
    db.flush()

    fac_a = Faculty(name="Dr. A", faculty_code="FAC01")
    fac_a.subjects = [cs201]
    fac_l = Faculty(name="Dr. L", faculty_code="FAC03")
    fac_l.subjects = [cs251]
    db.add_all([fac_a, fac_l])

    section = Section(academic_context_id=context.id, section_number="CSE-3A", strength=60)
    section.subjects = [cs201, cs251]
    db.add(section)

    slots = build_grid()
    for s in slots:
        if s.period_index == 4:
            s.is_lunch = True
    db.add_all(slots)
    db.commit()

    result, run = generate(db, academic_context_id=context.id, soft=False, max_seconds=20)
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.message
    assert check_all(db, run.id) == []

    assignments = db.query(Assignment).filter(Assignment.run_id == run.id).all()
    room_ids_used = {a.room_id for a in assignments}
    used_rooms = {db.get(Room, rid).room_number for rid in room_ids_used}

    faculty_room = db.query(Room).filter(Room.room_number == "A-F1").one()
    comp_lab = db.query(Room).filter(Room.room_number == "LAB-C1").one()
    elec_lab = db.query(Room).filter(Room.room_number == "LAB-E1").one()
    theory_room = db.query(Room).filter(Room.room_number == "A101").one()

    # The newly imported theory and lab rooms are genuinely selectable.
    assert theory_room.room_number in used_rooms
    assert comp_lab.room_number in used_rooms

    # Faculty room and the wrong-lab-type room are never used, matching
    # exactly what CRUD-created rooms would have produced.
    assert faculty_room.id not in room_ids_used
    assert elec_lab.id not in room_ids_used

    # The practical's room is genuinely capacity/lab-type correct.
    practical = next(a for a in assignments if a.subject_id == cs251.id)
    assert practical.room_id == comp_lab.id


def test_persistent_assignment_survives_regeneration_after_a_room_import(client, db_session, context):
    """A room import must not disturb an already-decided, persisted
    faculty/room choice for an existing (section, subject) pair."""
    db = db_session

    _import_rooms(
        client,
        "block,floor,room_number,capacity,room_type\n"
        "A,1,A101,70,theory\nA,1,A102,70,theory\n",
    )

    cs201 = Subject(name="Data Structures", code="CS201", type="theory",
                     session_length_hours=1, sessions_per_week=3)
    db.add(cs201)
    db.flush()
    fac_a = Faculty(name="Dr. A", faculty_code="FAC01")
    fac_b = Faculty(name="Dr. B", faculty_code="FAC02")
    fac_a.subjects = fac_b.subjects = [cs201]
    db.add_all([fac_a, fac_b])
    section = Section(academic_context_id=context.id, section_number="CSE-3A", strength=60)
    section.subjects = [cs201]
    db.add(section)
    slots = build_grid()
    for s in slots:
        if s.period_index == 4:
            s.is_lunch = True
    db.add_all(slots)
    db.commit()

    result1, run1 = generate(db, academic_context_id=context.id, soft=False, max_seconds=20)
    assert result1.status in {"OPTIMAL", "FEASIBLE"}
    anchor = db.query(SectionSubjectAssignment).filter(
        SectionSubjectAssignment.section_id == section.id,
        SectionSubjectAssignment.subject_id == cs201.id,
    ).one()
    first_faculty_id, first_room_id = anchor.faculty_id, anchor.room_id

    # A second, unrelated room import happens between generations - this must
    # not perturb the already-decided assignment.
    _import_rooms(
        client,
        "block,floor,room_number,capacity,room_type\n"
        "A,1,A101,70,theory\nA,1,A102,70,theory\nB,2,B201,70,theory\n",
    )

    result2, run2 = generate(db, academic_context_id=context.id, soft=False, max_seconds=20)
    assert result2.status in {"OPTIMAL", "FEASIBLE"}
    anchor2 = db.query(SectionSubjectAssignment).filter(
        SectionSubjectAssignment.section_id == section.id,
        SectionSubjectAssignment.subject_id == cs201.id,
    ).one()
    assert anchor2.faculty_id == first_faculty_id
    assert anchor2.room_id == first_room_id
