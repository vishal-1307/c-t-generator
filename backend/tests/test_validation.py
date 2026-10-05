"""Regression tests for the audit findings, updated for the Phase 4 schema.

Each test here corresponds to a bug found by auditing phases 1-2, or to a
validation-surface change made necessary by removing the per-section "home
room" concept (rooms now come from an eligible pool - block/floor/room_type/
lab_type/capacity - rather than one fixed classroom per section). They exist
so the false-green readiness indicators cannot come back.
"""
from __future__ import annotations

from test_crud import mk_context, mk_faculty, mk_room, mk_section, mk_subject


def check(client, check_id: str, academic_context_id: int | None = None) -> dict:
    """Fetch one readiness check by id."""
    url = "/api/validate"
    if academic_context_id is not None:
        url += f"?academic_context_id={academic_context_id}"
    report = client.get(url).json()
    found = [c for c in report["checks"] if c["id"] == check_id]
    assert found, f"no check with id {check_id!r}; got {[c['id'] for c in report['checks']]}"
    return found[0]


def full_dataset(client, strength=60, lab_capacity=70, theory_capacity=70):
    """A minimal but complete, solvable dataset."""
    ctx = mk_context(client)
    theory = mk_subject(client, code="CS201", type_="theory", length=1, spw=3)
    practical = mk_subject(client, code="CS251", type_="practical", length=2, spw=1)
    theory_room = mk_room(client, number="A101", room_type="theory", capacity=theory_capacity)
    lab = mk_room(client, number="LAB1", room_type="lab", capacity=lab_capacity)
    client.put(f"/api/rooms/{lab['id']}/fixed-subject", json={"subject_id": practical["id"]})
    fac = mk_faculty(client, code="FAC01")
    for s in (theory, practical):
        client.post(f"/api/faculty/{fac['id']}/subjects", json={"subject_id": s["id"]})
    section = mk_section(client, ctx["id"], strength=strength)
    for s in (theory, practical):
        client.post(f"/api/sections/{section['id']}/subjects", json={"subject_id": s["id"]})
    client.post("/api/timeslots/seed", json={})
    return {
        "context": ctx, "theory": theory, "practical": practical, "theory_room": theory_room,
        "lab": lab, "faculty": fac, "section": section,
    }


def test_complete_dataset_is_ready(client):
    full_dataset(client)
    report = client.get("/api/validate").json()
    failing = [c["label"] for c in report["checks"] if not c["ok"]]
    assert report["ready"] is True, f"unexpected blockers: {failing}"
    assert report["blocker_count"] == 0


def test_validate_scopes_to_one_academic_context(client):
    """A blocker in one semester's data must not fail a readiness check run
    for a different semester (spec: validation is scoped per context)."""
    healthy = full_dataset(client)
    broken_ctx = mk_context(client, semester=2)
    theory = mk_subject(client, code="MA201", type_="theory")
    section = mk_section(client, broken_ctx["id"], number="BROKEN", strength=60)
    client.post(f"/api/sections/{section['id']}/subjects", json={"subject_id": theory["id"]})
    # MA201 has no faculty mapped in this context - a genuine blocker there.

    healthy_report = client.get(
        f"/api/validate?academic_context_id={healthy['context']['id']}"
    ).json()
    assert healthy_report["ready"] is True, healthy_report["checks"]

    broken_report = client.get(
        f"/api/validate?academic_context_id={broken_ctx['id']}"
    ).json()
    assert broken_report["ready"] is False


# ------------------------------------------------------- BUG 1: empty grid


def test_empty_grid_does_not_report_demand_fits(client):
    """Regression: `teachable > 0 &&` made a zero-slot grid report 'all fit'."""
    full_dataset(client)
    # full_dataset already seeded a grid, so emptying it is a *replacement* and
    # needs confirming - the guard exists because that cascade is destructive.
    client.post("/api/timeslots/seed", json={"days": [], "confirm": True})

    assert client.get("/api/timeslots").json() == []
    assert check(client, "grid_exists")["ok"] is False
    demand = check(client, "demand_fits_grid")
    assert demand["ok"] is False, "zero slots but demand reported as fitting"
    assert "grid has 0" in demand["detail"]


