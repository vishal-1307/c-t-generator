"""An intake's timetable uses the rooms in its own room list, and no others.

Found on the deployment: the department's real files produced a valid
timetable with 40 of its 52 periods in rooms from an earlier dataset - rooms
that are not in its Infra.xlsx and do not exist in its building. Every rule
held; the answer was still wrong for the people who would have to walk to it.

Rooms are institution-wide, so the fix is a list per intake, set by the
two-file import, read by everything that decides eligibility. These tests
check that pool directly - never which room the solver happened to pick, which
would pass or fail on search order.
"""
from __future__ import annotations

from app import manual_edit, timetable_checker, validation
from app.models import (
    AcademicContext,
    AcademicContextRoom,
    Assignment,
    Room,
    Section,
    TimetableRun,
    TimeSlot,
)
from app.room_scope import usable_rooms
from app.solver.data import load
from app.solver.run import generate
from tests.test_teacher_import import (
    CONTEXT,
    INFRA_HEADERS,
    INFRA_ROWS,
    LOAD_HEADERS,
    LOAD_ROWS,
    _post,
    _xlsx,
)

INFRA_CODES = {f"{r[0]}-{r[1]}" for r in INFRA_ROWS}


def _outsider(db, **kw):
    """A room already on record that the room list does not name - and that
    would suit any class in it: big, a classroom, with BYOD."""
    fields = dict(room_number="201", block="42", room_code="42-201", capacity=200,
                  room_type="theory", is_active=True, byod=True, charging=True)
    fields.update(kw)
    room = Room(**fields)
    db.add(room)
    db.commit()
    return room


def _files(load_rows=LOAD_ROWS, infra_rows=INFRA_ROWS):
    return _xlsx(LOAD_HEADERS, load_rows), _xlsx(INFRA_HEADERS, infra_rows)


def _context(db):
    return db.query(AcademicContext).filter_by(program=CONTEXT["program"]).one()


def test_an_imported_intake_can_only_use_the_rooms_in_its_room_list(client, db_session):
    outsider = _outsider(db_session)
    lab_outsider = _outsider(db_session, room_number="202", room_code="42-202",
                             room_type="lab", capacity=70)

    resp = _post(client, "/api/teacher/apply", _files())
    assert resp.json()["committed"] is True
    ctx = _context(db_session)

    codes = {r.room_code for r in usable_rooms(db_session, ctx.id)}
    assert codes == INFRA_CODES

    inp = load(db_session, academic_context_id=ctx.id)
    offered = {rid for pair in inp.pairs for rid in pair.room_ids}
    assert outsider.id not in offered and lab_outsider.id not in offered

    result, run = generate(db_session, ctx.id, max_seconds=30)
    assert result.ok, result.message
    used = {
        db_session.get(Room, a.room_id).room_code
        for a in db_session.query(Assignment).filter(Assignment.run_id == run.id)
    }
    assert used <= INFRA_CODES, used - INFRA_CODES
    assert timetable_checker.check_all(db_session, run.id) == []


def test_an_intake_without_a_room_list_still_uses_every_room(db_session, context):
    """Everything created before room lists existed - and anything from the
    general importer - must behave exactly as it always did."""
    a = _outsider(db_session)
    b = _outsider(db_session, room_number="301", block="36", room_code="36-301")
    assert {r.id for r in usable_rooms(db_session, context.id)} >= {a.id, b.id}
    assert {r.id for r in usable_rooms(db_session, None)} >= {a.id, b.id}


def test_validation_judges_readiness_against_the_intakes_own_rooms(client, db_session):
    """ECE212 needs a room for 120. Take both 120-seat rooms out of the list
    and leave a 200-seat room on record outside it: the intake is not ready,
    because the room that would fit is not one it may use."""
    _outsider(db_session)
    infra = [r for r in INFRA_ROWS if r[2] != 120]

    resp = _post(client, "/api/teacher/preview", _files(infra_rows=infra))
    body = resp.json()
    assert body["readiness"]["ready"] is False
    assert any("ECE212" in b["detail"] for b in body["readiness"]["blockers"])


def test_the_checker_rejects_a_class_in_a_room_outside_the_list(client, db_session):
    outsider = _outsider(db_session)
    _post(client, "/api/teacher/apply", _files())
    ctx = _context(db_session)
    result, run = generate(db_session, ctx.id, max_seconds=30)
    assert result.ok

    # Move one lecture into the outsider by hand, behind every check's back.
    row = next(
        a for a in db_session.query(Assignment).filter(Assignment.run_id == run.id)
        if a.session_type == "L"
    )
    block = db_session.query(Assignment).filter_by(run_id=run.id, block_id=row.block_id).all()
    for a in block:
        a.room_id = outsider.id
    db_session.commit()

    problems = timetable_checker.check_all(db_session, run.id)
    assert any("not in this semester's room list" in p for p in problems), problems


def test_a_manual_room_change_cannot_use_a_room_outside_the_list(client, db_session):
    outsider = _outsider(db_session)
    _post(client, "/api/teacher/apply", _files())
    ctx = _context(db_session)
    result, run = generate(db_session, ctx.id, max_seconds=30)
    assert result.ok
    lecture = next(
        a for a in db_session.query(Assignment).filter(Assignment.run_id == run.id)
        if a.session_type == "L"
    )

    preview = manual_edit.preview_room_change(db_session, lecture.id, outsider.id)
    assert not preview.ok
    outcome = manual_edit.apply_room_change(db_session, lecture.id, outsider.id)
    assert not outcome.ok


def test_an_unchanged_re_upload_still_records_the_room_list(client, db_session):
    """An intake imported before room lists existed gets one the next time its
    files are uploaded - even when nothing in them changed, which is exactly
    the state the deployment's intake was in."""
    _post(client, "/api/teacher/apply", _files())
    ctx = _context(db_session)
    db_session.query(AcademicContextRoom).delete()
    db_session.commit()
    assert len(usable_rooms(db_session, ctx.id)) == db_session.query(Room).count()

    again = _post(client, "/api/teacher/apply", _files())
    assert again.json()["has_errors"] is False
    db_session.expire_all()
    assert {r.room_code for r in usable_rooms(db_session, ctx.id)} == INFRA_CODES


def test_a_room_dropped_from_the_list_stops_being_used_but_is_not_touched(
    client, db_session
):
    _post(client, "/api/teacher/apply", _files())
    ctx = _context(db_session)
    shorter = [r for r in INFRA_ROWS if not (r[0] == 36 and r[1] == 301)]

    _post(client, "/api/teacher/apply", _files(infra_rows=shorter))
    db_session.expire_all()
    codes = {r.room_code for r in usable_rooms(db_session, ctx.id)}
    assert "36-301" not in codes
    room = db_session.query(Room).filter_by(block="36", room_number="301").one()
    assert room.is_active is True, "the room itself must be left alone"


def test_the_preview_says_other_rooms_exist_and_will_not_be_used(client, db_session):
    _outsider(db_session)
    body = _post(client, "/api/teacher/preview", _files()).json()
    assert any("1 other rooms on record" in w and "only the 30 rooms" in w
               for w in body["warnings"]), body["warnings"]
    # And the preview wrote nothing - not even the room list.
    assert db_session.query(AcademicContextRoom).count() == 0
