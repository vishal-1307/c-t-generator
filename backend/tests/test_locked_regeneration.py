"""Locked assignments must survive regeneration exactly.

The product rule: once a (section, subject) is locked, regeneration reproduces
it with the same faculty, room, day and slot(s) until someone unlocks it.

These tests exist because `test_canonical_workflow_end_to_end` failed
intermittently with the locked pair having *no* rows at all after
regeneration. The cause was not the lock: `_persist` set the run's terminal
status before writing its assignment rows, so a reader that polls for a
terminal status and then reads the timetable could land in between and see an
empty run. See `test_a_terminal_run_is_never_visible_without_its_assignments`.

The rest of this file pins the locking rule itself, which that bug made look
broken.
"""
from __future__ import annotations

import time

import pytest

from app.models import SectionSubjectAssignment
from app.solver.data import load

# Reuses the async-capable fixture: its worker writes to the same database the
# client reads, which is what makes regeneration testable end to end.
from test_generate_api import api, run_to_completion  # noqa: F401


# --------------------------------------------------------------- helpers

def _master(api, run_id):
    r = api["client"].get(f"/api/timetable/master?run_id={run_id}&limit=2000")
    assert r.status_code == 200, r.text
    body = r.json()
    # A silent page cut would make these assertions meaningless.
    assert body["total"] == len(body["rows"]), "master paginated; widen the limit"
    return body["rows"]


def _rows_for(rows, section_number, subject_code):
    return [
        r for r in rows
        if r["section_number"] == section_number and r["subject_code"] == subject_code
    ]


def _placement(rows, section_number, subject_code):
    """Everything the invariant covers, in a comparable form."""
    got = _rows_for(rows, section_number, subject_code)
    return {
        "slots": sorted((r["day"], r["period_index"]) for r in got),
        "rooms": sorted({r["room_id"] for r in got}),
        "faculty": sorted({r["faculty_id"] for r in got}),
        "count": len(got),
    }