# ------------------------------------ BUG 2: FK enforcement / dangling refs


def test_deleting_subject_nulls_room_pin(client):
    data = full_dataset(client)
    assert client.delete(f"/api/subjects/{data['practical']['id']}").status_code == 204

    lab = client.get(f"/api/rooms/{data['lab']['id']}").json()
    assert lab["fixed_subject_id"] is None


def test_foreign_keys_reject_a_bad_reference(client, db_session):
    """With enforcement on, a dangling reference cannot even be written."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.models import Room

    data = full_dataset(client)
    room = db_session.get(Room, data["lab"]["id"])
    room.fixed_subject_id = 9999
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# --------------------------------------------- BUG 3: room pinning (reworked)
#
# Under the old "one home room per section" model, pinning a theory subject to
# a room was always wrong and rejected outright. Under the eligible-room-pool
# model, Room.fixed_subject_id is a legitimate override for ANY subject type
# (spec #15) - it is just wasteful for theory, which normally draws from the
# shared pool, so that case is now a *warning*, not a hard rejection. Faculty
# rooms remain a hard rejection regardless of subject type - they are never
# schedulable teaching rooms.


def test_theory_subject_can_be_pinned_but_is_flagged_as_a_warning(client):
    theory = mk_subject(client, code="CS201", type_="theory")
    lab = mk_room(client, number="LAB1", room_type="lab")

    r = client.put(f"/api/rooms/{lab['id']}/fixed-subject", json={"subject_id": theory["id"]})
    assert r.status_code == 200, r.text

    pinned = check(client, "no_theory_pinned")
    assert pinned["ok"] is False
    assert pinned["severity"] == "warning"
    assert "CS201" in pinned["detail"]


def test_practical_cannot_be_pinned_to_a_non_lab_room(client):
    practical = mk_subject(client, code="CS251", type_="practical", length=2, spw=1)
    theory_room = mk_room(client, number="A101", room_type="theory")

    r = client.put(
        f"/api/rooms/{theory_room['id']}/fixed-subject", json={"subject_id": practical["id"]}
    )
    assert r.status_code == 422
    assert "lab" in r.json()["detail"].lower()


def test_no_subject_can_be_pinned_to_a_faculty_room(client):
    theory = mk_subject(client, code="CS201", type_="theory")
    faculty_room = mk_room(client, number="A-F1", room_type="faculty", capacity=2)

    r = client.put(
        f"/api/rooms/{faculty_room['id']}/fixed-subject", json={"subject_id": theory["id"]}
    )
    assert r.status_code == 422
    assert "faculty room" in r.json()["detail"].lower()


def test_changing_room_type_revalidates_existing_pin(client):
    """Regression: update_room only validated when fixed_subject_id was in the
    payload, so flipping type lab->theory left a practical pinned to a
    now-non-lab room."""
    practical = mk_subject(client, code="CS251", type_="practical", length=2, spw=1)
    lab = mk_room(client, number="LAB1", room_type="lab")
    client.put(f"/api/rooms/{lab['id']}/fixed-subject", json={"subject_id": practical["id"]})

    r = client.put(f"/api/rooms/{lab['id']}", json={"room_type": "theory"})
    assert r.status_code == 422
    assert "lab" in r.json()["detail"].lower()


# ---------------------------------------------------- BUG 4: room capacity


def test_lab_too_small_for_section_is_a_blocker(client):
    """Regression: 'every practical has a lab' ignored capacity, so a 30-seat
    lab for a 65-student section reported green. Now surfaced through the
    merged eligible-room check, since labs are no longer a separate concept
    from the theory-room capacity check."""
    full_dataset(client, strength=65, lab_capacity=30)

    room_check = check(client, "pairs_have_eligible_room")
    assert room_check["ok"] is False, "undersized lab reported as fine"
    assert "CS251" in room_check["detail"]
    assert "30" in room_check["detail"] and "65" in room_check["detail"]


def test_undersized_theory_room_pool_is_a_blocker(client):
    """The theory-room analogue of the lab-capacity regression above: no
    per-section home room to fall back on any more, so an undersized pool is
    the only way theory capacity can fail."""
    full_dataset(client, strength=65, theory_capacity=20)

    room_check = check(client, "pairs_have_eligible_room")
    assert room_check["ok"] is False
    assert "CS201" in room_check["detail"]
    assert "20" in room_check["detail"] and "65" in room_check["detail"]


def test_no_active_theory_rooms_is_a_blocker(client):
    data = full_dataset(client)
    client.put(f"/api/rooms/{data['theory_room']['id']}", json={"is_active": False})

    theory_check = check(client, "theory_rooms_exist")
    assert theory_check["ok"] is False


# ------------------------------------------- BUG 5: unmapped-subject breadth


def test_subject_no_section_takes_is_not_a_blocker(client):
    """Regression: an unmapped subject nobody takes produced a false RED."""
    full_dataset(client)
    mk_subject(client, code="ZZ999", type_="theory")  # orphan, no section, no faculty

    subj_check = check(client, "subjects_have_faculty")
    assert subj_check["ok"] is True, f"orphan subject wrongly flagged: {subj_check['detail']}"


def test_unmapped_subject_that_is_taken_is_a_blocker(client):
    data = full_dataset(client)
    fac, theory = data["faculty"], data["theory"]
    client.delete(f"/api/faculty/{fac['id']}/subjects/{theory['id']}")

    subj_check = check(client, "subjects_have_faculty")
    assert subj_check["ok"] is False
    assert "CS201" in subj_check["detail"]


# ----------------------------------------------------- additional coverage


def test_lab_pressure_detects_insufficient_labs(client):
    """The user's own example: not enough lab rooms for this many practicals."""
    ctx = mk_context(client)
    practical = mk_subject(client, code="CS251", type_="practical", length=3, spw=5)
    lab = mk_room(client, number="LAB1", room_type="lab", capacity=70)
    client.put(f"/api/rooms/{lab['id']}/fixed-subject", json={"subject_id": practical["id"]})
    fac = mk_faculty(client)
    client.post(f"/api/faculty/{fac['id']}/subjects", json={"subject_id": practical["id"]})
    # 6 sections x 15 periods = 90 lab-periods, but one lab offers only 42.
    for i in range(6):
        sec = mk_section(client, ctx["id"], number=f"S{i}", strength=50)
        client.post(f"/api/sections/{sec['id']}/subjects", json={"subject_id": practical["id"]})
    client.post("/api/timeslots/seed", json={})

    pressure = check(client, "lab_pressure")
    assert pressure["ok"] is False
    # The demand appears, so the reader can see the size of the gap. Asserted
    # as a number rather than as a sentence, since the wording is meant to be
    # improvable without breaking this.
    assert "90" in pressure["detail"], pressure["detail"]
    assert pressure["offenders"], "the shortage must be listed, not only described"


