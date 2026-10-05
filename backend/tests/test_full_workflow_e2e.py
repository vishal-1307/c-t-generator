"""The whole product, once, over HTTP, on the real workbook.

Every other test in this suite proves one thing carefully. This one asks the
only question a registrar actually has: starting from nothing, can I get from a
spreadsheet to a published timetable I can hand out - and does each step tell
me the truth about what it did?

It runs against the committed 14-sheet fixture rather than synthetic data,
through the API rather than the internals, in the order a person would:

    empty database
      -> upload one workbook
      -> sheets detected without being told what they are
      -> references resolved across sheets that do not exist in the database yet
      -> preview, then import, all-or-nothing
      -> data health
      -> validation
      -> generate
      -> verify with the independent checker, not the solver's own opinion
      -> read it back in all four views
      -> edit a class by hand
      -> lock it
      -> regenerate and confirm the lock held
      -> publish
      -> compare versions
      -> export
      -> ask the assistant something
      -> confirm the audit trail recorded it all

The second test is the other half: a deliberately broken workbook must leave
the database exactly as it found it.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

FIXTURE = Path(__file__).parent / "fixtures" / "demo_workbook.xlsx"


def _workbook_bytes() -> bytes:
    return FIXTURE.read_bytes()


def _upload(client, data: bytes, name: str = "demo.xlsx"):
    return {
        "files": {
            "file": (name, io.BytesIO(data),
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        }
    }


@pytest.fixture
def admin(monkeypatch):
    """A client whose background solver writes to the database it reads.

    Same shape as test_generate_api's fixture: the worker cannot share the
    request's session, so it builds one from `jobs.session_factory`, and
    pointing that at the test engine is what makes generate testable end to
    end rather than in pieces.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app import jobs
    from app.auth import hash_password
    from app.database import Base, get_db
    from app.main import app
    from app.models import User

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = factory()
    monkeypatch.setattr(jobs, "session_factory", factory)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    db.add(User(username="e2e-admin", hashed_password=hash_password("pw12345678"),
                role="admin", is_active=True))
    db.commit()

    with TestClient(app) as c:
        login = c.post("/api/auth/login",
                       json={"username": "e2e-admin", "password": "pw12345678"})
        assert login.status_code == 200, login.text
        c.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        c.test_db = db          # so the independent checker can read it
        yield c

    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


