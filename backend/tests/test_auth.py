"""Phase 10: authentication and role-based authorization.

The default `client` fixture is pre-authenticated as admin (see conftest.py's
own docstring for why) - every test here deliberately swaps that out, via
`anon_client` (no token at all) or `login_as` (a fresh user of a specific
role), to prove the actual protection holds rather than assuming it from the
route wiring alone.
"""
from __future__ import annotations

from app.models import Faculty, Room, User
from conftest import TEST_ADMIN_PASSWORD, login_as


# --------------------------------------------------------------------- login


def test_login_succeeds_with_correct_credentials(client):
    resp = client.post("/api/auth/login", json={"username": "test-admin", "password": TEST_ADMIN_PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["username"] == "test-admin"
    assert body["user"]["role"] == "admin"
    assert len(body["access_token"]) > 20


def test_login_fails_with_wrong_password(client):
    resp = client.post("/api/auth/login", json={"username": "test-admin", "password": "wrong"})
    assert resp.status_code == 401


def test_login_fails_for_unknown_username(client):
    resp = client.post("/api/auth/login", json={"username": "nobody", "password": "whatever"})
    assert resp.status_code == 401


def test_login_error_message_does_not_reveal_which_part_was_wrong(client):
    """A real security property, not incidental: the same message for a bad
    username and a bad password, so login errors can't be used to enumerate
    valid accounts."""
    wrong_user = client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    wrong_pass = client.post("/api/auth/login", json={"username": "test-admin", "password": "x"})
    assert wrong_user.json()["detail"] == wrong_pass.json()["detail"]


def test_disabled_account_cannot_log_in(client, db_session):
    from app.auth import hash_password

    db_session.add(User(username="disabled-user", hashed_password=hash_password("pw12345678"),
                         role="viewer", is_active=False))
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": "disabled-user", "password": "pw12345678"})
    assert resp.status_code == 403


def test_me_returns_the_authenticated_user(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["username"] == "test-admin"


def test_me_requires_a_token(anon_client):
    resp = anon_client.get("/api/auth/me")
    assert resp.status_code == 401


def test_invalid_token_is_rejected(anon_client):
    anon_client.headers["Authorization"] = "Bearer not-a-real-token"
    resp = anon_client.get("/api/auth/me")
    assert resp.status_code == 401


# ------------------------------------------------------------ write protection


def test_creating_a_room_requires_authentication(anon_client):
    resp = anon_client.post("/api/rooms", json={
        "room_number": "X1", "block": "X", "capacity": 60, "room_type": "theory",
    })
    assert resp.status_code == 401


def test_creating_a_room_as_viewer_is_forbidden(client, db_session):
    login_as(client, db_session, username="a-viewer", role="viewer")
    resp = client.post("/api/rooms", json={
        "room_number": "X1", "block": "X", "capacity": 60, "room_type": "theory",
    })
    assert resp.status_code == 403
    assert "admin" in resp.json()["detail"]


def test_creating_a_room_as_faculty_is_forbidden(client, db_session):
    login_as(client, db_session, username="a-faculty", role="faculty")
    resp = client.post("/api/rooms", json={
        "room_number": "X1", "block": "X", "capacity": 60, "room_type": "theory",
    })
    assert resp.status_code == 403


def test_creating_a_room_as_scheduler_is_forbidden_reference_data_is_admin_only(client, db_session):
    """Spec PART 13: 'manage all reference data' is listed only under Admin,
    not Scheduler."""
    login_as(client, db_session, username="a-scheduler", role="scheduler")
    resp = client.post("/api/rooms", json={
        "room_number": "X1", "block": "X", "capacity": 60, "room_type": "theory",
    })
    assert resp.status_code == 403


def test_creating_a_room_as_admin_succeeds(client, db_session):
    login_as(client, db_session, username="an-admin", role="admin")
    resp = client.post("/api/rooms", json={
        "room_number": "X1", "block": "X", "capacity": 60, "room_type": "theory",
    })
    assert resp.status_code == 201


def test_reading_rooms_needs_no_authentication(anon_client, db_session):
    """The deliberate scope decision (auth.py's own docstring): GET stays
    open. Timetable/reference-data reads are not the risk surface."""
    resp = anon_client.get("/api/rooms")
    assert resp.status_code == 200


def test_generate_requires_scheduler_or_admin(client, db_session, context):
    login_as(client, db_session, username="a-viewer2", role="viewer")
    resp = client.post("/api/generate", json={"academic_context_id": context.id})
    assert resp.status_code == 403


def test_generate_as_scheduler_is_allowed_past_the_auth_check(client, db_session, context):
    """Scheduler is authorized for generation (spec PART 13) - assert the
    request gets *past* the 403/401 layer, not that generation itself
    succeeds (there is no curriculum in this fixture, so it will 4xx for an
    unrelated, expected reason)."""
    login_as(client, db_session, username="a-scheduler2", role="scheduler")
    resp = client.post("/api/generate", json={"academic_context_id": context.id})
    assert resp.status_code != 401
    assert resp.status_code != 403


def test_bulk_import_requires_admin_not_scheduler(client, db_session):
    import io

    login_as(client, db_session, username="a-scheduler3", role="scheduler")
    resp = client.post(
        "/api/bulk-import/rooms/preview",
        files={"file": ("r.csv", io.BytesIO(b"room_number,capacity,room_type\nA1,60,theory\n"), "text/csv")},
        data={"mode": "add_update"},
    )
    assert resp.status_code == 403


def test_manual_move_preview_requires_scheduler_or_admin(client, db_session):
    login_as(client, db_session, username="a-viewer3", role="viewer")
    resp = client.post("/api/assignments/1/move/preview", json={"target_timeslot_id": 1})
    assert resp.status_code == 403


def test_publish_requires_scheduler_or_admin(client, db_session):
    login_as(client, db_session, username="a-viewer4", role="viewer")
    resp = client.post("/api/runs/1/publish")
    assert resp.status_code == 403


# ------------------------------------------------------------- user management


def test_only_admin_can_list_users(client, db_session):
    login_as(client, db_session, username="a-scheduler4", role="scheduler")
    resp = client.get("/api/auth/users")
    assert resp.status_code == 403


def test_admin_can_create_a_user(client, db_session):
    login_as(client, db_session, username="an-admin2", role="admin")
    resp = client.post("/api/auth/users", json={
        "username": "new-scheduler", "password": "pw12345678", "role": "scheduler",
    })
    assert resp.status_code == 201
    assert resp.json()["role"] == "scheduler"


def test_duplicate_username_is_rejected(client, db_session):
    login_as(client, db_session, username="an-admin3", role="admin")
    client.post("/api/auth/users", json={"username": "dup", "password": "pw12345678", "role": "viewer"})
    resp = client.post("/api/auth/users", json={"username": "dup", "password": "pw12345678", "role": "viewer"})
    assert resp.status_code == 409


def test_admin_cannot_deactivate_their_own_account(client, db_session):
    me = client.get("/api/auth/me").json()
    resp = client.put(f"/api/auth/users/{me['id']}", json={"is_active": False})
    assert resp.status_code == 422


def test_admin_cannot_demote_their_own_account(client, db_session):
    me = client.get("/api/auth/me").json()
    resp = client.put(f"/api/auth/users/{me['id']}", json={"role": "viewer"})
    assert resp.status_code == 422


def test_faculty_user_can_be_linked_to_a_faculty_row(client, db_session):
    fac = Faculty(name="Dr. Link", faculty_code="FAC-LINK")
    db_session.add(fac)
    db_session.commit()
    resp = client.post("/api/auth/users", json={
        "username": "dr-link", "password": "pw12345678", "role": "faculty", "faculty_id": fac.id,
    })
    assert resp.status_code == 201
    assert resp.json()["faculty_id"] == fac.id


# --------------------------------------------------------------- passwords


def test_password_is_never_stored_in_plain_text(client, db_session):
    login_as(client, db_session, username="an-admin4", role="admin")
    client.post("/api/auth/users", json={
        "username": "plaintext-check", "password": "my-real-password-1", "role": "viewer",
    })
    row = db_session.query(User).filter(User.username == "plaintext-check").one()
    assert row.hashed_password != "my-real-password-1"
    assert row.hashed_password.startswith("$2b$")


def test_password_hash_is_unique_per_user_even_with_the_same_password(client, db_session):
    """A real bcrypt property (random salt) - proves the hash isn't a naive
    fixed-salt or unsalted scheme."""
    login_as(client, db_session, username="an-admin5", role="admin")
    client.post("/api/auth/users", json={"username": "same-pw-1", "password": "identical-password-1", "role": "viewer"})
    client.post("/api/auth/users", json={"username": "same-pw-2", "password": "identical-password-1", "role": "viewer"})
    u1 = db_session.query(User).filter(User.username == "same-pw-1").one()
    u2 = db_session.query(User).filter(User.username == "same-pw-2").one()
    assert u1.hashed_password != u2.hashed_password