def test_a_surplus_of_one_lab_type_does_not_cover_a_shortage_of_another(client):
    """Lab pools are not interchangeable, so they cannot be added up.

    The solver matches lab_type exactly, as a hard constraint. Summed across
    types, a room nobody can use offsets a room everybody needs, and the
    timetable is declared possible when it is not. Found on the real demo
    workbook, which has one cybersecurity lab and plenty of others.
    """
    ctx = mk_context(client)
    fac = mk_faculty(client)
    scarce = mk_subject(client, code="CY251", type_="practical", length=2, spw=3,
                        required_lab_type="cybersecurity")
    client.post(f"/api/faculty/{fac['id']}/subjects", json={"subject_id": scarce["id"]})

    # One lab of the type that is needed; four of a type nothing asks for.
    mk_room(client, number="CY1", room_type="lab", capacity=70,
            lab_type="cybersecurity")
    for i in range(4):
        mk_room(client, number=f"EL{i}", room_type="lab", capacity=70,
                lab_type="electronics")

    for i in range(8):
        sec = mk_section(client, ctx["id"], number=f"S{i}", strength=60)
        client.post(f"/api/sections/{sec['id']}/subjects",
                    json={"subject_id": scarce["id"]})
    client.post("/api/timeslots/seed", json={})

    pressure = check(client, "lab_pressure", ctx["id"])
    assert pressure["ok"] is False, (
        "the electronics labs cannot host a cybersecurity practical, so they "
        "must not count towards its supply"
    )
    assert "cybersecurity" in pressure["detail"], pressure["detail"]
    assert "electronics" not in pressure["detail"], (
        "naming a type that is not short sends someone to add the wrong room"
    )


