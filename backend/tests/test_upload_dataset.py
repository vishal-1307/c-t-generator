"""Uploading the two files, with nothing else asked for.

* No semester, programme or department has to be typed. An upload that does
  not name one becomes its own dataset, labelled by when and from what - and a
  second upload is a second dataset, so nothing from the first leaks into it.
* Each row's teacher teaches *that* section. The sheet says who teaches whom;
  it does not say a teacher merely may teach a subject.
* A class no room can take is named, with the one reason that rules every
  room out - not folded into a sentence about "eligible rooms".
"""
from __future__ import annotations

import datetime as dt

from app.models import AcademicContext, Section, SectionSubject, SectionSubjectAssignment
from app.solver.data import load
from tests.test_teacher_import import (
    INFRA_HEADERS,
    INFRA_ROWS,
    LOAD_HEADERS,
    LOAD_ROWS,
    _xlsx,
)


def _send(client, path, load_rows=LOAD_ROWS, infra_rows=INFRA_ROWS, **form):
    return client.post(
        path,
        files={
            "load": ("Load.xlsx", _xlsx(LOAD_HEADERS, load_rows), "application/vnd.ms-excel"),
            "infra": ("Infra.xlsx", _xlsx(INFRA_HEADERS, infra_rows), "application/vnd.ms-excel"),
        },
        data=form,
    )


# ------------------------------------------------------------ no semester


