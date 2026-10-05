"""The room list is saved once; the teaching load comes and goes.

    Upload Infra.xlsx once -> upload Load.xlsx -> generate
    later: upload a new Load.xlsx -> generate, with the same rooms
    replace Infra.xlsx -> the timetables built on the old rooms go
    clear the load, the rooms, or both - and only what was asked for

Every test here checks the database afterwards, not only the response.
"""
from __future__ import annotations

from app.infrastructure import CONFIRMATIONS, active, room_ids
from app.models import (
    AcademicContext,
    Faculty,
    Infrastructure,
    Room,
    Section,
    Subject,
    TimetableRun,
    User,
)
from app.room_scope import usable_rooms
from tests.test_teacher_import import INFRA_HEADERS, INFRA_ROWS, LOAD_HEADERS, LOAD_ROWS, _xlsx

XLSX = "application/vnd.ms-excel"


def _infra(rows=INFRA_ROWS) -> tuple:
    return ("Infra.xlsx", _xlsx(INFRA_HEADERS, rows), XLSX)


def _load(rows=LOAD_ROWS) -> tuple:
    return ("Load.xlsx", _xlsx(LOAD_HEADERS, rows), XLSX)


def _save_infra(client, rows=INFRA_ROWS, **form):
    return client.post("/api/infrastructure", files={"infra": _infra(rows)}, data=form)


def _load_only(client, path, rows=LOAD_ROWS):
    return client.post(path, files={"load": _load(rows)})


def _a_timetable(db, context_id: int) -> None:
    """A generated timetable, as far as replacing and clearing care."""
    db.add(TimetableRun(academic_context_id=context_id, version=1, status="OPTIMAL",
                        publish_status="DRAFT"))
    db.commit()


# The fixture's room list without its last five rooms: a different list.
FEWER_ROOMS = INFRA_ROWS[:-5]


# ------------------------------------------------------------- saving rooms


def test_nothing_is_saved_to_begin_with(client):
    body = client.get("/api/infrastructure").json()
    assert body == {"available": False, "filename": None, "uploaded_at": None,
                    "summary": {}, "timetables": 0}


def test_saving_the_room_list_on_its_own(client, db_session):
    preview = client.post("/api/infrastructure/preview", files={"infra": _infra()})
    assert preview.status_code == 200, preview.text
    assert preview.json()["replaces"] is False
    assert db_session.query(Infrastructure).count() == 0, "preview writes nothing"

    saved = _save_infra(client)
    assert saved.status_code == 200, saved.text
    assert saved.json()["committed"] is True

    body = client.get("/api/infrastructure").json()
    assert body["available"] is True
    assert body["filename"] == "Infra.xlsx"
    assert body["summary"]["rooms"] == len(INFRA_ROWS)
    assert len(room_ids(db_session, active(db_session).id)) == len(INFRA_ROWS)


def test_a_teaching_load_needs_rooms_saved_first(client):
    resp = _load_only(client, "/api/teacher/preview")
    assert resp.status_code == 422
    assert "No infrastructure is saved yet" in resp.json()["detail"]["message"]


# ------------------------------------------------------- reusing the rooms


def test_a_teaching_load_is_scheduled_against_the_saved_rooms(client, db_session):
    _save_infra(client)
    saved = active(db_session)

    preview = _load_only(client, "/api/teacher/preview").json()
    assert preview["readiness"]["ready"] is True, preview["readiness"]
    # The dashboard's numbers include the saved rooms, though no room list came.
    assert preview["summary"]["rooms"] == len(INFRA_ROWS)

    applied = _load_only(client, "/api/teacher/apply").json()
    assert applied["committed"] is True
    ctx = db_session.get(AcademicContext, applied["academic_context_id"])
    assert ctx.infrastructure_id == saved.id
    assert {r.id for r in usable_rooms(db_session, ctx.id)} == room_ids(db_session, saved.id)


def test_a_second_teaching_load_reuses_the_same_rooms(client, db_session):
    _save_infra(client)
    first = _load_only(client, "/api/teacher/apply").json()
    second = _load_only(client, "/api/teacher/apply").json()
    assert second["committed"] is True and second["readiness"]["ready"] is True
    a = db_session.get(AcademicContext, first["academic_context_id"])
    b = db_session.get(AcademicContext, second["academic_context_id"])
    assert a.infrastructure_id == b.infrastructure_id
    assert db_session.query(Infrastructure).count() == 1

    # And the second one solves inside those rooms.
    from app.solver.data import load
    from app.solver.solve import solve

    result = solve(load(db_session, academic_context_id=b.id), max_seconds=60, soft=False)
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.message
    assert {p.room_id for p in result.placements} <= room_ids(db_session, b.infrastructure_id)


def test_uploading_both_files_together_saves_the_rooms_too(client, db_session):
    body = client.post(
        "/api/teacher/apply", files={"load": _load(), "infra": _infra()}
    ).json()
    assert body["committed"] is True
    assert client.get("/api/infrastructure").json()["available"] is True
    ctx = db_session.get(AcademicContext, body["academic_context_id"])
    assert ctx.infrastructure_id == active(db_session).id


# ------------------------------------------------------ replacing the rooms


