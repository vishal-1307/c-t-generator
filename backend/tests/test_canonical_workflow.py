"""Phase 10 PART 21: the canonical end-to-end workflow.

Create context -> import time slots -> rooms -> faculty -> subjects ->
faculty<->subject -> sections -> section<->subject -> availability ->
validate -> generate -> verify -> manual change -> lock -> regenerate ->
verify locked item preserved -> publish -> export.

Every setup step goes through the real HTTP import endpoints an admin would
actually use - not hand-built ORM rows - because the point of this test is
to prove the *whole pipeline* works together, including the dependency
validation between steps (PART 11).

Uses a bespoke fixture (not conftest.py's `client`) for the same reason
test_generate_api.py does: the async /api/generate endpoint runs its solve
on a background thread that builds its own DB session from
``app.jobs.session_factory`` - that factory has to point at this test's
own engine, or the worker silently writes to a different database than the
test polls.
"""
from __future__ import annotations

import io
import os
import tempfile
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import jobs
from app.auth import hash_password
from app.database import Base, get_db
from app.main import app
from app.models import User

from constraint_checker import check_all


@pytest.fixture
def workflow(monkeypatch):
    # A file-backed database with normal pooling, NOT the in-memory StaticPool
    # used elsewhere in this suite.
    #
    # StaticPool hands the *same* DBAPI connection to every session. That is
    # harmless when one session does all the work, but this test drives the
    # async path: a background solver thread commits on its own session while
    # the client keeps issuing requests. Sharing one connection makes those
    # sessions interleave BEGIN/COMMIT on the same transaction - a request can
    # then read a half-written run, or end the worker's transaction under it.
    # Both were observed here as intermittent failures that looked like the
    # solver losing a locked assignment.
    #
    # A file database gives every session its own connection, which is exactly
    # what production has (SessionLocal over a normal pool). The concurrency
    # this test exercises is then the real thing rather than an artefact.
    handle, db_path = tempfile.mkstemp(suffix=".db", prefix="timetable-workflow-")
    os.close(handle)
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = factory()

    monkeypatch.setattr(jobs, "session_factory", factory)

    def override_get_db():
        """One session per request, exactly as production's get_db does.

        This fixture used to yield a single long-lived session to every
        request. That is the one place the harness differed from production,
        and it mattered: the background solver commits on its own session, and
        a request session that has been open since before that commit can keep
        serving a stale view of the run - reporting a regenerated timetable as
        partially or entirely empty. Production never sees this, because
        get_db() builds a fresh session per request and closes it after.
        """
        request_db = factory()
        try:
            yield request_db
        finally:
            request_db.close()

    app.dependency_overrides[get_db] = override_get_db

    db.add(User(username="workflow-admin", hashed_password=hash_password("pw12345678"),
                role="admin", is_active=True))
    db.commit()

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "workflow-admin", "password": "pw12345678"})
        assert login.status_code == 200, login.text
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        yield {"client": client, "db": db}

    app.dependency_overrides.clear()
    db.close()
    engine.dispose()
    # Windows keeps a handle briefly after dispose(); a leftover temp file is
    # not worth failing a passing test over.
    try:
        os.remove(db_path)
    except OSError:
        pass


