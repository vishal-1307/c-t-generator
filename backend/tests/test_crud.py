"""CRUD endpoints, mapping endpoints, academic context, and grid seeding."""
from __future__ import annotations


def mk_context(client, year="2026-27", semester=1, program="BCA", department="CSE"):
    r = client.post(
        "/api/academic-contexts",
        json={
            "academic_year": year,
            "semester": semester,
            "program": program,
            "department": department,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def mk_subject(client, code="CS201", type_="theory", length=1, spw=3, required_lab_type=None):
    r = client.post(
        "/api/subjects",
        json={
            "name": f"Subject {code}",
            "code": code,
            "type": type_,
            "session_length_hours": length,
            "sessions_per_week": spw,
            "required_lab_type": required_lab_type,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def mk_room(client, number="A101", room_type="theory", capacity=70, lab_type=None, fixed=None):
    r = client.post(
        "/api/rooms",
        json={
            "room_number": number,
            "block": "A",
            "floor": "1",
            "capacity": capacity,
            "room_type": room_type,
            "lab_type": lab_type,
            "fixed_subject_id": fixed,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def mk_faculty(client, code="FAC01", department=None):
    r = client.post(
        "/api/faculty", json={"name": f"Dr. {code}", "faculty_code": code, "department": department}
    )
    assert r.status_code == 201, r.text
    return r.json()


def mk_section(client, context_id, number="CSE-3A", strength=60):
    r = client.post(
        "/api/sections",
        json={"academic_context_id": context_id, "section_number": number, "strength": strength},
    )
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------- basics


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] in {"sqlite", "postgresql", "postgresql+psycopg"}


def test_subject_lifecycle(client):
    subj = mk_subject(client)
    assert subj["periods_per_week"] == 3

    got = client.get(f"/api/subjects/{subj['id']}").json()
    assert got["code"] == "CS201"

    upd = client.put(f"/api/subjects/{subj['id']}", json={"sessions_per_week": 5}).json()
    assert upd["sessions_per_week"] == 5
    assert upd["periods_per_week"] == 5

    assert client.delete(f"/api/subjects/{subj['id']}").status_code == 204
    assert client.get(f"/api/subjects/{subj['id']}").status_code == 404


def test_duplicate_code_is_409(client):
    mk_subject(client, code="CS201")
    r = client.post(
        "/api/subjects",
        json={"name": "Other", "code": "CS201", "type": "theory",
              "session_length_hours": 1, "sessions_per_week": 1},
    )
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


def test_invalid_enum_and_range_rejected(client, context):
    bad_type = client.post(
        "/api/subjects",
        json={"name": "X", "code": "X1", "type": "tutorial",
              "session_length_hours": 1, "sessions_per_week": 1},
    )
    assert bad_type.status_code == 422

    bad_length = client.post(
        "/api/subjects",
        json={"name": "X", "code": "X2", "type": "theory",
              "session_length_hours": 4, "sessions_per_week": 1},
    )
    assert bad_length.status_code == 422

    bad_strength = client.post(
        "/api/sections",
        json={"academic_context_id": context.id, "section_number": "S", "strength": 0},
    )
    assert bad_strength.status_code == 422


# ------------------------------------------------------------- academic context


def test_academic_context_lifecycle(client):
    ctx = mk_context(client)
    assert ctx["label"] == "2026-27 / Sem 1 / BCA (CSE)"

    got = client.get(f"/api/academic-contexts/{ctx['id']}").json()
    assert got["academic_year"] == "2026-27"

    assert client.delete(f"/api/academic-contexts/{ctx['id']}").status_code == 204
    assert client.get(f"/api/academic-contexts/{ctx['id']}").status_code == 404


def test_duplicate_academic_context_is_409(client):
    mk_context(client)
    r = client.post(
        "/api/academic-contexts",
        json={"academic_year": "2026-27", "semester": 1, "program": "BCA", "department": "CSE"},
    )
    assert r.status_code == 409


def test_same_section_number_allowed_in_different_contexts(client):
    """D2402 in Sem 1 and D2402 in Sem 3 are different sections entirely."""
    ctx1 = mk_context(client, semester=1)
    ctx3 = mk_context(client, semester=3)

    s1 = mk_section(client, ctx1["id"], number="D2402")
    s3 = mk_section(client, ctx3["id"], number="D2402")
    assert s1["id"] != s3["id"]

    # But the same number twice in the SAME context is rejected.
    dup = client.post(
        "/api/sections",
        json={"academic_context_id": ctx1["id"], "section_number": "D2402", "strength": 50},
    )
    assert dup.status_code == 409


def test_sections_filtered_by_context(client):
    ctx1 = mk_context(client, semester=1)
    ctx3 = mk_context(client, semester=3)
    mk_section(client, ctx1["id"], number="A")
    mk_section(client, ctx3["id"], number="B")

    only_ctx1 = client.get(f"/api/sections?academic_context_id={ctx1['id']}").json()
    assert [s["section_number"] for s in only_ctx1] == ["A"]


# ------------------------------------------------------------------- mappings


def test_faculty_subject_mapping(client):
    fac = mk_faculty(client)
    subj = mk_subject(client)

    mapped = client.post(
        f"/api/faculty/{fac['id']}/subjects", json={"subject_id": subj["id"]}
    ).json()
    assert [s["code"] for s in mapped["subjects"]] == ["CS201"]

    dup = client.post(
        f"/api/faculty/{fac['id']}/subjects", json={"subject_id": subj["id"]}
    )
    assert dup.status_code == 409

    unmapped = client.delete(
        f"/api/faculty/{fac['id']}/subjects/{subj['id']}"
    ).json()
    assert unmapped["subjects"] == []

    assert client.delete(
        f"/api/faculty/{fac['id']}/subjects/{subj['id']}"
    ).status_code == 404


def test_section_subject_mapping(client, context):
    section = mk_section(client, context.id)
    subj = mk_subject(client)

    mapped = client.post(
        f"/api/sections/{section['id']}/subjects", json={"subject_id": subj["id"]}
    ).json()
    assert [s["code"] for s in mapped["subjects"]] == ["CS201"]

    dup = client.post(
        f"/api/sections/{section['id']}/subjects", json={"subject_id": subj["id"]}
    )
    assert dup.status_code == 409

    removed = client.delete(
        f"/api/sections/{section['id']}/subjects/{subj['id']}"
    ).json()
    assert removed["subjects"] == []

    assert client.delete(
        f"/api/sections/{section['id']}/subjects/{subj['id']}"
    ).status_code == 404

    # Unknown ids must 404 rather than silently no-op.
    assert client.post(
        f"/api/sections/{section['id']}/subjects", json={"subject_id": 9999}
    ).status_code == 404
    assert client.post(
        "/api/sections/9999/subjects", json={"subject_id": subj["id"]}
    ).status_code == 404


# ------------------------------------------------------------------------ rooms


def test_lab_pinning(client):
    lab_subj = mk_subject(client, code="CS251", type_="practical", length=2, spw=1)
    lab = mk_room(client, number="LAB1", room_type="lab")

    pinned = client.put(
        f"/api/rooms/{lab['id']}/fixed-subject", json={"subject_id": lab_subj["id"]}
    ).json()
    assert pinned["fixed_subject"]["code"] == "CS251"

    unpinned = client.put(
        f"/api/rooms/{lab['id']}/fixed-subject", json={"subject_id": None}
    ).json()
    assert unpinned["fixed_subject"] is None


def test_practical_cannot_be_pinned_to_theory_room(client):
    """A practical pinned to a theory room could never be scheduled anywhere."""
    lab_subj = mk_subject(client, code="CS251", type_="practical", length=2, spw=1)
    theory_room = mk_room(client, number="A101", room_type="theory")

    r = client.put(
        f"/api/rooms/{theory_room['id']}/fixed-subject",
        json={"subject_id": lab_subj["id"]},
    )
    assert r.status_code == 422
    assert "lab rooms" in r.json()["detail"]


def test_faculty_room_cannot_be_pinned_to_any_subject(client):
    subj = mk_subject(client, code="CS201")
    faculty_room = mk_room(client, number="F-201", room_type="faculty", capacity=1)

    r = client.put(
        f"/api/rooms/{faculty_room['id']}/fixed-subject", json={"subject_id": subj["id"]}
    )
    assert r.status_code == 422


def test_flipping_room_to_faculty_rejects_existing_pin(client):
    """Regression-shaped: the guard must apply on update, not just create."""
    lab_subj = mk_subject(client, code="CS251", type_="practical", length=2, spw=1)
    lab = mk_room(client, number="LAB1", room_type="lab")
    client.put(f"/api/rooms/{lab['id']}/fixed-subject", json={"subject_id": lab_subj["id"]})

    r = client.put(f"/api/rooms/{lab['id']}", json={"room_type": "faculty"})
    assert r.status_code == 422


def test_room_lab_type_and_floor_round_trip(client):
    room = mk_room(client, number="LAB1", room_type="lab", lab_type="ELECTRONICS")
    assert room["lab_type"] == "ELECTRONICS"
    assert room["floor"] == "1"
    assert room["is_active"] is True


def test_room_can_be_deactivated(client):
    room = mk_room(client, number="A101")
    updated = client.put(f"/api/rooms/{room['id']}", json={"is_active": False}).json()
    assert updated["is_active"] is False


# ---------------------------------------------------------------- availability


def test_faculty_unavailability_crud(client):
    fac = mk_faculty(client)
    slot = client.post(
        "/api/timeslots",
        json={"day": "Monday", "day_index": 0, "period_index": 0,
              "start_time": "09:30:00", "end_time": "10:20:00", "is_lunch": False},
    ).json()

    blocked = client.post(
        f"/api/faculty/{fac['id']}/unavailability",
        json={"timeslot_id": slot["id"], "reason": "Conference"},
    )
    assert blocked.status_code == 201
    block_id = blocked.json()["id"]

    listed = client.get(f"/api/faculty/{fac['id']}/unavailability").json()
    assert len(listed) == 1
    assert listed[0]["reason"] == "Conference"

    dup = client.post(
        f"/api/faculty/{fac['id']}/unavailability", json={"timeslot_id": slot["id"]}
    )
    assert dup.status_code == 409

    assert client.delete(
        f"/api/faculty/{fac['id']}/unavailability/{block_id}"
    ).status_code == 204
    assert client.get(f"/api/faculty/{fac['id']}/unavailability").json() == []


def test_room_and_section_unavailability_crud(client, context):
    room = mk_room(client)
    section = mk_section(client, context.id)
    slot = client.post(
        "/api/timeslots",
        json={"day": "Monday", "day_index": 0, "period_index": 0,
              "start_time": "09:30:00", "end_time": "10:20:00", "is_lunch": False},
    ).json()

    r1 = client.post(f"/api/rooms/{room['id']}/unavailability", json={"timeslot_id": slot["id"]})
    assert r1.status_code == 201
    r2 = client.post(f"/api/sections/{section['id']}/unavailability", json={"timeslot_id": slot["id"]})
    assert r2.status_code == 201

    assert len(client.get(f"/api/rooms/{room['id']}/unavailability").json()) == 1
    assert len(client.get(f"/api/sections/{section['id']}/unavailability").json()) == 1


# ------------------------------------------------------------------ timeslots


def test_seed_grid_shape(client):
    """Default grid: Monday-Friday ONLY, 9 x 50min from 09:30, ending at 17:00."""
    slots = client.post("/api/timeslots/seed", json={}).json()
    assert len(slots) == 45  # 5 days x 9 periods

    days = {s["day"] for s in slots}
    assert days == {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}
    assert "Saturday" not in days
    assert "Sunday" not in days

    # Every generated slot is teachable; lunch is a manual toggle.
    assert all(not s["is_lunch"] for s in slots)

    monday = [s for s in slots if s["day_index"] == 0]
    assert len(monday) == 9
    assert [s["period_index"] for s in monday] == list(range(9))

    # The exact schedule requested, with no gaps between periods.
    assert [(s["start_time"][:5], s["end_time"][:5]) for s in monday] == [
        ("09:30", "10:20"),
        ("10:20", "11:10"),
        ("11:10", "12:00"),
        ("12:00", "12:50"),
        ("12:50", "13:40"),
        ("13:40", "14:30"),
        ("14:30", "15:20"),
        ("15:20", "16:10"),
        ("16:10", "17:00"),
    ]
    # Each period starts where the previous ended.
    for a, b in zip(monday, monday[1:]):
        assert a["end_time"] == b["start_time"]


def test_lunch_is_a_manual_per_slot_toggle(client):
    """The generator never decides where lunch falls; the admin marks it."""
    slots = client.post("/api/timeslots/seed", json={}).json()
    assert all(not s["is_lunch"] for s in slots)

    # Mark the 12:50 period as lunch on Monday only.
    target = next(
        s for s in slots if s["day_index"] == 0 and s["start_time"][:5] == "12:50"
    )
    updated = client.put(f"/api/timeslots/{target['id']}", json={"is_lunch": True}).json()
    assert updated["is_lunch"] is True
    assert updated["start_time"][:5] == "12:50"

    after = client.get("/api/timeslots").json()
    assert len([s for s in after if s["is_lunch"]]) == 1
    assert len([s for s in after if not s["is_lunch"]]) == 44

    # And it can be turned back off.
    client.put(f"/api/timeslots/{target['id']}", json={"is_lunch": False})
    assert all(not s["is_lunch"] for s in client.get("/api/timeslots").json())


def test_seed_is_idempotent(client):
    """Re-seeding the same grid produces the same grid.

    The second call now needs `confirm`, because replacing an existing grid
    cascades into availability blocks and generated classes. The idempotence
    being asserted here is unchanged - only the ceremony in front of it.
    """
    first = client.post("/api/timeslots/seed", json={}).json()
    second = client.post("/api/timeslots/seed", json={"confirm": True}).json()
    assert len(first) == len(second) == 45
    assert len(client.get("/api/timeslots").json()) == 45


def test_seed_custom_grid(client):
    slots = client.post(
        "/api/timeslots/seed",
        json={
            "days": ["Monday", "Tuesday"],
            "periods": 5,
            "start_hour": 8,
            "start_minute": 15,
            "period_minutes": 45,
        },
    ).json()
    assert len(slots) == 10  # 2 days x 5 periods
    monday = [s for s in slots if s["day_index"] == 0]
    assert monday[0]["start_time"][:5] == "08:15"
    assert monday[-1]["end_time"][:5] == "12:00"  # 08:15 + 5*45min


def test_seed_can_still_include_saturday_explicitly(client):
    """Saturday is not the default, but a future configuration can still ask
    for it explicitly - the grid builder itself is not hard-restricted."""
    slots = client.post(
        "/api/timeslots/seed",
        json={"days": ["Monday", "Saturday"], "periods": 3},
    ).json()
    days = {s["day"] for s in slots}
    assert days == {"Monday", "Saturday"}


def test_duplicate_slot_rejected(client):
    payload = {
        "day": "Monday", "day_index": 0, "period_index": 0,
        "start_time": "09:00:00", "end_time": "10:00:00", "is_lunch": False,
    }
    assert client.post("/api/timeslots", json=payload).status_code == 201
    assert client.post("/api/timeslots", json=payload).status_code == 409
