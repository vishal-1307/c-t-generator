"""What the 2026-09-17 audit changed, held down.

- Sign-in slows guessing: repeated failures are refused with 429.
- Reads that name people (import history, change history) need an account.
- Uploads: no macro workbooks, the size limit is refused before the body is
  read and named from the setting, a workbook with absurdly many rows is
  refused, and workbook XML is parsed with defusedxml.
- Downloads and printed headers carry a dataset and a version, never an id.
- The published version is the timetable in force on every view.
- The Generate page's figures describe the dataset, not the whole database.
"""
from __future__ import annotations

import io
import re

import pytest
from openpyxl import Workbook

from app.models import Assignment, Faculty, SectionSubjectAssignment
from app.solver.run import generate
from app import validation

from test_master_timetable_audit import solved  # noqa: F401 - shared fixture


# ------------------------------------------------------------------ sign-in


def test_repeated_failed_sign_ins_are_refused_for_a_while(client, db_session):
    from app.config import settings

    for _ in range(settings.login_max_attempts):
        bad = client.post("/api/auth/login", json={"username": "test-admin", "password": "nope"})
        assert bad.status_code == 401

    locked = client.post("/api/auth/login", json={"username": "test-admin", "password": "nope"})
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    assert "Too many sign-in attempts" in locked.json()["detail"]

    # Even the right password waits: otherwise the limit tells a guesser when
    # they have found it.
    from conftest import TEST_ADMIN_PASSWORD

    right = client.post("/api/auth/login",
                        json={"username": "test-admin", "password": TEST_ADMIN_PASSWORD})
    assert right.status_code == 429


def test_a_successful_sign_in_clears_that_usernames_failures(client):
    from app.config import settings
    from conftest import TEST_ADMIN_PASSWORD

    for _ in range(settings.login_max_attempts - 1):
        client.post("/api/auth/login", json={"username": "test-admin", "password": "nope"})
    ok = client.post("/api/auth/login",
                     json={"username": "test-admin", "password": TEST_ADMIN_PASSWORD})
    assert ok.status_code == 200

    from app.login_limit import login_limiter

    assert login_limiter.retry_after("someone-else", "test-admin") == 0


def test_the_limiter_counts_addresses_and_usernames_separately():
    from app.login_limit import LoginLimiter

    limiter = LoginLimiter(attempts=3, window_seconds=60)
    for name in ("a", "b", "c"):
        limiter.failed("10.0.0.1", name)
    # One address guessing three usernames is stopped for any username.
    assert limiter.retry_after("10.0.0.1", "d") > 0
    # A different address guessing a fresh username is not.
    assert limiter.retry_after("10.0.0.2", "d") == 0
    for ip in ("10.0.0.3", "10.0.0.4", "10.0.0.5"):
        limiter.failed(ip, "target")
    # Three addresses guessing one username stop that username everywhere.
    assert limiter.retry_after("10.0.0.9", "target") > 0


# --------------------------------------------------- reads that name people


def test_import_and_change_history_need_an_account(client, anon_client, solved):  # noqa: F811
    run = solved["run"]
    a = solved["db"].query(Assignment).filter(Assignment.run_id == run.id).first()
    for path in ("/api/bulk-import/history",
                 f"/api/assignments/{a.id}/history",
                 f"/api/assignments/history/run/{run.id}"):
        assert anon_client.get(path).status_code == 401, path
        assert client.get(path).status_code == 200, path


# ------------------------------------------------------------------ uploads