def test_a_lab_of_the_right_type_that_still_cannot_be_used_says_why(client):
    """A shortage that reads as "build another lab" invites building one just
    as unusable as the last. The reason is what makes it advice."""
    ctx = mk_context(client)
    fac = mk_faculty(client)
    subject = mk_subject(client, code="CY251", type_="practical", length=2, spw=3,
                         required_lab_type="cybersecurity")
    client.post(f"/api/faculty/{fac['id']}/subjects", json={"subject_id": subject["id"]})

    mk_room(client, number="CY1", room_type="lab", capacity=70,
            lab_type="cybersecurity")
    # Right type, too small for any of these sections.
    mk_room(client, number="CY2", room_type="lab", capacity=20,
            lab_type="cybersecurity")

    for i in range(8):
        sec = mk_section(client, ctx["id"], number=f"S{i}", strength=60)
        client.post(f"/api/sections/{sec['id']}/subjects",
                    json={"subject_id": subject["id"]})
    client.post("/api/timeslots/seed", json={})

    pressure = check(client, "lab_pressure", ctx["id"])
    assert pressure["ok"] is False
    detail = pressure["detail"]
    assert "60" in detail and "seat" in detail, (
        f"the message must say the room is too small, and for whom: {detail}"
    )


def test_multi_hour_block_needs_contiguous_window(client):
    """A 3-hour block is impossible if lunch splits every run of 3 periods."""
    data = full_dataset(client)
    client.put(
        f"/api/subjects/{data['practical']['id']}", json={"session_length_hours": 3}
    )
    # 4 periods/day is enough for a 3-hour block...
    # Replaces the grid full_dataset seeded, hence `confirm`.
    slots = client.post(
        "/api/timeslots/seed", json={"periods": 4, "confirm": True}
    ).json()
    assert check(client, "multi_hour_blocks_fit")["ok"] is True

    # ...until lunch is marked in the middle, splitting every day into runs of
    # length 1 and 2. This exercises the real per-slot toggle, not a generator flag.
    for slot in [s for s in slots if s["period_index"] == 1]:
        client.put(f"/api/timeslots/{slot['id']}", json={"is_lunch": True})

    blocks = check(client, "multi_hour_blocks_fit")
    assert blocks["ok"] is False
    assert "contiguous" in blocks["detail"]

    # Moving lunch to the end of the day restores a 3-period run.
    for slot in [s for s in slots if s["period_index"] == 1]:
        client.put(f"/api/timeslots/{slot['id']}", json={"is_lunch": False})
    for slot in [s for s in slots if s["period_index"] == 3]:
        client.put(f"/api/timeslots/{slot['id']}", json={"is_lunch": True})
    assert check(client, "multi_hour_blocks_fit")["ok"] is True


def test_sole_faculty_overload_detected(client):
    ctx = mk_context(client)
    practical = mk_subject(client, code="CS251", type_="practical", length=3, spw=6)
    lab = mk_room(client, number="LAB1", room_type="lab", capacity=70)
    client.put(f"/api/rooms/{lab['id']}/fixed-subject", json={"subject_id": practical["id"]})
    fac = mk_faculty(client, code="SOLO")
    client.post(f"/api/faculty/{fac['id']}/subjects", json={"subject_id": practical["id"]})
    for i in range(4):
        sec = mk_section(client, ctx["id"], number=f"S{i}", strength=50)
        client.post(f"/api/sections/{sec['id']}/subjects", json={"subject_id": practical["id"]})
    client.post("/api/timeslots/seed", json={})

    overload = check(client, "faculty_not_overloaded")
    assert overload["ok"] is False
    assert "sole teacher" in overload["detail"]