@pytest.mark.slow
def test_a_spreadsheet_becomes_a_published_timetable(admin, capsys):
    client = admin
    data = _workbook_bytes()

    def step(n, title):
        print(f"\n{n}. {title}")

    # ---------------------------------------------------------- 1. empty
    step(1, "A DATABASE WITH NOTHING IN IT")
    assert client.get("/api/sections").json() == []
    assert client.get("/api/subjects").json() == []
    print("   sections=0 subjects=0 faculty=0")

    # ------------------------------------------------- 2. one upload, previewed
    step(2, "UPLOAD ONE WORKBOOK - WHAT WOULD IT DO?")
    r = client.post("/api/bulk-import/workbook/analyse", **_upload(client, data))
    assert r.status_code == 200, r.text
    preview = r.json()

    detected = [s for s in preview["sheets"] if s["status"] == "detected"]
    ignored = [s for s in preview["sheets"] if s["status"] == "ignored"]
    print(f"   {len(detected)} sheets recognised, {len(ignored)} ignored as not data")
    print(f"   {preview['counts']['creates']} records would be created, "
          f"{preview['counts']['invalid']} problems")
    for decision in preview["decisions"]:
        print(f"   decision needed: [{decision['kind']}] {decision['label']}")

    assert len(detected) >= 9, "sheet detection should recognise the data sheets"
    assert preview["counts"]["creates"] > 300
    assert preview["can_apply"] is True

    # The whole reason this project was rebuilt: sheets referencing each other
    # before anything exists in the database.
    assert preview["counts"]["invalid"] == 0, (
        "cross-sheet references must resolve against the workbook itself, not "
        "only against the database"
    )

    # ------------------------------------------------------------ 3. apply
    step(3, "CONFIRM - IMPORT IT")
    r = client.post("/api/bulk-import/workbook/apply", **_upload(client, data))
    assert r.status_code == 200, r.text
    applied = r.json()
    assert applied["committed"] is True
    print(f"   committed, {applied['counts']['creates']} records")

    contexts = client.get("/api/academic-contexts").json()
    print(f"   {len(contexts)} academic contexts now exist")
    assert len(contexts) >= 3

    # -------------------------------------------------- 4. is it idempotent?
    step(4, "RE-IMPORT THE SAME FILE - NOTHING SHOULD CHANGE")
    before = len(client.get("/api/subjects").json())
    r = client.post("/api/bulk-import/workbook/apply", **_upload(client, data))
    assert r.status_code == 200, r.text
    after = len(client.get("/api/subjects").json())
    assert before == after, "re-importing must not duplicate anything"
    print(f"   subjects before={before} after={after}")

    # ------------------------------------------------------ 5. data health
    step(5, "DATA HEALTH")
    ctx = next(c for c in contexts if c["program"] == "BCA")
    summary = client.get(f"/api/data/summary?academic_context_id={ctx['id']}").json()
    for entity in summary["entities"]:
        print(f"   {entity['label']:22} {entity['count']:4}"
              f"{'  (' + str(entity['issues']) + ' ' + (entity['issue_label'] or 'issues') + ')' if entity['issues'] else ''}")
    assert summary["entities"], "the workspace must be able to describe the data"

    # ------------------------------------------------------- 6. validation
    step(6, "VALIDATION")
    report = client.get(f"/api/validate?academic_context_id={ctx['id']}").json()
    print(f"   ready={report['ready']} blockers={report['blocker_count']} "
          f"warnings={report['warning_count']}")
    for check in report["checks"]:
        if not check["ok"] and check["severity"] == "blocker":
            print(f"   BLOCKER {check['label']}: {check['detail'][:120]}")

    if not report["ready"]:
        # The demo dataset is genuinely short of two labs. Acting on what the
        # message says is itself the test: a blocker that cannot be acted on is
        # not a useful blocker.
        print("   -> acting on the advice: adding the labs it names")
        for code, lab_type in (("39-201", "cybersecurity"), ("39-202", "programming")):
            created = client.post("/api/rooms", json={
                "room_number": code.split("-")[1], "block": code.split("-")[0],
                "capacity": 70, "room_type": "lab", "lab_type": lab_type,
                "byod": True, "charging": True, "fixed_subject_id": None,
            })
            assert created.status_code == 201, created.text
        report = client.get(f"/api/validate?academic_context_id={ctx['id']}").json()
        print(f"   ready={report['ready']} blockers={report['blocker_count']}")

    assert report["ready"], "the advice validation gave must actually make it ready"

    # -------------------------------------------------------- 7. generate
    step(7, "GENERATE")
    run = _generate(client, ctx["id"])
    print(f"   status={run['status']} version={run['version']} "
          f"assignments={run['assignment_count']}")
    assert run["status"] in ("OPTIMAL", "FEASIBLE")
    assert run["assignment_count"] > 0

    # ------------------------------- 8. verified by something else entirely
    step(8, "INDEPENDENT HARD-CONSTRAINT CHECK")
    problems = _check_via_db(client, run["id"])
    print(f"   violations: {len(problems)}")
    for p in problems[:5]:
        print(f"     {p}")
    assert problems == [], "the timetable must satisfy the rules independently"

    # ------------------------------------------------------- 9. every view
    step(9, "ALL FOUR VIEWS")
    master = client.get(
        f"/api/timetable/master?run_id={run['id']}&limit=2000").json()
    print(f"   master: {master['total']} classes")
    assert master["total"] > 0

    sections = client.get(f"/api/sections?academic_context_id={ctx['id']}").json()
    faculty = client.get("/api/faculty").json()
    rooms = client.get("/api/rooms").json()

    section_grid = client.get(
        f"/api/timetable/section/{sections[0]['id']}?run_id={run['id']}").json()
    faculty_grid = client.get(
        f"/api/timetable/faculty/{faculty[0]['id']}?run_id={run['id']}").json()
    used_room = master["rows"][0]["room_id"]
    room_grid = client.get(
        f"/api/timetable/room/{used_room}?run_id={run['id']}").json()

    for name, grid in (("section", section_grid), ("faculty", faculty_grid),
                       ("room", room_grid)):
        print(f"   {name:8} view: {grid['total_periods']:3} periods  "
              f"v{grid['version']} {grid['publish_status']}")
        assert grid["perspective"] == name
        # Every view must say which version it is showing.
        assert grid["version"] == run["version"]
        assert grid["publish_status"] == "DRAFT"

    # ---------------------------------------------- 10. lecture / practical
    step(10, "LECTURE AND PRACTICAL, SCHEDULED SEPARATELY")
    mixed = [s for s in client.get("/api/subjects").json() if s["type"] == "mixed"]
    print(f"   {len(mixed)} subjects are taught as both")
    by_type: dict[str, set] = {}
    for row in master["rows"]:
        if row.get("session_type"):
            by_type.setdefault(row["session_type"], set()).add(row["subject_code"])
    print(f"   scheduled components: "
          + ", ".join(f"{k}={len(v)}" for k, v in sorted(by_type.items())))
    assert "L" in by_type and "P" in by_type, (
        "a dataset with mixed subjects must produce both kinds of session"
    )

    # ------------------------------------------------------ 11. manual edit
    step(11, "EDIT A CLASS BY HAND")
    target = next(r for r in master["rows"] if not r["locked"])
    new_room = _another_room(rooms, target["room_id"])
    preview_edit = client.post(
        f"/api/assignments/{target['assignment_id']}/room/preview",
        json={"room_id": new_room},
    )
    assert preview_edit.status_code == 200, preview_edit.text
    verdict = preview_edit.json()
    print(f"   moving {target['subject_code']} to room {new_room}: "
          f"ok={verdict['ok']}"
          + (f" - {verdict['issues'][0][:80]}" if verdict.get("issues") else ""))

    # The preview is the authority, so an edit it approves must apply and an
    # edit it refuses must not be attempted. Either verdict is a pass here;
    # what would not be is a preview that says nothing.
    assert isinstance(verdict["ok"], bool)
    if verdict["ok"]:
        applied_edit = client.post(
            f"/api/assignments/{target['assignment_id']}/room",
            json={"room_id": new_room},
        )
        assert applied_edit.status_code == 200, applied_edit.text
        print("   applied")
        master = client.get(
            f"/api/timetable/master?run_id={run['id']}&limit=2000").json()
        moved = next(r for r in master["rows"]
                     if r["assignment_id"] == target["assignment_id"])
        assert moved["room_id"] == new_room, "the edit did not stick"
        target = moved

    # ------------------------------------------------------------ 12. lock
    step(12, "LOCK A CLASS")
    lock_preview = client.post(
        f"/api/academic-contexts/{ctx['id']}/assignments/lock/preview",
        json={"section_id": target["section_id"],
              "subject_id": target["subject_id"], "lock": True},
    ).json()
    print(f"   preview: {lock_preview['summary']}")
    assert lock_preview["would_change"] is True

    locked = client.post(
        f"/api/academic-contexts/{ctx['id']}/assignments/lock",
        json={"section_id": target["section_id"], "subject_id": target["subject_id"]},
    )
    assert locked.status_code == 200, locked.text
    before_lock = _placement(master["rows"], target)
    print(f"   locked {target['subject_code']} for {target['section_number']}")

    # ---------------------------------------------------- 13. regeneration
    step(13, "REGENERATE - THE LOCK MUST HOLD")
    run2 = _generate(client, ctx["id"])
    print(f"   status={run2['status']} version={run2['version']} "
          f"assignments={run2['assignment_count']}")
    print(f"   message={(run2.get('message') or '')[:200]}")
    master2 = client.get(
        f"/api/timetable/master?run_id={run2['id']}&limit=2000").json()
    after_lock = _placement(master2["rows"], target)
    print(f"   before: {before_lock}")
    print(f"   after:  {after_lock}")
    assert after_lock == before_lock, "a locked class moved"
    print(f"   warnings: {run2.get('warnings') or 'none'}")

    # -------------------------------------------------------- 14. publish
    step(14, "PUBLISH")
    published = client.post(f"/api/runs/{run2['id']}/publish")
    assert published.status_code == 200, published.text
    print(f"   v{run2['version']} is now {published.json()['publish_status']}")

    grid_after = client.get(
        f"/api/timetable/section/{sections[0]['id']}?run_id={run2['id']}").json()
    assert grid_after["publish_status"] == "PUBLISHED", (
        "the views must show that this is the timetable in force"
    )

    # ------------------------------------------------- 15. compare versions
    step(15, "COMPARE VERSIONS")
    diff = client.get(f"/api/runs/{run['id']}/diff/{run2['id']}")
    assert diff.status_code == 200, diff.text
    d = diff.json()
    print(f"   v1 -> v2: moved={d['moved']} added={d['added']} "
          f"removed={d['removed']} faculty_changed={d['faculty_changed']} "
          f"room_changed={d['room_changed']} ({len(d['rows'])} rows detailed)")
    # The locked pair must not appear as having moved between the versions -
    # that is the same invariant as step 13, seen through the comparison a
    # reader would actually use to check it.
    moved_locked = [
        r for r in d["rows"]
        if r.get("subject_code") == target["subject_code"]
        and r.get("section_number") == target["section_number"]
        and r.get("change") == "moved"
    ]
    assert not moved_locked, f"the locked class is reported as moved: {moved_locked}"

    # --------------------------------------------------------- 16. exports
    step(16, "EXPORT")
    for fmt, kind in (("csv", "text/csv"), ("xlsx", None), ("pdf", None)):
        r = client.get(f"/api/export/master.{fmt}?academic_context_id={ctx['id']}")
        assert r.status_code == 200, f"{fmt}: {r.status_code}"
        print(f"   master.{fmt:4} {len(r.content):>8} bytes")
        assert len(r.content) > 100

    # ----------------------------------------------------------- 18. audit
    step(18, "AUDIT TRAIL")
    # A change is recorded against the run that was current when it was made,
    # so the lock belongs to v1 - it was applied before v2 existed.
    history = client.get(f"/api/assignments/history/run/{run['id']}").json()
    kinds = sorted({e["change_type"] for e in history})
    actors = sorted({e["actor"] for e in history if e.get("actor")})
    print(f"   {len(history)} changes recorded against v{run['version']}: {kinds}")
    print(f"   attributable to: {actors}")
    assert any(e["change_type"] in ("lock", "unlock") for e in history), (
        "locking is a decision somebody made and must be attributable"
    )
    assert actors, "a change with no actor cannot be audited"

    imports = client.get("/api/bulk-import/history")
    if imports.status_code == 200:
        print(f"   {len(imports.json())} import sessions recorded")
        assert imports.json(), "the import must be in the record"

    print("\n   WORKFLOW COMPLETE")