def _xlsx(rows: int) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["block", "floor", "room_number", "capacity", "room_type"])
    for i in range(rows):
        ws.append(["36", "1", f"R{i}", 60, "theory"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_a_macro_enabled_workbook_is_refused(client):
    resp = client.post(
        "/api/bulk-import/rooms/preview",
        files={"file": ("rooms.xlsm", io.BytesIO(_xlsx(1)), "application/octet-stream")},
        data={"mode": "add_update"},
    )
    assert resp.status_code == 422
    assert ".xlsm" not in resp.json()["detail"].split("accepted")[0]


def test_the_size_limit_is_named_from_the_setting(client, monkeypatch):
    from app.routers import bulk_import as router

    monkeypatch.setattr(router, "MAX_BYTES", 2 * 1024 * 1024)
    resp = client.post(
        "/api/bulk-import/rooms/preview",
        files={"file": ("rooms.csv", io.BytesIO(b"x" * (2 * 1024 * 1024 + 10)), "text/csv")},
        data={"mode": "add_update"},
    )
    assert resp.status_code == 413
    assert "2 MB" in resp.json()["detail"]


def test_a_workbook_with_too_many_rows_is_refused(client, monkeypatch):
    from app.bulk_import import parsing

    monkeypatch.setattr(parsing, "MAX_ROWS", 5)
    resp = client.post(
        "/api/bulk-import/rooms/preview",
        files={"file": ("rooms.xlsx", io.BytesIO(_xlsx(8)), "application/octet-stream")},
        data={"mode": "add_update"},
    )
    assert resp.status_code == 422
    assert "more than 5 rows" in resp.json()["detail"]


def test_workbook_xml_is_parsed_with_defusedxml():
    import openpyxl.xml

    assert openpyxl.xml.DEFUSEDXML is True


# --------------------------------------------------------------- downloads


def test_downloads_are_named_by_dataset_and_version_not_by_id(client, solved):  # noqa: F811
    run = solved["run"]
    for path in (f"/api/export/allocation.xlsx?run_id={run.id}", f"/api/export/run/{run.id}.xlsx"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        name = re.search(r'filename="([^"]+)"', resp.headers["content-disposition"]).group(1)
        assert name.endswith(f"_v{run.version}.xlsx"), name
        assert f"run-{run.id}" not in name and f"run_{run.id}" not in name, name


def test_the_master_pdf_is_labelled_by_version(client, solved):  # noqa: F811
    from app.routers import export

    db, run = solved["db"], solved["run"]
    label = export._version_label(db, run.id)
    assert label.startswith(f"Version {run.version} (draft) - ")
    assert "#" not in label


# ----------------------------------------------------------- in force


def test_the_published_version_stays_in_force_when_a_newer_draft_exists(client, solved):  # noqa: F811
    db, run, context = solved["db"], solved["run"], solved["context"]
    assert client.post(f"/api/runs/{run.id}/publish").status_code == 200
    _result, draft = generate(db, academic_context_id=context.id, soft=False, max_seconds=30)
    assert draft.version == run.version + 1

    ctx = {"academic_context_id": context.id}
    sample = db.query(Assignment).filter(Assignment.run_id == run.id).first()
    assert client.get("/api/timetable/master", params=ctx).json()["run_id"] == run.id
    assert client.get(f"/api/timetable/section/{sample.section_id}").json()["run_id"] == run.id
    assert client.get(f"/api/timetable/faculty/{sample.faculty_id}", params=ctx).json()["run_id"] == run.id
    allocation = client.get("/api/allocation", params=ctx).json()
    assert allocation["run_id"] == run.id
    assert allocation["publish_status"] == "PUBLISHED"
    assert allocation["newest_version"] == draft.version

    # The draft is still there to look at, by name.
    assert client.get("/api/timetable/master", params={"run_id": draft.id}).json()["run_id"] == draft.id

    # Publishing the draft moves every view to it.
    assert client.post(f"/api/runs/{draft.id}/publish").status_code == 200
    assert client.get("/api/allocation", params=ctx).json()["run_id"] == draft.id


def test_without_a_published_version_the_newest_is_shown(client, solved):  # noqa: F811
    db, run, context = solved["db"], solved["run"], solved["context"]
    _result, newer = generate(db, academic_context_id=context.id, soft=False, max_seconds=30)
    body = client.get("/api/allocation", params={"academic_context_id": context.id}).json()
    assert body["run_id"] == newer.id and body["newest_version"] == newer.version


# ----------------------------------------------------------- the figures


def test_the_dataset_figures_count_its_own_teachers_and_classes(client, solved):  # noqa: F811
    db, context = solved["db"], solved["context"]

    # A teacher from another upload who happens to be qualified for a subject
    # this dataset takes is not one of this dataset's teachers.
    stranger = Faculty(name="Other Upload", faculty_code="99999", is_active=True)
    stranger.subjects = list(solved["faculty"][0].subjects)
    db.add(stranger)
    db.commit()

    stats = validation.validate(db, academic_context_id=context.id).stats
    pinned = {
        f for (f,) in db.query(SectionSubjectAssignment.faculty_id)
        .filter(SectionSubjectAssignment.academic_context_id == context.id,
                SectionSubjectAssignment.faculty_id.isnot(None)).distinct()
    }
    assert pinned, "the fixture's solve should have recorded each class's teacher"
    assert stats["context_faculty"] == len(pinned)
    assert stranger.id not in pinned

    # Classes are sessions a week; required periods are those times length.
    # Fixture: 2401 takes CS201 x3 (1 period) + EC282 x1 (2 periods);
    # 2402 takes CS202 x2 (1 period) + EC282 x1 (2 periods).
    assert stats["context_classes"] == 3 + 1 + 2 + 1
    assert stats["context_required_periods"] == 3 + 2 + 2 + 2
    assert stats["context_required_periods"] == stats["total_periods_demanded"]


@pytest.mark.parametrize("ext", ["csv", "xlsx"])
def test_the_upload_extensions_still_accepted(client, ext):
    body = (b"block,floor,room_number,capacity,room_type\n36,1,101,60,theory\n"
            if ext == "csv" else _xlsx(1))
    resp = client.post(
        "/api/bulk-import/rooms/preview",
        files={"file": (f"rooms.{ext}", io.BytesIO(body), "application/octet-stream")},
        data={"mode": "add_update"},
    )
    assert resp.status_code == 200, resp.text


def test_a_lab_blocked_all_week_supplies_no_lab_periods(client, solved):  # noqa: F811
    from app.models import Room, RoomUnavailability, TimeSlot

    db, context = solved["db"], solved["context"]
    before = validation.validate(db, academic_context_id=context.id)
    assert next(c for c in before.checks if c.id == "lab_pressure").ok

    lab = db.query(Room).filter(Room.room_type == "lab").one()
    for slot in db.query(TimeSlot).filter(TimeSlot.is_lunch.is_(False)):
        db.add(RoomUnavailability(room_id=lab.id, timeslot_id=slot.id))
    db.commit()

    after = validation.validate(db, academic_context_id=context.id)
    assert not next(c for c in after.checks if c.id == "lab_pressure").ok
