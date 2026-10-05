"""`/api/data/summary` answers "is my data ready?" in one request.

Working that out previously meant visiting up to ten pages and counting by eye.
These tests pin what it reports, that it stays cheap, and - importantly - that
it does not quietly become a second opinion on whether generation can proceed.
`/api/validate` owns that verdict.
"""
from __future__ import annotations

from sqlalchemy import event


def _summary(client, **params):
    resp = client.get("/api/data/summary", params=params)
    assert resp.status_code == 200, resp.text
    return {e["key"]: e for e in resp.json()["entities"]}, resp.json()


def test_an_empty_database_reports_zeroes_and_a_missing_grid(client):
    by_key, body = _summary(client)
    assert by_key["faculty"]["count"] == 0
    assert by_key["sections"]["count"] == 0
    assert by_key["timeslots"]["count"] == 0
    assert by_key["timeslots"]["issues"] == 1
    assert "no weekly grid" in by_key["timeslots"]["issue_label"]
    assert body["total_issues"] >= 1


def test_faculty_without_a_subject_mapping_are_counted_as_an_issue(client):
    """A faculty member mapped to nothing can teach nothing - that is a data
    gap the workspace should surface before someone tries to generate."""
    for i in range(3):
        client.post("/api/faculty", json={
            "name": f"Dr {i}", "faculty_code": f"T-{i}", "department": "CSE",
        })
    by_key, _ = _summary(client)
    assert by_key["faculty"]["count"] == 3
    assert by_key["faculty"]["issues"] == 3
    assert "not mapped" in by_key["faculty"]["issue_label"]


def test_mapping_a_faculty_to_a_subject_clears_the_issue(client):
    fac = client.post("/api/faculty", json={
        "name": "Dr Rao", "faculty_code": "T-001", "department": "CSE",
    }).json()
    sub = client.post("/api/subjects", json={
        "name": "Python", "code": "CAP460", "type": "theory",
        "session_length_hours": 1, "sessions_per_week": 2,
    }).json()
    client.post(f"/api/faculty/{fac['id']}/subjects", json={"subject_id": sub["id"]})

    by_key, _ = _summary(client)
    assert by_key["faculty"]["issues"] == 0
    assert by_key["subjects"]["issues"] == 0


def test_a_subject_nobody_can_teach_is_an_issue(client):
    client.post("/api/subjects", json={
        "name": "Orphan", "code": "ORP101", "type": "theory",
        "session_length_hours": 1, "sessions_per_week": 1,
    })
    by_key, _ = _summary(client)
    assert by_key["subjects"]["issues"] == 1
    assert "no eligible faculty" in by_key["subjects"]["issue_label"]


def test_faculty_rooms_are_excluded_from_the_teaching_count(client):
    """"We have 200 rooms" is misleading when sixty of them are offices, and
    the solver already refuses to schedule into one."""
    client.post("/api/rooms", json={
        "room_number": "101", "block": "36", "capacity": 70, "room_type": "theory",
    })
    client.post("/api/rooms", json={
        "room_number": "L1", "block": "36", "capacity": 40,
        "room_type": "lab", "lab_type": "programming",
    })
    client.post("/api/rooms", json={
        "room_number": "F1", "block": "36", "capacity": 4, "room_type": "faculty",
    })

    by_key, _ = _summary(client)
    assert by_key["rooms"]["count"] == 2, "faculty rooms are not teaching space"
    assert "1 labs" in by_key["rooms"]["detail"]
    assert "faculty rooms excluded" in by_key["rooms"]["detail"]


def test_sections_without_a_curriculum_are_an_issue(client):
    ctx = client.post("/api/academic-contexts", json={
        "academic_year": "2026-27", "semester": 5,
        "program": "BCA", "department": "CSE",
    }).json()
    client.post("/api/sections", json={
        "academic_context_id": ctx["id"], "section_number": "D2401", "strength": 64,
    })
    by_key, _ = _summary(client, academic_context_id=ctx["id"])
    assert by_key["sections"]["count"] == 1
    assert by_key["sections"]["issues"] == 1
    assert "no subjects" in by_key["sections"]["issue_label"]


def test_section_counts_respect_the_academic_context(client):
    """Two contexts share faculty, subjects and rooms by design, but not
    sections - a scheduler working on one semester should not see the other's
    section count."""
    a = client.post("/api/academic-contexts", json={
        "academic_year": "2026-27", "semester": 5,
        "program": "BCA", "department": "CSE",
    }).json()
    b = client.post("/api/academic-contexts", json={
        "academic_year": "2026-27", "semester": 3,
        "program": "BCA", "department": "CSE",
    }).json()
    for i in range(3):
        client.post("/api/sections", json={
            "academic_context_id": a["id"], "section_number": f"A{i}", "strength": 60,
        })
    client.post("/api/sections", json={
        "academic_context_id": b["id"], "section_number": "B0", "strength": 60,
    })

    by_a, _ = _summary(client, academic_context_id=a["id"])
    by_b, _ = _summary(client, academic_context_id=b["id"])
    by_all, _ = _summary(client)
    assert by_a["sections"]["count"] == 3
    assert by_b["sections"]["count"] == 1
    assert by_all["sections"]["count"] == 4


def test_the_grid_reports_teachable_slots_separately_from_total(client):
    """Lunch is never scheduled, so a nine-period week is not nine teachable
    periods and the workspace should not imply that it is."""
    slots = client.post("/api/timeslots/seed", json={}).json()
    for slot in [s for s in slots if s["period_index"] == 4]:
        client.put(f"/api/timeslots/{slot['id']}", json={"is_lunch": True})

    by_key, _ = _summary(client)
    assert by_key["timeslots"]["count"] == 45
    assert by_key["timeslots"]["issues"] == 0
    assert "40 teachable" in by_key["timeslots"]["detail"]


def test_it_does_not_return_a_readiness_verdict(client):
    """Readiness belongs to /api/validate. Two sources of truth for "can I
    generate" would drift, and the wrong one would eventually be believed."""
    _, body = _summary(client)
    assert "ready" not in body
    assert "blockers" not in body


def test_the_summary_is_a_fixed_number_of_queries(client, db_session):
    """It loads on every visit to the workspace, so its cost must not grow
    with the size of the institution."""
    for i in range(15):
        client.post("/api/faculty", json={
            "name": f"Dr {i}", "faculty_code": f"T-{i:03d}", "department": "CSE",
        })

    statements: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        client.get("/api/data/summary")
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert all("SELECT" in s.upper() for s in statements)
    # One per figure reported; nothing per row.
    assert len(statements) <= 12, "\n".join(statements)