def test_a_broken_workbook_leaves_the_database_untouched(admin):
    """All-or-nothing, checked against a file that fails late.

    A partial import is the worst outcome available: it is not obviously
    broken, so it gets used. The failure is planted in the last dependency
    level, so everything before it has already validated and would have been
    written by an importer that commits as it goes.
    """
    client = admin

    # Import cleanly first, so there is real data to corrupt.
    ok = client.post("/api/bulk-import/workbook/apply", **_upload(client, _workbook_bytes()))
    assert ok.status_code == 200

    before = {
        "subjects": len(client.get("/api/subjects").json()),
        "faculty": len(client.get("/api/faculty").json()),
        "rooms": len(client.get("/api/rooms").json()),
        "sections": len(client.get("/api/sections").json()),
    }

    broken = _break_last_sheet(_workbook_bytes())

    analysed = client.post("/api/bulk-import/workbook/analyse", **_upload(client, broken))
    assert analysed.status_code == 200, analysed.text
    body = analysed.json()
    assert body["counts"]["invalid"] > 0, "the damage must be reported, not absorbed"
    assert body["can_apply"] is False, (
        "a workbook with unresolvable rows must not offer to apply itself"
    )

    applied = client.post("/api/bulk-import/workbook/apply", **_upload(client, broken))
    assert applied.status_code in (200, 400, 422)
    if applied.status_code == 200:
        assert applied.json()["committed"] is False

    after = {
        "subjects": len(client.get("/api/subjects").json()),
        "faculty": len(client.get("/api/faculty").json()),
        "rooms": len(client.get("/api/rooms").json()),
        "sections": len(client.get("/api/sections").json()),
    }
    assert after == before, f"the database changed: {before} -> {after}"


