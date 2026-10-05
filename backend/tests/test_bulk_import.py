"""Phase 9: bulk room import - preview, upsert, full sync, validation, history."""
from __future__ import annotations

import io

from app.models import ImportHistory, Room


def upload(client, entity: str, content: str | bytes, filename: str, action: str, mode: str = "add_update", confirm=False):
    data = content if isinstance(content, bytes) else content.encode()
    form = {"mode": mode}
    if action == "apply":
        form["confirm_deactivations"] = "true" if confirm else "false"
    return client.post(
        f"/api/bulk-import/{entity}/{action}",
        files={"file": (filename, io.BytesIO(data), "text/csv")},
        data=form,
    )


ROOMS_CSV = (
    "block,floor,room_number,capacity,room_type,lab_type,is_active\n"
    "38,5,38-502,70,theory,,true\n"
    "36,7,36-701,60,lab,COMPUTING,true\n"
)


# ------------------------------------------------------------------- template


def test_room_entity_is_advertised(client):
    entities = client.get("/api/bulk-import/entities").json()
    keys = {e["key"] for e in entities}
    assert "rooms" in keys
    rooms_spec = next(e for e in entities if e["key"] == "rooms")
    assert set(rooms_spec["required"]) <= set(rooms_spec["columns"])
    assert "room_number" in rooms_spec["required"]


def test_template_csv_has_correct_headers_and_sample_rows(client):
    resp = client.get("/api/bulk-import/rooms/template.csv")
    assert resp.status_code == 200
    lines = resp.text.strip().splitlines()
    header = lines[0].split(",")
    assert header == [
        "block", "floor", "room_number", "room_code", "capacity",
        "room_type", "lab_type", "byod", "charging", "charging_sockets",
        "is_faculty_room", "is_active",
    ]
    assert len(lines) >= 2  # header + at least one example row


def test_template_xlsx_downloads(client):
    resp = client.get("/api/bulk-import/rooms/template.xlsx")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/vnd.openxml")
    assert len(resp.content) > 0


# --------------------------------------------------------------------- preview


def test_preview_new_rooms_does_not_write_anything(client, db_session):
    resp = upload(client, "rooms", ROOMS_CSV, "rooms.csv", "preview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"]["create"] == 2
    assert body["counts"]["update"] == 0
    assert body["can_apply"] is True
    assert body["committed"] is False
    assert db_session.query(Room).count() == 0


def test_missing_required_column_is_rejected(client):
    resp = upload(client, "rooms", "block,room_number\nA,A101\n", "bad.csv", "preview")
    assert resp.status_code == 422
    assert "capacity" in resp.json()["detail"] or "room_type" in resp.json()["detail"]


def test_missing_capacity_value_gives_actionable_row_message(client):
    csv = "block,floor,room_number,capacity,room_type\n38,3,36-301,,theory\n"
    resp = upload(client, "rooms", csv, "rooms.csv", "preview")
    body = resp.json()
    assert body["counts"]["invalid"] == 1
    row = body["rows"][0]
    assert row["row_number"] == 2
    assert "36-301" in row["identity"]
    assert "capacity is missing" in row["message"]


def test_invalid_capacity_value_is_rejected(client):
    csv = "block,floor,room_number,capacity,room_type\nA,1,A999,not-a-number,theory\n"
    resp = upload(client, "rooms", csv, "rooms.csv", "preview")
    row = resp.json()["rows"][0]
    assert row["verdict"] == "invalid"
    assert "whole number" in row["message"]


def test_invalid_room_type_is_rejected(client):
    csv = "block,floor,room_number,capacity,room_type\nA,1,A999,70,gymnasium\n"
    resp = upload(client, "rooms", csv, "rooms.csv", "preview")
    row = resp.json()["rows"][0]
    assert row["verdict"] == "invalid"
    assert "room_type" in row["message"]


def test_lab_type_on_a_theory_room_is_rejected(client):
    csv = "block,floor,room_number,capacity,room_type,lab_type\nA,1,A999,70,theory,COMPUTING\n"
    resp = upload(client, "rooms", csv, "rooms.csv", "preview")
    row = resp.json()["rows"][0]
    assert row["verdict"] == "invalid"
    assert "lab_type" in row["message"]


def test_duplicate_rows_in_the_same_file_are_flagged(client):
    csv = (
        "block,floor,room_number,capacity,room_type\n"
        "38,5,38-502,70,theory\n"
        "38,5,38-502,75,theory\n"
    )
    resp = upload(client, "rooms", csv, "rooms.csv", "preview")
    body = resp.json()
    assert body["counts"]["create"] == 1
    assert body["counts"]["duplicate"] == 1
    dup = next(r for r in body["rows"] if r["verdict"] == "duplicate")
    assert "row 2" in dup["message"]