def _lock(api, section_id, subject_id):
    context_id = api["context"].id
    r = api["client"].post(
        f"/api/academic-contexts/{context_id}/assignments/lock",
        json={"section_id": section_id, "subject_id": subject_id},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _ids(rows, section_number, subject_code):
    row = _rows_for(rows, section_number, subject_code)[0]
    return row["section_id"], row["subject_id"]


def _regenerate(api):
    run = run_to_completion(api)
    assert run["status"] in ("OPTIMAL", "FEASIBLE"), run
    return run["id"]


# ------------------------------------------------- the actual defect found

def test_a_terminal_run_is_never_visible_without_its_assignments(api, monkeypatch):
    """A client that polls for a terminal status then reads the timetable must
    never see an empty one.

    This is the bug the canonical workflow kept tripping over. `_persist` set
    the terminal status, flushed, and only then wrote the assignment rows;
    anything sharing that connection could observe the gap. Delaying the first
    Assignment holds the window open, making the check deterministic instead
    of a race that reproduces roughly one run in three.
    """
    import app.solver.run as run_mod

    real_assignment = run_mod.Assignment
    first = {"pending": True}

    def slow_assignment(*args, **kwargs):
        if first["pending"]:
            first["pending"] = False
            time.sleep(1.0)          # the window, held open on purpose
        return real_assignment(*args, **kwargs)

    monkeypatch.setattr(run_mod, "Assignment", slow_assignment)

    started = api["client"].post(
        "/api/generate", json={"academic_context_id": api["context"].id}
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]

    deadline = time.time() + 120
    observed_terminal = False
    while time.time() < deadline:
        api["db"].expire_all()
        status = api["client"].get(f"/api/runs/{run_id}").json()["status"]
        if status in ("OPTIMAL", "FEASIBLE", "PARTIAL", "INFEASIBLE"):
            observed_terminal = True
            if status != "INFEASIBLE":
                rows = _master(api, run_id)
                assert rows, (
                    "run reported a terminal status but had no assignments - "
                    "the status was published before the rows existed"
                )
            break
        time.sleep(0.02)

    assert observed_terminal, "run never reached a terminal status"
    api["db"].expire_all()


# ------------------------------------------------------- the locking rule

def test_one_locked_assignment_survives_regeneration(api):
    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS201")
    _lock(api, section_id, subject_id)
    expected = _placement(before, "S-A", "CS201")
    assert expected["count"] > 0

    after = _master(api, _regenerate(api))
    assert _placement(after, "S-A", "CS201") == expected


def test_several_locked_assignments_survive_regeneration(api):
    before = _master(api, _regenerate(api))
    targets = [("S-A", "CS201"), ("S-B", "CS202"), ("S-A", "CS251")]
    for section_number, code in targets:
        _lock(api, *_ids(before, section_number, code))
    expected = {t: _placement(before, *t) for t in targets}

    after = _master(api, _regenerate(api))
    for target in targets:
        assert _placement(after, *target) == expected[target], f"{target} moved"


def test_multi_hour_locked_assignment_keeps_every_slot(api):
    """CS251 is a 2-hour practical: both periods must come back unchanged, not
    just the block's first."""
    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS251")
    _lock(api, section_id, subject_id)
    expected = _placement(before, "S-A", "CS251")
    assert expected["count"] >= 2, "fixture no longer has a multi-hour session"

    after = _master(api, _regenerate(api))
    got = _placement(after, "S-A", "CS251")
    assert got["slots"] == expected["slots"]
    assert got["count"] == expected["count"]


def test_locked_faculty_room_day_and_slots_are_all_identical(api):
    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS201")
    locked = _lock(api, section_id, subject_id)
    expected = _placement(before, "S-A", "CS201")

    after_rows = _master(api, _regenerate(api))
    got = _rows_for(after_rows, "S-A", "CS201")
    assert got
    assert all(r["room_id"] == locked["room_id"] for r in got)
    assert all(r["faculty_id"] == locked["faculty_id"] for r in got)
    assert sorted((r["day"], r["period_index"]) for r in got) == expected["slots"]
    assert {r["day"] for r in got} == {day for day, _ in expected["slots"]}


def test_the_solver_is_still_free_to_move_unlocked_pairs(api):
    """The lock must pin the locked pair and nothing else.

    Asserted on the solver's input rather than by regenerating and hoping
    something moves - "it happened to change" is not a property, and CP-SAT is
    entitled to return the same solution twice.
    """
    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS201")
    _lock(api, section_id, subject_id)

    api["db"].expire_all()
    inp = load(api["db"], api["context"].id)
    # Matched on the ids rather than the key tuple: a pair's key gained a
    # session type when subjects could have two components, and this test is
    # about locking, not about the key's shape.
    locked_pair = next(
        p for p in inp.pairs
        if p.section_id == section_id and p.subject_id == subject_id
    )
    assert locked_pair.locked_starts, "locked pair was not pinned"
    assert set(locked_pair.starts) == set(locked_pair.locked_starts), (
        "a locked pair must have no alternative placements"
    )

    others = [
        p for p in inp.pairs
        if not (p.section_id == section_id and p.subject_id == subject_id)
    ]
    assert others
    for pair in others:
        assert not pair.locked_starts, f"{pair.subject_code} pinned but never locked"
        assert len(pair.starts) > 1, f"{pair.subject_code} lost its freedom to move"


def test_locked_assignment_appears_in_every_view_after_regeneration(api):
    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS201")
    locked = _lock(api, section_id, subject_id)
    expected = _placement(before, "S-A", "CS201")

    run2 = _regenerate(api)
    assert _placement(_master(api, run2), "S-A", "CS201") == expected

    client = api["client"]
    for url in (
        f"/api/timetable/section/{section_id}?run_id={run2}",
        f"/api/timetable/faculty/{locked['faculty_id']}?run_id={run2}",
        f"/api/timetable/room/{locked['room_id']}?run_id={run2}",
    ):
        r = client.get(url)
        assert r.status_code == 200, f"{url} -> {r.text}"
        cells = [
            cell
            for row in r.json()["rows"]
            for cell in row["cells"]
            if cell and cell.get("subject_code") == "CS201"
        ]
        assert cells, f"locked class missing from {url}"


def test_regeneration_does_not_silently_clear_the_lock(api):
    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS201")
    _lock(api, section_id, subject_id)

    _regenerate(api)

    api["db"].expire_all()
    row = (
        api["db"].query(SectionSubjectAssignment)
        .filter_by(section_id=section_id, subject_id=subject_id)
        .one()
    )
    assert row.locked is True, "regeneration cleared the lock"


@pytest.mark.parametrize("rounds", [5])
def test_repeated_regeneration_never_loses_the_locked_assignment(api, rounds):
    """One regeneration proves little: the placement is re-derived every time,
    so drift would show up on a later round."""
    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS201")
    _lock(api, section_id, subject_id)
    expected = _placement(before, "S-A", "CS201")

    for i in range(rounds):
        rows = _master(api, _regenerate(api))
        assert _placement(rows, "S-A", "CS201") == expected, f"drifted on round {i + 1}"
        api["db"].expire_all()
        row = (
            api["db"].query(SectionSubjectAssignment)
            .filter_by(section_id=section_id, subject_id=subject_id).one()
        )
        assert row.locked is True, f"lock lost on round {i + 1}"


# ===========================================================================
# A lock that cannot be honoured must say so.
#
# Locks used to fail open in silence: if the previous placement no longer fit
# the grid, the pair was simply rescheduled and nothing recorded it. A lock is
# a decision somebody made deliberately, so a timetable that quietly disagrees
# with one is worse than a timetable that says it had to.
# ===========================================================================


def test_a_lock_that_cannot_be_reproduced_is_reported_not_dropped(api):
    """Change the weekly load of a locked subject, then regenerate.

    The old placement has the wrong number of sessions to reuse, so the lock
    cannot be honoured. The run must still succeed - refusing to generate would
    be worse - but it must carry a warning naming the class.
    """
    import json

    from app.models import Subject, TimetableRun

    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS201")
    _lock(api, section_id, subject_id)

    subject = api["db"].get(Subject, subject_id)
    subject.sessions_per_week += 1
    api["db"].commit()

    run_id = _regenerate(api)
    run = api["db"].get(TimetableRun, run_id)

    assert run.status in ("OPTIMAL", "FEASIBLE"), "the timetable is still produced"
    warnings = json.loads(run.warnings_json or "[]")
    assert warnings, "a dropped lock must be recorded, not silently ignored"
    assert any("CS201" in w for w in warnings), warnings
    assert any("locked" in w.lower() for w in warnings), warnings


def test_the_warning_reaches_the_api(api):
    import json

    from app.models import Subject

    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS201")
    _lock(api, section_id, subject_id)

    subject = api["db"].get(Subject, subject_id)
    subject.sessions_per_week += 1
    api["db"].commit()

    run_id = _regenerate(api)
    body = api["client"].get(f"/api/runs/{run_id}").json()
    assert body["warnings"], "warnings must be visible to whoever reads the run"
    assert any("CS201" in w for w in body["warnings"])


def test_a_lock_that_still_fits_produces_no_warning(api):
    """The counterpart. A warning that appears when nothing went wrong is
    noise, and noise is how real warnings get ignored."""
    import json

    from app.models import TimetableRun

    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS201")
    _lock(api, section_id, subject_id)

    run_id = _regenerate(api)
    run = api["db"].get(TimetableRun, run_id)
    assert json.loads(run.warnings_json or "[]") == []


def test_a_half_written_run_is_not_mistaken_for_a_locks_history(api):
    """A lock's previous placement is read from a finished run, not a live one.

    The read path has always ignored runs that are still RUNNING; the solver
    did not, and would happily reconstruct a lock from whatever rows a live run
    had at that instant. A run whose rows are incomplete makes the locked block
    look the wrong length, and the lock is then reported as impossible to
    honour when nothing is wrong with it.

    Committed rows on a RUNNING run are not a state production reaches - rows
    and the terminal status are written in one transaction, deliberately. It is
    reachable in this harness, where the worker shares a connection with the
    test, and it is the state the guard exists for, so it is what is set up
    here.
    """
    import json

    from app.jobs import _next_version
    from app.models import Assignment, TimetableRun

    db = api["db"]
    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS201")
    _lock(api, section_id, subject_id)

    live = TimetableRun(
        academic_context_id=api["context"].id,
        version=_next_version(db, api["context"].id),
        status="RUNNING",
        publish_status="DRAFT",
        message="Solver started.",
    )
    db.add(live)
    db.commit()

    # One period of the locked pair, where a finished run has three. This is
    # what "some of the rows are written" looks like from outside.
    source = (
        db.query(Assignment)
        .filter_by(section_id=section_id, subject_id=subject_id)
        .first()
    )
    db.add(Assignment(
        run_id=live.id,
        section_id=source.section_id,
        subject_id=source.subject_id,
        timeslot_id=source.timeslot_id,
        room_id=source.room_id,
        faculty_id=source.faculty_id,
        block_id=source.block_id,
        session_type=source.session_type,
    ))
    db.commit()

    run_id = _regenerate(api)
    run = db.get(TimetableRun, run_id)
    assert json.loads(run.warnings_json or "[]") == [], (
        "the lock is reproducible; only the unfinished run made it look otherwise"
    )

    after = _master(api, run_id)
    assert _placement(before, "S-A", "CS201") == _placement(after, "S-A", "CS201"), (
        "and the lock actually held, not merely went unreported"
    )


def test_history_is_only_read_when_something_is_actually_locked(api):
    """Reproducing a lock needs the previous run's rows; nothing locked needs
    none of them.

    Read unconditionally, this is every assignment of every run the context has
    ever produced - a table that grows with each generate - loaded before the
    solver starts and then discarded in full. The cost is invisible in a test
    database and grows with the age of a real one.
    """
    from sqlalchemy import event

    from app.models import Assignment
    from app.solver.data import load

    _regenerate(api)  # so there is history to read
    db = api["db"]
    engine = db.get_bind()

    def count_assignment_reads() -> int:
        seen: list[str] = []

        def record(conn, cursor, statement, params, context, executemany):
            if "FROM assignment" in statement:
                seen.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            load(db, api["context"].id)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(seen)

    assert count_assignment_reads() == 0, (
        "no lock means no reason to read a single previous assignment"
    )

    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS201")
    _lock(api, section_id, subject_id)
    db.expire_all()

    assert count_assignment_reads() > 0, "a lock must still be reproduced"

    # And the rows it reads are the locked pair's, not the whole table.
    inp = load(db, api["context"].id)
    pinned = [p for p in inp.pairs if p.locked_starts]
    assert len(pinned) == 1
    assert db.query(Assignment).count() > len(pinned)


def test_changing_a_locked_subjects_load_is_refused_by_default(api):
    """The other half of making locks loud.

    Reporting a dropped lock after the fact is better than silence, but better
    still is not letting the edit that breaks it happen unnoticed.
    """
    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS201")
    _lock(api, section_id, subject_id)

    resp = api["client"].put(
        f"/api/subjects/{subject_id}", json={"sessions_per_week": 4}
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "locked" in detail
    assert "force=true" in detail, "the refusal should say how to proceed"


def test_forcing_the_change_removes_the_locks_and_records_why(api):
    from app.models import ChangeHistory, SectionSubjectAssignment

    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS201")
    _lock(api, section_id, subject_id)

    resp = api["client"].put(
        f"/api/subjects/{subject_id}?force=true", json={"sessions_per_week": 4}
    )
    assert resp.status_code == 200, resp.text

    api["db"].expire_all()
    still_locked = (
        api["db"].query(SectionSubjectAssignment)
        .filter_by(subject_id=subject_id, locked=True)
        .count()
    )
    assert still_locked == 0

    unlocks = (
        api["db"].query(ChangeHistory)
        .filter_by(subject_id=subject_id, change_type="unlock")
        .all()
    )
    assert unlocks, "removing a lock must leave a trace"
    assert any("weekly load changed" in (u.reason or "") for u in unlocks)


def test_an_unrelated_edit_is_not_refused(api):
    """The guard is about the weekly load, not about editing at all."""
    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS201")
    _lock(api, section_id, subject_id)

    resp = api["client"].put(
        f"/api/subjects/{subject_id}", json={"name": "Data Structures II"}
    )
    assert resp.status_code == 200, resp.text


# --------------------------------------------------- previewing a lock


def _preview(api, section_id, subject_id, lock, session_type=None):
    body = {"section_id": section_id, "subject_id": subject_id, "lock": lock}
    if session_type:
        body["session_type"] = session_type
    r = api["client"].post(
        f"/api/academic-contexts/{api['context'].id}/assignments/lock/preview",
        json=body,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_a_lock_can_be_described_before_it_is_applied(api):
    """Every other edit is previewed first; locking used to write immediately.

    The asymmetry was visible in the product: the assistant could explain what
    locking would do because the AI layer had its own preview, while the screen
    could only do it and show the result afterwards.
    """
    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS201")

    preview = _preview(api, section_id, subject_id, lock=True)
    assert preview["would_change"] is True
    assert preview["currently_locked"] is False
    assert preview["will_be_locked"] is True
    assert "CS201" in preview["summary"]
    assert preview["consequence"], "a preview that says nothing is not a preview"


def test_previewing_changes_nothing(api):
    """The point of a preview."""
    from app.models import SectionSubjectAssignment

    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS201")

    _preview(api, section_id, subject_id, lock=True)

    api["db"].expire_all()
    rows = (
        api["db"].query(SectionSubjectAssignment)
        .filter_by(section_id=section_id, subject_id=subject_id).all()
    )
    assert rows and not any(r.locked for r in rows), "preview must not write"


def test_a_no_op_says_so_rather_than_promising_a_change(api):
    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS201")
    _lock(api, section_id, subject_id)

    preview = _preview(api, section_id, subject_id, lock=True)
    assert preview["would_change"] is False
    assert "already" in preview["summary"].lower()


def test_the_preview_lists_every_component_it_would_affect(api):
    """Locking a subject taught as both a lecture and a practical locks both.
    A preview that mentioned one of them would understate the act."""
    before = _master(api, _regenerate(api))
    section_id, subject_id = _ids(before, "S-A", "CS201")

    preview = _preview(api, section_id, subject_id, lock=True)
    assert len(preview["components"]) >= 1
    for component in preview["components"]:
        assert "session_type" in component
        assert "locked" in component


def test_previewing_an_unscheduled_pair_is_refused_exactly_as_applying_is(api):
    """A preview that succeeds where the write would fail is worse than no
    preview: it invites the user to confirm something impossible."""
    from app.models import Section, Subject

    db = api["db"]
    section = db.query(Section).filter_by(section_number="S-A").one()
    subject = Subject(name="Never Scheduled", code="CS999", type="theory",
                      session_length_hours=1, sessions_per_week=1)
    db.add(subject)
    db.commit()

    body = {"section_id": section.id, "subject_id": subject.id, "lock": True}
    preview = api["client"].post(
        f"/api/academic-contexts/{api['context'].id}/assignments/lock/preview",
        json=body,
    )
    applied = api["client"].post(
        f"/api/academic-contexts/{api['context'].id}/assignments/lock",
        json={"section_id": section.id, "subject_id": subject.id},
    )
    assert preview.status_code == applied.status_code == 404