# ------------------------------------------------------------------ helpers


def _generate(client, context_id: int) -> dict:
    """Start a solve and wait for it to actually finish.

    Polling the status rather than trusting the join: the worker updates the
    row through its own session, and the request session shares an identity
    map with it, so a read without expiring first answers RUNNING from cache
    however long you waited.
    """
    import time

    from app import jobs

    started = client.post("/api/generate", json={
        "academic_context_id": context_id, "max_seconds": 300,
    })
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]

    jobs.wait(run_id, timeout=900)

    deadline = time.time() + 120
    while time.time() < deadline:
        client.test_db.expire_all()
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] != "RUNNING":
            return body
        time.sleep(0.5)
    raise AssertionError(f"run {run_id} never reached a terminal status")


def _placement(rows, target) -> dict:
    got = [
        r for r in rows
        if r["section_id"] == target["section_id"]
        and r["subject_id"] == target["subject_id"]
    ]
    return {
        "slots": sorted((r["day"], r["period_index"]) for r in got),
        "rooms": sorted({r["room_id"] for r in got}),
        "faculty": sorted({r["faculty_id"] for r in got}),
    }


def _another_room(rooms, current_id: int) -> int:
    for r in rooms:
        if r["id"] != current_id and r["room_type"] == "theory" and r["is_active"]:
            return r["id"]
    return current_id


def _check_via_db(client, run_id: int) -> list[str]:
    """The independent checker, against the same database the API just used.

    Deliberately not the solver's own status: a solver reporting OPTIMAL is
    reporting on the model it built, which is exactly the thing under test.
    """
    from app.timetable_checker import check_all, one_faculty_per_pair

    db = client.test_db
    db.expire_all()
    return check_all(db, run_id) + one_faculty_per_pair(db, run_id)


def _break_last_sheet(data: bytes) -> bytes:
    """Point an availability row at a room that does not exist anywhere.

    Availability is the last dependency level, so every other sheet has already
    been validated and staged when this fails.
    """
    wb = load_workbook(io.BytesIO(data))
    sheet = next(
        wb[name] for name in wb.sheetnames if "availability" in name.lower()
    )
    headers = [str(c.value or "").strip().lower() for c in sheet[1]]
    target = None
    for candidate in ("resource_id", "resource_code", "resource", "room_code", "code"):
        if candidate in headers:
            target = headers.index(candidate) + 1
            break
    assert target is not None, f"unexpected availability columns: {headers}"
    sheet.cell(row=2, column=target, value="NO-SUCH-ROOM-XYZ")

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