def test_same_room_number_in_two_blocks_is_not_a_duplicate(client):
    """Identity is block + floor + room_number, not room_number alone."""
    csv = (
        "block,floor,room_number,capacity,room_type\n"
        "A,1,301,70,theory\n"
        "B,3,301,70,theory\n"
    )
    resp = upload(client, "rooms", csv, "rooms.csv", "preview")
    body = resp.json()
    assert body["counts"]["create"] == 2
    assert body["counts"]["duplicate"] == 0


# ----------------------------------------------------------------------- xlsx


def test_valid_room_xlsx_previews_correctly(client):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["block", "floor", "room_number", "capacity", "room_type", "lab_type", "is_active"])
    ws.append(["38", "5", "38-502", 70, "theory", "", "true"])
    ws.append(["36", "7", "36-701", 60.0, "lab", "COMPUTING", "true"])
    buf = io.BytesIO()
    wb.save(buf)

    resp = client.post(
        "/api/bulk-import/rooms/preview",
        files={"file": ("rooms.xlsx", buf.getvalue(),
               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"mode": "add_update"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"]["create"] == 2
    # openpyxl numeric capacity (70, not "70.0") must not corrupt validation
    assert body["counts"]["invalid"] == 0


# ------------------------------------------------------------------------ apply


def test_apply_creates_new_rooms_and_records_history(client, db_session):
    resp = upload(client, "rooms", ROOMS_CSV, "rooms.csv", "apply")
    assert resp.status_code == 200
    body = resp.json()
    assert body["committed"] is True
    assert body["counts"]["create"] == 2

    rooms = db_session.query(Room).order_by(Room.room_number).all()
    assert [r.room_number for r in rooms] == ["36-701", "38-502"]
    lab = next(r for r in rooms if r.room_number == "36-701")
    assert lab.room_type == "lab" and lab.lab_type == "COMPUTING"

    history = db_session.query(ImportHistory).one()
    assert history.entity == "rooms"
    assert history.status == "applied"
    assert history.rows_created == 2
    assert history.filename == "rooms.csv"


def test_apply_with_invalid_rows_writes_nothing(client, db_session):
    csv = (
        "block,floor,room_number,capacity,room_type\n"
        "38,5,38-502,70,theory\n"
        "38,5,38-503,,theory\n"  # missing capacity
    )
    resp = upload(client, "rooms", csv, "rooms.csv", "apply")
    body = resp.json()
    assert body["committed"] is False
    assert db_session.query(Room).count() == 0
    history = db_session.query(ImportHistory).one()
    assert history.status == "rejected"


def test_existing_room_is_updated_not_duplicated(client, db_session):
    upload(client, "rooms", ROOMS_CSV, "rooms.csv", "apply")
    assert db_session.query(Room).count() == 2

    updated_csv = (
        "block,floor,room_number,capacity,room_type,lab_type,is_active\n"
        "38,5,38-502,75,theory,,true\n"   # capacity 70 -> 75
        "36,7,36-701,60,lab,COMPUTING,true\n"
    )
    resp = upload(client, "rooms", updated_csv, "rooms2.csv", "apply")
    body = resp.json()
    assert body["counts"]["update"] == 1
    assert body["counts"]["unchanged"] == 1
    assert db_session.query(Room).count() == 2  # no duplicate created

    room = db_session.query(Room).filter(Room.room_number == "38-502").one()
    assert room.capacity == 75

    row = next(r for r in body["rows"] if r["identity"].startswith("38-502"))
    change = next(c for c in row["changes"] if c["field"] == "capacity")
    assert change["old"] == "70" and change["new"] == "75"


def test_room_missing_from_file_is_not_deleted_in_normal_mode(client, db_session):
    upload(client, "rooms", ROOMS_CSV, "rooms.csv", "apply")
    assert db_session.query(Room).count() == 2

    only_one = "block,floor,room_number,capacity,room_type\n38,5,38-502,70,theory\n"
    resp = upload(client, "rooms", only_one, "rooms3.csv", "apply", mode="add_update")
    assert resp.status_code == 200
    assert db_session.query(Room).count() == 2  # 36-701 still present
    room = db_session.query(Room).filter(Room.room_number == "36-701").one()
    assert room.is_active is True


def test_full_sync_identifies_absent_rooms_without_deleting(client, db_session):
    upload(client, "rooms", ROOMS_CSV, "rooms.csv", "apply")

    only_one = "block,floor,room_number,capacity,room_type\n38,5,38-502,70,theory\n"
    preview = upload(client, "rooms", only_one, "rooms3.csv", "preview", mode="full_sync")
    body = preview.json()
    assert len(body["deactivations"]) == 1
    assert body["deactivations"][0]["identity"].startswith("36-701")
    # preview never writes
    assert db_session.query(Room).filter(Room.room_number == "36-701").one().is_active is True


def test_full_sync_apply_requires_explicit_confirmation(client, db_session):
    upload(client, "rooms", ROOMS_CSV, "rooms.csv", "apply")
    only_one = "block,floor,room_number,capacity,room_type\n38,5,38-502,70,theory\n"

    resp = upload(client, "rooms", only_one, "rooms3.csv", "apply", mode="full_sync", confirm=False)
    body = resp.json()
    assert body["committed"] is False
    room = db_session.query(Room).filter(Room.room_number == "36-701").one()
    assert room.is_active is True  # untouched without confirmation

    resp2 = upload(client, "rooms", only_one, "rooms3.csv", "apply", mode="full_sync", confirm=True)
    body2 = resp2.json()
    assert body2["committed"] is True
    room2 = db_session.query(Room).filter(Room.room_number == "36-701").one()
    assert room2.is_active is False  # deactivated, never deleted
    assert db_session.query(Room).count() == 2


def test_import_history_lists_recent_imports(client):
    upload(client, "rooms", ROOMS_CSV, "rooms.csv", "apply")
    resp = client.get("/api/bulk-import/history")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["entity"] == "rooms"
    assert body[0]["rows_created"] == 2


# --------------------------------------------------------------- faculty rooms


def test_imported_faculty_room_is_excluded_from_room_eligibility(client, db_session):
    """A room imported as room_type=faculty must behave exactly like one
    created through the CRUD form - never schedulable."""
    from app.grid import build_grid
    from app.models import AcademicContext, Faculty, Section, Subject
    from app.solver.data import load

    csv = (
        "block,floor,room_number,capacity,room_type\n"
        "A,1,A-F9,2,faculty\n"
        "A,1,A101,70,theory\n"
    )
    upload(client, "rooms", csv, "rooms.csv", "apply")

    db_session.add_all(build_grid())
    ctx = AcademicContext(academic_year="2026-27", semester=1, program="BCA", department="CSE")
    db_session.add(ctx)
    db_session.flush()
    subject = Subject(name="Data Structures", code="CS201", type="theory",
                       session_length_hours=1, sessions_per_week=2)
    faculty = Faculty(name="Dr. A", faculty_code="FAC01")
    faculty.subjects = [subject]
    db_session.add_all([subject, faculty])
    section = Section(academic_context_id=ctx.id, section_number="S-A", strength=60)
    section.subjects = [subject]
    db_session.add(section)
    db_session.commit()

    inp = load(db_session, ctx.id)
    faculty_room = db_session.query(Room).filter(Room.room_number == "A-F9").one()
    assert len(inp.pairs) == 1
    for pair in inp.pairs:
        assert faculty_room.id not in pair.room_ids


def test_imported_lab_capability_matching_is_correct(client, db_session):
    """A lab imported with lab_type=COMPUTING must only be eligible for
    subjects that require that same lab_type - matching the CRUD-created
    behaviour exactly, since this reuses the same eligibility rule."""
    from app.grid import build_grid
    from app.models import AcademicContext, Faculty, Section, Subject
    from app.solver.data import load

    csv = (
        "block,floor,room_number,capacity,room_type,lab_type\n"
        "C,1,LAB-E1,70,lab,ELECTRONICS\n"
        "C,1,LAB-C1,70,lab,COMPUTING\n"
    )
    upload(client, "rooms", csv, "rooms.csv", "apply")

    db_session.add_all(build_grid())
    ctx = AcademicContext(academic_year="2026-27", semester=1, program="BCA", department="CSE")
    db_session.add(ctx)
    db_session.flush()
    subject = Subject(name="DS Lab", code="CS251", type="practical",
                       session_length_hours=2, sessions_per_week=1, required_lab_type="COMPUTING")
    faculty = Faculty(name="Dr. L", faculty_code="FAC03")
    faculty.subjects = [subject]
    db_session.add_all([subject, faculty])
    section = Section(academic_context_id=ctx.id, section_number="S-A", strength=60)
    section.subjects = [subject]
    db_session.add(section)
    db_session.commit()

    inp = load(db_session, ctx.id)
    comp_lab = db_session.query(Room).filter(Room.room_number == "LAB-C1").one()
    elec_lab = db_session.query(Room).filter(Room.room_number == "LAB-E1").one()
    assert len(inp.pairs) == 1
    pair = inp.pairs[0]
    assert comp_lab.id in pair.room_ids
    assert elec_lab.id not in pair.room_ids