def test_an_upload_needs_no_semester_and_becomes_its_own_dataset(client, db_session):
    today = dt.datetime.now(dt.UTC).date().isoformat()

    preview = _send(client, "/api/teacher/preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["dataset_label"] == f"Upload 1 · {today} · Load"
    assert preview.json()["readiness"]["ready"] is True

    first = _send(client, "/api/teacher/apply").json()
    assert first["committed"] is True
    assert first["dataset_label"] == f"Upload 1 · {today} · Load"

    second = _send(client, "/api/teacher/apply").json()
    assert second["committed"] is True
    assert second["dataset_label"] == f"Upload 2 · {today} · Load"
    assert second["academic_context_id"] != first["academic_context_id"]

    # Two datasets, each with its own nine sections - the second upload did not
    # add to the first or reuse its sections.
    for ctx_id in (first["academic_context_id"], second["academic_context_id"]):
        assert db_session.query(Section).filter_by(academic_context_id=ctx_id).count() == 9
    assert db_session.query(AcademicContext).count() == 2


def test_a_second_upload_is_complete_and_leaves_the_first_alone(client, db_session):
    """The same two files uploaded twice. Counting sections was not enough:
    the second dataset's sections came out with no subjects and no groups,
    because a mapping was matched by its typed numbers and so found the first
    dataset's identical row - and the group link found the first dataset's
    24011 and repointed it at the second dataset's 2401."""
    first = _send(client, "/api/teacher/apply").json()
    again = _send(client, "/api/teacher/preview").json()
    assert again["readiness"]["ready"] is True, again["readiness"]
    second = _send(client, "/api/teacher/apply").json()
    assert second["readiness"]["ready"] is True, second["readiness"]
    a, b = first["academic_context_id"], second["academic_context_id"]
    db_session.expire_all()

    def shape(ctx):
        sections = db_session.query(Section).filter_by(academic_context_id=ctx).all()
        by_id = {s.id: s for s in sections}
        curriculum = sorted(
            (link.section.section_number, link.subject.code)
            for link in db_session.query(SectionSubject)
            .filter(SectionSubject.section_id.in_(by_id)).all()
        )
        groups = {}
        for s in sections:
            if s.parent_section_id is not None:
                parent = by_id.get(s.parent_section_id)
                # A parent outside this dataset is exactly the corruption.
                groups[s.section_number] = parent.section_number if parent else "ELSEWHERE"
        return curriculum, groups

    first_shape, second_shape = shape(a), shape(b)
    assert first_shape[0], "the first dataset lost its subjects"
    assert second_shape == first_shape
    assert second_shape[1] == {"24011": "2401", "24031": "2403"}

    # And it can be solved: every class, rooms only from the listed rooms.
    from app.room_scope import usable_rooms
    from app.solver.solve import solve

    inp = load(db_session, academic_context_id=b)
    assert len(inp.pairs) == len(load(db_session, academic_context_id=a).pairs)
    result = solve(inp, max_seconds=60, soft=False)
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.message
    listed = {r.id for r in usable_rooms(db_session, b)}
    assert {p.room_id for p in result.placements} <= listed


def test_naming_a_semester_still_updates_it_in_place(client, db_session):
    form = dict(academic_year="2025-26", semester=1, program="B.Tech ECE",
                department="Electronics")
    _send(client, "/api/teacher/apply", **form)
    _send(client, "/api/teacher/apply", **form)
    assert db_session.query(AcademicContext).count() == 1


# ------------------------------------------------------------ teachers


def test_each_section_keeps_the_teacher_its_row_names(client, db_session):
    """ECE181 taught to two sections by two teachers. Without pinning both
    teachers are eligible for both sections and the solver may swap them."""
    rows = [list(r) for r in LOAD_ROWS]
    rows.append(["Faculty H", 90008, "Embedded systems", "ECE181", 2402, 38,
                 "Class", "No", 1, 4])
    body = _send(client, "/api/teacher/apply", load_rows=rows).json()
    assert body["committed"] is True, body
    ctx = body["academic_context_id"]

    inp = load(db_session, academic_context_id=ctx)
    teacher = {
        (p.section_number, p.subject_code): p.faculty_ids for p in inp.pairs
    }
    fac = {f.faculty_code: f.id for f in inp.faculty.values()}
    assert teacher[("2401", "ECE181")] == [fac["90001"]]
    assert teacher[("2402", "ECE181")] == [fac["90008"]]

    pins = db_session.query(SectionSubjectAssignment).filter_by(academic_context_id=ctx).count()
    assert pins == 10


def test_one_section_given_one_subject_by_two_teachers_is_refused(client):
    rows = [list(r) for r in LOAD_ROWS]
    rows.append(["Faculty H", 90008, "Embedded systems", "ECE181", 2401, 68,
                 "Class", "Yes", 1, 4])
    resp = _send(client, "/api/teacher/preview", load_rows=rows)
    assert resp.status_code == 422
    problems = resp.json()["detail"]["problems"]
    assert any("2401" in p["message"] and "ECE181" in p["message"]
               and "one teacher" in p["message"] for p in problems), problems


# ------------------------------------------------------ no room at all


def test_a_class_too_big_for_every_room_is_named_with_its_reason(client):
    rows = [list(r) for r in LOAD_ROWS]
    next(r for r in rows if r[4] == 2323)[5] = 130
    body = _send(client, "/api/teacher/preview", load_rows=rows).json()
    missing = body["readiness"]["unassignable"]
    assert missing == [{
        "subject_code": "ECE212", "subject_name": "Signals and systems",
        "section": "2323", "strength": 130, "byod": False, "type": "Theory",
        "reason": "No classroom with capacity >= 130; the largest seats 120.",
    }]
    assert body["readiness"]["ready"] is False


def test_a_byod_class_with_no_byod_room_big_enough_says_so(client):
    """Only 36-409 seats 120 once 36-309 is gone, and it has no charging
    points - so a BYOD class of 120 has nowhere to go, for that reason."""
    infra = [r for r in INFRA_ROWS if not (r[0] == 36 and r[1] == 309)]
    rows = [list(r) for r in LOAD_ROWS]
    next(r for r in rows if r[4] == 2323)[7] = "Yes"
    body = _send(client, "/api/teacher/preview", load_rows=rows, infra_rows=infra).json()
    missing = body["readiness"]["unassignable"]
    assert [m["reason"] for m in missing] == [
        "No available BYOD-capable classroom with capacity >= 120."
    ]
    assert missing[0]["byod"] is True