def test_the_same_room_list_again_replaces_nothing(client, db_session):
    _save_infra(client)
    first_id = active(db_session).id
    ctx = _load_only(client, "/api/teacher/apply").json()["academic_context_id"]
    _a_timetable(db_session, ctx)

    preview = client.post("/api/infrastructure/preview", files={"infra": _infra()}).json()
    assert preview["unchanged"] is True and preview["replaces"] is False
    again = _save_infra(client)
    assert again.status_code == 200, again.text
    assert active(db_session).id == first_id
    assert db_session.query(TimetableRun).count() == 1, "no timetable was removed"


def test_a_different_room_list_asks_before_removing_timetables(client, db_session):
    _save_infra(client)
    ctx_id = _load_only(client, "/api/teacher/apply").json()["academic_context_id"]
    _a_timetable(db_session, ctx_id)

    preview = client.post("/api/infrastructure/preview",
                          files={"infra": _infra(FEWER_ROOMS)}).json()
    assert preview["replaces"] is True and preview["timetables_removed"] == 1

    refused = _save_infra(client, FEWER_ROOMS)
    assert refused.status_code == 409
    assert refused.json()["detail"]["timetables"] == 1
    db_session.expire_all()
    assert db_session.query(TimetableRun).count() == 1, "nothing removed without confirming"

    replaced = _save_infra(client, FEWER_ROOMS, confirm_replace="true")
    assert replaced.status_code == 200, replaced.text
    db_session.expire_all()
    assert db_session.query(TimetableRun).count() == 0
    new = active(db_session)
    assert db_session.query(Infrastructure).count() == 1
    assert len(room_ids(db_session, new.id)) == len(FEWER_ROOMS)

    # The teaching load survived, and is now scheduled against the new rooms.
    ctx = db_session.get(AcademicContext, ctx_id)
    assert ctx.infrastructure_id == new.id
    assert {r.id for r in usable_rooms(db_session, ctx_id)} == room_ids(db_session, new.id)
    assert db_session.query(Section).filter_by(academic_context_id=ctx_id).count() == 9


def test_a_different_room_list_with_the_load_needs_the_same_confirmation(client, db_session):
    _save_infra(client)
    ctx_id = _load_only(client, "/api/teacher/apply").json()["academic_context_id"]
    _a_timetable(db_session, ctx_id)

    resp = client.post("/api/teacher/apply",
                       files={"load": _load(), "infra": _infra(FEWER_ROOMS)})
    assert resp.status_code == 409
    db_session.expire_all()
    assert db_session.query(TimetableRun).count() == 1


# ----------------------------------------------------------------- clearing


def _everything(client, db_session) -> int:
    _save_infra(client)
    ctx = _load_only(client, "/api/teacher/apply").json()["academic_context_id"]
    _a_timetable(db_session, ctx)
    return ctx


def _clear(client, what, confirm=None):
    return client.post("/api/data/clear",
                       json={"what": what, "confirm": confirm or CONFIRMATIONS[what]})


def test_clearing_says_first_what_it_would_delete(client, db_session):
    _everything(client, db_session)
    body = client.get("/api/data/clear?what=load").json()
    assert body["confirmation"] == "CLEAR LOAD DATA"
    assert body["counts"]["section"] == 9 and body["counts"]["timetable_run"] == 1
    assert "room" not in body["counts"]
    assert db_session.query(Section).count() == 9, "the preview deletes nothing"


def test_clearing_needs_the_exact_phrase(client, db_session):
    _everything(client, db_session)
    resp = _clear(client, "both", confirm="clear all data")
    assert resp.status_code == 400
    assert db_session.query(Room).count() == len(INFRA_ROWS)
    assert db_session.query(Section).count() == 9


def test_clearing_the_load_keeps_the_rooms(client, db_session):
    _everything(client, db_session)
    resp = _clear(client, "load")
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    for model in (AcademicContext, Section, Subject, Faculty, TimetableRun):
        assert db_session.query(model).count() == 0, model.__name__
    assert db_session.query(Room).count() == len(INFRA_ROWS)
    assert client.get("/api/infrastructure").json()["available"] is True

    # A new teaching load goes straight onto the kept rooms.
    again = _load_only(client, "/api/teacher/apply").json()
    assert again["committed"] is True and again["readiness"]["ready"] is True


def test_clearing_the_rooms_keeps_the_load(client, db_session):
    ctx = _everything(client, db_session)
    resp = _clear(client, "infrastructure")
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    assert db_session.query(Room).count() == 0
    assert db_session.query(Infrastructure).count() == 0
    assert db_session.query(TimetableRun).count() == 0
    assert db_session.query(Section).filter_by(academic_context_id=ctx).count() == 9
    assert db_session.query(Faculty).count() == 7
    assert client.get("/api/infrastructure").json()["available"] is False


def test_clearing_both_leaves_a_clean_start_and_the_accounts(client, db_session):
    _everything(client, db_session)
    users = db_session.query(User).count()
    resp = _clear(client, "both")
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    for model in (AcademicContext, Section, Subject, Faculty, TimetableRun, Room, Infrastructure):
        assert db_session.query(model).count() == 0, model.__name__
    assert db_session.query(User).count() == users