def _import(client, entity: str, csv_text: str):
    resp = client.post(
        f"/api/bulk-import/{entity}/apply",
        files={"file": (f"{entity}.csv", io.BytesIO(csv_text.encode()), "text/csv")},
        data={"mode": "add_update"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True, body
    return body


def _wait_for_terminal(client, run_id: int, timeout_s: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] != "RUNNING":
            return run
        time.sleep(0.25)
    raise AssertionError(f"run {run_id} did not reach a terminal status within {timeout_s}s")


def test_canonical_workflow_end_to_end(workflow):
    client, db = workflow["client"], workflow["db"]

    # 1. Academic context
    ctx_resp = client.post("/api/academic-contexts", json={
        "academic_year": "2026-27", "semester": 1, "program": "BCA", "department": "CSE",
    })
    assert ctx_resp.status_code == 201, ctx_resp.text
    ctx = ctx_resp.json()

    # 2. Time slots (Mon-Fri with one lunch slot/day - seeded, the canonical
    # way to create the grid, then one slot flipped to lunch).
    seed_resp = client.post("/api/timeslots/seed", json={})
    assert seed_resp.status_code == 200, seed_resp.text
    for s in seed_resp.json():
        if s["period_index"] == 4:
            client.put(f"/api/timeslots/{s['id']}", json={"is_lunch": True})

    # 3. Rooms
    _import(client, "rooms", (
        "block,floor,room_number,capacity,room_type,lab_type,is_active\n"
        "A,1,A101,70,theory,,true\n"
        "A,1,A102,70,theory,,true\n"
        "C,1,LAB1,70,lab,COMPUTING,true\n"
    ))

    # 4. Faculty
    _import(client, "faculty", (
        "faculty_id,name,department,is_active\n"
        "T-1,Dr. A. Sharma,CSE,true\n"
        "T-2,Dr. B. Rao,CSE,true\n"
        "T-3,Dr. C. Menon,CSE,true\n"
    ))

    # 5. Subjects
    _import(client, "subjects", (
        "subject_code,subject_name,type,sessions_per_week,duration_slots,required_lab_type,is_active\n"
        "CS201,Data Structures,theory,3,1,,true\n"
        "CS202,Operating Systems,theory,3,1,,true\n"
        "CS251,DS Lab,practical,1,2,COMPUTING,true\n"
    ))

    # 6. Faculty <-> Subject
    _import(client, "faculty_subjects", (
        "faculty_id,subject_code\n"
        "T-1,CS201\nT-2,CS201\nT-2,CS202\nT-1,CS202\nT-3,CS251\n"
    ))

    # 7. Sections
    _import(client, "sections", (
        "section_number,academic_year,semester,program,department,strength,is_active\n"
        "CSE-3A,2026-27,1,BCA,CSE,60,true\n"
        "CSE-3B,2026-27,1,BCA,CSE,55,true\n"
    ))

    # 8. Section <-> Subject
    _import(client, "section_subjects", (
        "academic_year,semester,program,department,section_number,subject_code\n"
        "2026-27,1,BCA,CSE,CSE-3A,CS201\n"
        "2026-27,1,BCA,CSE,CSE-3A,CS202\n"
        "2026-27,1,BCA,CSE,CSE-3A,CS251\n"
        "2026-27,1,BCA,CSE,CSE-3B,CS201\n"
        "2026-27,1,BCA,CSE,CSE-3B,CS202\n"
        "2026-27,1,BCA,CSE,CSE-3B,CS251\n"
    ))

    # 9. Availability - block one faculty member's first slot Monday
    _import(client, "availability", (
        "entity_type,entity_identifier,day,slot,reason\n"
        "faculty,T-1,Monday,1,Department meeting\n"
    ))

    # 10. Validate
    validate_resp = client.get(f"/api/validate?academic_context_id={ctx['id']}")
    assert validate_resp.status_code == 200
    report = validate_resp.json()
    assert report["ready"] is True, report["checks"]

    # 11. Generate
    gen_resp = client.post("/api/generate", json={"academic_context_id": ctx["id"], "max_seconds": 20})
    assert gen_resp.status_code == 202, gen_resp.text
    run = _wait_for_terminal(client, gen_resp.json()["id"])
    assert run["status"] in ("OPTIMAL", "FEASIBLE"), run
    run_id = run["id"]

    # 12. Verify timetable: every hard constraint holds, and the blocked
    # slot is genuinely respected.
    db.expire_all()   # the worker committed on another session
    assert check_all(db, run_id) == []

    master = client.get(f"/api/timetable/master?run_id={run_id}&limit=500").json()
    assert master["total"] > 0
    for row in master["rows"]:
        if row["faculty_code"] == "T-1":
            assert not (row["day"] == "Monday" and row["period_index"] == 0), (
                "T-1's declared-unavailable Monday P1 must never be scheduled"
            )

    # 13. Manual change: move one CS201/CSE-3A class to a different free slot.
    a = next(r for r in master["rows"] if r["section_number"] == "CSE-3A" and r["subject_code"] == "CS201")
    all_slots = client.get("/api/timeslots").json()
    occupied = {(r["day"], r["period_index"]) for r in master["rows"]}
    target = next(
        s for s in all_slots
        if not s["is_lunch"] and (s["day"], s["period_index"]) not in occupied
    )
    move_preview = client.post(
        f"/api/assignments/{a['assignment_id']}/move/preview",
        json={"target_timeslot_id": target["id"]},
    )
    assert move_preview.status_code == 200
    if move_preview.json()["ok"]:
        move_resp = client.post(
            f"/api/assignments/{a['assignment_id']}/move",
            json={"target_timeslot_id": target["id"]},
        )
        assert move_resp.status_code == 200, move_resp.text
        assert move_resp.json()["ok"] is True

    # 14. Lock CSE-3A/CS201 so regeneration cannot move it.
    lock_resp = client.post(
        f"/api/academic-contexts/{ctx['id']}/assignments/lock",
        json={"section_id": a["section_id"], "subject_id": a["subject_id"]},
    )
    assert lock_resp.status_code == 200, lock_resp.text
    locked_room_id = lock_resp.json()["room_id"]
    locked_faculty_id = lock_resp.json()["faculty_id"]

    before_master = client.get(f"/api/timetable/master?run_id={run_id}&limit=500").json()
    locked_slots_before = sorted(
        (r["day"], r["period_index"])
        for r in before_master["rows"]
        if r["section_number"] == "CSE-3A" and r["subject_code"] == "CS201"
    )

    # 15. Regenerate.
    gen2_resp = client.post("/api/generate", json={"academic_context_id": ctx["id"], "max_seconds": 20})
    assert gen2_resp.status_code == 202, gen2_resp.text
    run2 = _wait_for_terminal(client, gen2_resp.json()["id"])
    assert run2["status"] in ("OPTIMAL", "FEASIBLE"), run2
    run2_id = run2["id"]

    # 16. Verify the locked item survived regeneration exactly.
    after_master = client.get(f"/api/timetable/master?run_id={run2_id}&limit=500").json()
    locked_rows_after = [
        r for r in after_master["rows"]
        if r["section_number"] == "CSE-3A" and r["subject_code"] == "CS201"
    ]
    locked_slots_after = sorted((r["day"], r["period_index"]) for r in locked_rows_after)
    assert locked_slots_after == locked_slots_before, "locked assignment's slots must survive regeneration exactly"
    assert all(r["room_id"] == locked_room_id for r in locked_rows_after)
    assert all(r["faculty_id"] == locked_faculty_id for r in locked_rows_after)

    db.expire_all()   # the worker committed on another session
    assert check_all(db, run2_id) == []

    # 17. Publish.
    publish_resp = client.post(f"/api/runs/{run2_id}/publish")
    assert publish_resp.status_code == 200, publish_resp.text
    assert publish_resp.json()["publish_status"] == "PUBLISHED"

    # 18. Export - the published run is exportable in every format.
    csv_resp = client.get(f"/api/export/master.csv?run_id={run2_id}")
    assert csv_resp.status_code == 200
    assert len(csv_resp.text) > 0

    xlsx_resp = client.get(f"/api/export/master.xlsx?run_id={run2_id}")
    assert xlsx_resp.status_code == 200
    assert xlsx_resp.content[:2] == b"PK"  # a real zip/xlsx container

    pdf_resp = client.get(f"/api/export/master.pdf?run_id={run2_id}")
    assert pdf_resp.status_code == 200
    assert pdf_resp.content[:4] == b"%PDF"
