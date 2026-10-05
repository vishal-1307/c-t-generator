"""The HTTP surface for generation and the timetable grids.

Generation is asynchronous because a solve at target scale takes tens of
seconds - longer than the idle timeout of most hosting platforms. So the
contract these tests pin down is: the request returns almost immediately with a
run id, and the client polls that id until it reaches a terminal status.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import jobs
from app.auth import hash_password
from app.database import Base, get_db
from app.grid import build_grid
from app.main import app
from app.models import AcademicContext, Faculty, Room, Section, Subject, User

from constraint_checker import check_all

TERMINAL = {"OPTIMAL", "FEASIBLE", "PARTIAL", "INFEASIBLE"}


@pytest.fixture
def api(monkeypatch):
    """A client whose background solver writes to the same database it does.

    The worker cannot share the request's session, so it builds its own from
    ``jobs.session_factory``. Pointing that at the test engine is what makes the
    async path testable end to end rather than only in pieces.
    """
    from fastapi.testclient import TestClient

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

    ctx = AcademicContext(academic_year="2026-27", semester=1, program="BCA", department="CSE")
    db.add(ctx)
    db.flush()

    subjects = {}
    for name, code, type_, length, spw in [
        ("Data Structures", "CS201", "theory", 1, 3),
        ("Operating Systems", "CS202", "theory", 1, 3),
        ("DS Lab", "CS251", "practical", 2, 1),
    ]:
        s = Subject(name=name, code=code, type=type_,
                    session_length_hours=length, sessions_per_week=spw)
        db.add(s)
        subjects[code] = s
    db.flush()

    rooms = {}
    for number, room_type, pin in [
        ("A101", "theory", None), ("A102", "theory", None), ("LAB1", "lab", "CS251"),
    ]:
        r = Room(room_number=number, block="A", floor="1", capacity=70, room_type=room_type,
                 fixed_subject_id=subjects[pin].id if pin else None)
        db.add(r)
        rooms[number] = r
    db.flush()

    for fname, fcode, teaches in [
        ("Dr. A", "FAC01", ["CS201", "CS202"]),
        ("Dr. B", "FAC02", ["CS201", "CS202"]),
        ("Dr. L", "FAC03", ["CS251"]),
    ]:
        f = Faculty(name=fname, faculty_code=fcode)
        f.subjects = [subjects[c] for c in teaches]
        db.add(f)

    for number in ("S-A", "S-B"):
        sec = Section(academic_context_id=ctx.id, section_number=number, strength=60)
        sec.subjects = list(subjects.values())
        db.add(sec)

    slots = build_grid()
    for s in slots:
        if s.period_index == 4:
            s.is_lunch = True
    db.add_all(slots)

    # Phase 10: every mutation endpoint this fixture exercises (generate,
    # publish, lock/unlock) is now authenticated - same pattern as
    # conftest.py's own `client` fixture.
    db.add(User(username="api-fixture-admin", hashed_password=hash_password("pw12345678"),
                role="admin", is_active=True))
    db.commit()

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "api-fixture-admin", "password": "pw12345678"})
        assert login.status_code == 200, login.text
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        yield {"client": client, "db": db, "context": ctx, "rooms": rooms, "subjects": subjects}

    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def run_to_completion(api, **body) -> dict:
    client = api["client"]
    body.setdefault("academic_context_id", api["context"].id)
    started = client.post("/api/generate", json=body)
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]
    jobs.wait(run_id, timeout=120)
    api["db"].expire_all()
    return client.get(f"/api/runs/{run_id}").json()


# ------------------------------------------------------------------- the async contract


def test_generate_returns_immediately_with_a_running_run(api):
    body = api["client"].post(
        "/api/generate", json={"academic_context_id": api["context"].id, "max_seconds": 20}
    ).json()
    assert body["status"] == "RUNNING"
    assert body["id"] > 0
    assert body["assignment_count"] == 0
    jobs.wait(body["id"], timeout=120)


def test_unknown_academic_context_is_404(api):
    resp = api["client"].post(
        "/api/generate", json={"academic_context_id": 9999, "max_seconds": 20}
    )
    assert resp.status_code == 404


def test_polling_reaches_a_terminal_status(api):
    run = run_to_completion(api, max_seconds=20)
    assert run["status"] in TERMINAL
    assert run["status"] != "RUNNING"
    assert run["solve_time_seconds"] >= 0


def test_successful_run_persists_its_assignments(api):
    """Regression: an earlier version wrote the result to a second run and
    re-pointed the rows, which the delete-orphan cascade then wiped."""
    run = run_to_completion(api, max_seconds=20)
    assert run["status"] in {"OPTIMAL", "FEASIBLE"}
    # 2 sections x (3 + 3 theory + 2 practical) = 16
    assert run["assignment_count"] == 16, run
    assert check_all(api["db"], run["id"]) == []


def test_run_carries_academic_context_and_version(api):
    run = run_to_completion(api, max_seconds=20)
    assert run["academic_context_id"] == api["context"].id
    assert run["version"] == 1
    assert run["publish_status"] == "DRAFT"


def test_optimal_is_flagged_as_proven(api):
    run = run_to_completion(api, max_seconds=30)
    assert run["proven_optimal"] is (run["status"] == "OPTIMAL")


def test_run_reports_weights_and_penalties(api):
    run = run_to_completion(api, max_seconds=20)
    assert set(run["weights"]) == {"gap", "spread", "repeat", "long_run", "proximity"}
    assert set(run["penalties"]) == {
        "gaps", "peak_faculty_load", "same_subject_repeats", "long_faculty_runs",
        "room_block_spread",
    }


def test_weights_can_be_overridden_through_the_api(api):
    run = run_to_completion(
        api, max_seconds=20, weights={"gap": 50, "spread": 2, "repeat": 1}
    )
    assert run["weights"]["gap"] == 50


def test_second_generate_while_running_is_rejected(api):
    """Two concurrent solves would race on the same database."""
    first = api["client"].post(
        "/api/generate", json={"academic_context_id": api["context"].id, "max_seconds": 20}
    ).json()
    second = api["client"].post(
        "/api/generate", json={"academic_context_id": api["context"].id, "max_seconds": 20}
    )
    assert second.status_code == 409
    assert "already being generated" in second.json()["detail"]
    jobs.wait(first["id"], timeout=120)


# ----------------------------------------------------------------- run history


def test_run_history_and_latest(api):
    run = run_to_completion(api, max_seconds=20)
    runs = api["client"].get("/api/runs").json()
    assert len(runs) >= 1
    assert runs[0]["id"] == run["id"]

    latest = api["client"].get("/api/runs/latest").json()
    assert latest["id"] == run["id"]


def test_unknown_run_is_404(api):
    assert api["client"].get("/api/runs/9999").status_code == 404


def test_latest_is_404_before_any_run(api):
    assert api["client"].get("/api/runs/latest").status_code == 404


def test_run_can_be_deleted(api):
    run = run_to_completion(api, max_seconds=20)
    assert api["client"].delete(f"/api/runs/{run['id']}").status_code == 204
    assert api["client"].get(f"/api/runs/{run['id']}").status_code == 404


# ------------------------------------------------------------- lifecycle: validate/publish


def test_run_can_be_validated_then_published(api):
    client = api["client"]
    run = run_to_completion(api, max_seconds=20)
    assert run["status"] in {"OPTIMAL", "FEASIBLE"}

    validated = client.post(f"/api/runs/{run['id']}/validate")
    assert validated.status_code == 200, validated.text
    assert validated.json()["publish_status"] == "VALIDATED"

    published = client.post(f"/api/runs/{run['id']}/publish")
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["publish_status"] == "PUBLISHED"
    assert body["published_at"] is not None


def test_publishing_a_new_run_archives_the_previous_publication(api):
    client = api["client"]
    context = api["context"]

    run1 = run_to_completion(api, max_seconds=20)
    client.post(f"/api/runs/{run1['id']}/validate")
    client.post(f"/api/runs/{run1['id']}/publish")

    run2 = run_to_completion(api, max_seconds=20)
    client.post(f"/api/runs/{run2['id']}/validate")
    published2 = client.post(f"/api/runs/{run2['id']}/publish")
    assert published2.status_code == 200

    archived1 = client.get(f"/api/runs/{run1['id']}").json()
    assert archived1["publish_status"] == "ARCHIVED"

    live2 = client.get(f"/api/runs/{run2['id']}").json()
    assert live2["publish_status"] == "PUBLISHED"
    assert context.id == run1["academic_context_id"] == run2["academic_context_id"]


def test_published_run_cannot_be_deleted(api):
    client = api["client"]
    run = run_to_completion(api, max_seconds=20)
    client.post(f"/api/runs/{run['id']}/validate")
    client.post(f"/api/runs/{run['id']}/publish")

    resp = client.delete(f"/api/runs/{run['id']}")
    assert resp.status_code == 409
    assert client.get(f"/api/runs/{run['id']}").status_code == 200


def test_publish_before_validate_is_allowed_from_draft(api):
    """The lifecycle permits DRAFT -> PUBLISHED directly, not only through
    VALIDATED - VALIDATED is a checkpoint, not a mandatory gate."""
    client = api["client"]
    run = run_to_completion(api, max_seconds=20)
    resp = client.post(f"/api/runs/{run['id']}/publish")
    assert resp.status_code == 200
    assert resp.json()["publish_status"] == "PUBLISHED"


def test_unsolved_run_cannot_be_validated(api):
    client = api["client"]
    api["subjects"]["CS202"].faculties = []
    api["db"].commit()
    run = run_to_completion(api, max_seconds=20)
    assert run["status"] == "INFEASIBLE"

    resp = client.post(f"/api/runs/{run['id']}/validate")
    assert resp.status_code == 409


# ----------------------------------------------------------------- lifecycle: locking


def test_assignment_can_be_locked_and_unlocked(api):
    client = api["client"]
    context = api["context"]
    run_to_completion(api, max_seconds=20)

    assignments = client.get(f"/api/academic-contexts/{context.id}/assignments").json()
    assert assignments, "generation should have persisted section-subject assignments"
    target = assignments[0]

    locked = client.post(
        f"/api/academic-contexts/{context.id}/assignments/lock",
        json={"section_id": target["section_id"], "subject_id": target["subject_id"]},
    )
    assert locked.status_code == 200
    assert locked.json()["locked"] is True

    unlocked = client.post(
        f"/api/academic-contexts/{context.id}/assignments/unlock",
        json={"section_id": target["section_id"], "subject_id": target["subject_id"]},
    )
    assert unlocked.status_code == 200
    assert unlocked.json()["locked"] is False


def test_locking_an_ungenerated_pair_is_404(api):
    client = api["client"]
    context = api["context"]
    section = client.get("/api/sections").json()[0]
    subject = client.get("/api/subjects").json()[0]

    resp = client.post(
        f"/api/academic-contexts/{context.id}/assignments/lock",
        json={"section_id": section["id"], "subject_id": subject["id"]},
    )
    assert resp.status_code == 404


def test_locked_assignment_survives_regeneration_through_the_api(api):
    client = api["client"]
    context = api["context"]
    run1 = run_to_completion(api, max_seconds=20)

    assignments = client.get(f"/api/academic-contexts/{context.id}/assignments").json()
    target = assignments[0]
    client.post(
        f"/api/academic-contexts/{context.id}/assignments/lock",
        json={"section_id": target["section_id"], "subject_id": target["subject_id"]},
    )

    run2 = run_to_completion(api, max_seconds=20)
    assert run2["id"] != run1["id"]

    after = client.get(f"/api/academic-contexts/{context.id}/assignments").json()
    same = next(
        a for a in after
        if a["section_id"] == target["section_id"] and a["subject_id"] == target["subject_id"]
    )
    assert same["faculty_id"] == target["faculty_id"]
    assert same["room_id"] == target["room_id"]
    assert same["locked"] is True


# ------------------------------------------------------------------ grid views


def test_section_grid_shape(api):
    run = run_to_completion(api, max_seconds=20)
    section = api["client"].get("/api/sections").json()[0]
    grid = api["client"].get(f"/api/timetable/section/{section['id']}").json()

    assert grid["perspective"] == "section"
    assert grid["title"] == section["section_number"]
    assert grid["run_id"] == run["id"]
    assert len(grid["rows"]) == 5, "Mon-Fri"
    assert len(grid["periods"]) == 9
    assert grid["total_periods"] == 8, "3 + 3 theory + 2 practical"


def test_lunch_column_is_marked_and_never_scheduled(api):
    run_to_completion(api, max_seconds=20)
    section = api["client"].get("/api/sections").json()[0]
    grid = api["client"].get(f"/api/timetable/section/{section['id']}").json()

    lunch_cells = [c for r in grid["rows"] for c in r["cells"] if c["is_lunch"]]
    assert len(lunch_cells) == 5, "one lunch period per day"
    assert all(c["subject_code"] is None for c in lunch_cells)


def test_multi_hour_block_is_returned_as_a_merged_cell(api):
    """The grid must hand the frontend a span rather than making it re-derive
    contiguity from the raw rows."""
    run_to_completion(api, max_seconds=20)
    section = api["client"].get("/api/sections").json()[0]
    grid = api["client"].get(f"/api/timetable/section/{section['id']}").json()

    spans = [c for r in grid["rows"] for c in r["cells"] if c["span"] > 1]
    assert len(spans) == 1, "one 2-hour practical"
    block = spans[0]
    assert block["span"] == 2
    assert block["subject_code"] == "CS251"
    assert block["room_number"] == "LAB1"

    # The second period of the block is a continuation, not a separate class.
    row = next(r for r in grid["rows"] if any(c is block or c == block for c in r["cells"]))
    following = next(
        c for c in row["cells"] if c["period_index"] == block["period_index"] + 1
    )
    assert following["continuation"] is True
    assert following["span"] == 0
    assert following["block_id"] == block["block_id"]


def test_faculty_grid_shows_every_section_they_teach(api):
    run_to_completion(api, max_seconds=20)
    faculty = api["client"].get("/api/faculty").json()
    lab_teacher = next(f for f in faculty if f["faculty_code"] == "FAC03")

    grid = api["client"].get(f"/api/timetable/faculty/{lab_teacher['id']}").json()
    assert grid["perspective"] == "faculty"
    sections = {
        c["section_number"] for r in grid["rows"] for c in r["cells"]
        if c["section_number"]
    }
    assert sections == {"S-A", "S-B"}, "sole lab teacher serves both sections"


def test_room_grid_shows_cross_section_use(api):
    run_to_completion(api, max_seconds=20)
    lab = next(
        r for r in api["client"].get("/api/rooms").json() if r["room_number"] == "LAB1"
    )
    grid = api["client"].get(f"/api/timetable/room/{lab['id']}").json()
    assert grid["perspective"] == "room"
    assert grid["total_periods"] == 4, "2 sections x one 2-hour session"


def test_grid_404s_for_unknown_ids(api):
    run_to_completion(api, max_seconds=20)
    client = api["client"]
    assert client.get("/api/timetable/section/9999").status_code == 404
    assert client.get("/api/timetable/faculty/9999").status_code == 404
    assert client.get("/api/timetable/room/9999").status_code == 404
    assert client.get("/api/timetable/section/1?run_id=9999").status_code == 404


def test_grid_404s_before_any_run(api):
    section = api["client"].get("/api/sections").json()[0]
    r = api["client"].get(f"/api/timetable/section/{section['id']}")
    assert r.status_code == 404
    assert "generated" in r.json()["detail"]


# --------------------------------------------------------------------- master view


def test_master_view_lists_every_assignment(api):
    run = run_to_completion(api, max_seconds=20)
    master = api["client"].get("/api/timetable/master").json()
    assert master["run_id"] == run["id"]
    assert master["total"] == 16
    assert len(master["rows"]) == 16


def test_master_view_filters_by_block(api):
    run_to_completion(api, max_seconds=20)
    master = api["client"].get("/api/timetable/master?block=A").json()
    assert master["total"] > 0
    assert all(r["block"] == "A" for r in master["rows"])

    none_match = api["client"].get("/api/timetable/master?block=ZZZ").json()
    assert none_match["total"] == 0


def test_master_view_filters_by_academic_context_fields(api):
    context = api["context"]
    run_to_completion(api, max_seconds=20)

    matching = api["client"].get(
        f"/api/timetable/master?academic_year={context.academic_year}&semester={context.semester}"
        f"&program={context.program}&department={context.department}"
    ).json()
    assert matching["total"] == 16

    non_matching = api["client"].get(
        "/api/timetable/master?academic_year=1999-00&semester=1&program=BCA&department=CSE"
    ).json()
    assert non_matching["total"] == 0


def test_master_view_filters_by_locked_state(api):
    client = api["client"]
    context = api["context"]
    run_to_completion(api, max_seconds=20)

    assignments = client.get(f"/api/academic-contexts/{context.id}/assignments").json()
    target = assignments[0]
    client.post(
        f"/api/academic-contexts/{context.id}/assignments/lock",
        json={"section_id": target["section_id"], "subject_id": target["subject_id"]},
    )

    locked_rows = client.get("/api/timetable/master?locked=true").json()
    assert locked_rows["total"] >= 1
    assert all(r["locked"] for r in locked_rows["rows"])

    unlocked_rows = client.get("/api/timetable/master?locked=false").json()
    assert unlocked_rows["total"] + locked_rows["total"] == 16


# ------------------------------------------------------- infeasible over HTTP


def test_infeasible_input_returns_200_with_an_explanation(api):
    """A data problem is not a server error - the client must get the report."""
    db = api["db"]
    api["subjects"]["CS202"].faculties = []
    db.commit()

    run = run_to_completion(api, max_seconds=20)
    assert run["status"] == "INFEASIBLE"
    assert run["assignment_count"] == 0
    assert "must be fixed" in run["message"], run["message"]

    # The blocker detail is available from the validation endpoint.
    report = api["client"].get(
        f"/api/validate?academic_context_id={api['context'].id}"
    ).json()
    assert report["ready"] is False
    assert any("CS202" in c["detail"] for c in report["checks"] if not c["ok"])
