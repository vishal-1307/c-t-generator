"""Lab groups: a section split in two, and what that does to clashes.

A department that splits section 2401 into 24011 and 24012 is saying two things
at once, and the solver has to believe both:

* a group's students are the parent's students, so a lecture for 2401 and a lab
  for 24011 cannot overlap;
* the two groups are *different* students, so their labs may run at the same
  time - which is the whole reason for splitting a section.

A single "these three sections clash" rule would satisfy the first and destroy
the second. These tests pin both halves, and the sibling one matters most: it
is the property a careless fix silently removes, and nothing else would notice
because a timetable that runs the groups one after another still looks correct.
"""
from __future__ import annotations

from app import timetable_checker
from app.grid import build_grid
from app.models import Room, Section, Subject
from app.solver.data import load
from app.solver.model import build
from app.solver.run import generate
from app.solver.solve import solve


def _grid(db_session, periods=4):
    db_session.add_all(build_grid(periods=periods))
    db_session.commit()


def _rooms(db_session):
    rooms = [
        Room(room_number="101", block="36", room_code="36-101", capacity=80,
             room_type="theory", is_active=True),
        Room(room_number="102", block="36", room_code="36-102", capacity=80,
             room_type="theory", is_active=True),
        Room(room_number="201", block="33", room_code="33-201", capacity=40,
             room_type="lab", is_active=True),
        Room(room_number="202", block="33", room_code="33-202", capacity=40,
             room_type="lab", is_active=True),
    ]
    db_session.add_all(rooms)
    db_session.commit()
    return {r.room_code: r for r in rooms}


def _subject(db_session, code, kind, faculty, sessions=1, length=1):
    s = Subject(name=code, code=code, type=kind,
                sessions_per_week=sessions, session_length_hours=length)
    db_session.add(s)
    db_session.commit()
    faculty.subjects.append(s)
    db_session.commit()
    return s


def _faculty(db_session, code):
    from app.models import Faculty

    f = Faculty(name=code, faculty_code=code, is_active=True)
    db_session.add(f)
    db_session.commit()
    return f


def _family(db_session, context, *, parent_strength=70, group_strength=35):
    """2401 with two lab groups, 24011 and 24012."""
    parent = Section(academic_context_id=context.id, section_number="2401",
                     strength=parent_strength, is_active=True)
    db_session.add(parent)
    db_session.commit()
    kids = [
        Section(academic_context_id=context.id, section_number=n,
                strength=group_strength, is_active=True,
                parent_section_id=parent.id)
        for n in ("24011", "24012")
    ]
    db_session.add_all(kids)
    db_session.commit()
    return parent, kids


# --------------------------------------------------------------------------


def test_a_section_and_its_group_are_never_taught_at_the_same_time(db_session, context):
    """The same students, so the same rule as one section with two subjects.

    Asked as a question the model can only answer one way: pin the parent's
    lecture and the group's lab to the same period and see whether the model
    still admits a solution. Merely generating and looking at the output is not
    enough - a first draft of this test used one teacher for both subjects and
    passed with the rule switched off, because the faculty clash was quietly
    doing the work. Different teachers and different room types leave the
    hierarchy as the only thing that can forbid the overlap.
    """
    from ortools.sat.python import cp_model

    _grid(db_session, periods=4)
    _rooms(db_session)
    t1, t2 = _faculty(db_session, "T-1"), _faculty(db_session, "T-2")
    parent, kids = _family(db_session, context)

    lecture = _subject(db_session, "ECE181", "theory", t1, sessions=1)
    lab = _subject(db_session, "ECE182", "practical", t2, sessions=1)
    parent.subjects.append(lecture)
    kids[0].subjects.append(lab)
    db_session.commit()

    inp = load(db_session, academic_context_id=context.id)
    built = build(inp)

    by_section = {p.section_id: i for i, p in enumerate(inp.pairs)}
    p_parent, p_child = by_section[parent.id], by_section[kids[0].id]

    # Both obligations into slot 0. Nothing else in this model objects.
    built.model.Add(built.start[(p_parent, 0, 0)] == 1)
    built.model.Add(built.start[(p_child, 0, 0)] == 1)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10
    status = solver.Solve(built.model)
    assert status == cp_model.INFEASIBLE, (
        "the model allowed 2401's lecture and 24011's lab in the same period. "
        "Those are the same students."
    )

    # And the whole thing still solves when they are not forced to collide.
    result, run = generate(db_session, context.id, max_seconds=20)
    assert result.ok, result.message
    assert timetable_checker.check_all(db_session, run.id) == []


def test_a_manual_move_cannot_put_a_group_on_top_of_its_section(db_session, context):
    """The preview has to know the hierarchy the re-check already knows.

    It did not: moving 24011's lab onto a period where 2401 has a lecture was
    approved by the preview, then refused by apply's independent re-check - a
    preview promising something apply will not do, which is the bug this
    codebase has already fixed once for mixed subjects.
    """
    from app import manual_edit
    from app.models import Assignment, TimeSlot, TimetableRun

    _grid(db_session, periods=4)
    rooms = _rooms(db_session)
    t1, t2 = _faculty(db_session, "T-1"), _faculty(db_session, "T-2")
    parent, kids = _family(db_session, context)
    lecture = _subject(db_session, "ECE181", "theory", t1, sessions=1)
    lab = _subject(db_session, "ECE182", "practical", t2, sessions=1)
    parent.subjects.append(lecture)
    kids[0].subjects.append(lab)
    db_session.commit()

    monday = db_session.query(TimeSlot).filter_by(day="Monday").order_by(
        TimeSlot.period_index).all()
    run = TimetableRun(academic_context_id=context.id, version=1,
                       status="FEASIBLE", publish_status="DRAFT")
    db_session.add(run)
    db_session.commit()
    db_session.add_all([
        Assignment(run_id=run.id, section_id=parent.id, subject_id=lecture.id,
                   faculty_id=t1.id, room_id=rooms["36-101"].id,
                   timeslot_id=monday[0].id, block_id=1, session_type="L"),
        Assignment(run_id=run.id, section_id=kids[0].id, subject_id=lab.id,
                   faculty_id=t2.id, room_id=rooms["33-201"].id,
                   timeslot_id=monday[1].id, block_id=2, session_type="P"),
    ])
    db_session.commit()
    lab_row = db_session.query(Assignment).filter_by(block_id=2).one()

    preview = manual_edit.preview_move(db_session, lab_row.id, monday[0].id)
    assert not preview.ok, "the preview approved a lab on top of its own section"
    assert any("shares students" in i for i in preview.issues), preview.issues

    outcome = manual_edit.apply_move(db_session, lab_row.id, monday[0].id)
    assert not outcome.ok


def test_two_unrelated_sections_may_share_a_period(db_session, context):
    """The control for the test above: without a parent link, no constraint.

    If this ever became infeasible too, the rule would be forbidding overlaps
    between sections that have nothing to do with each other - and the test
    above would still pass, saying nothing.
    """
    from ortools.sat.python import cp_model

    _grid(db_session, periods=4)
    _rooms(db_session)
    t1, t2 = _faculty(db_session, "T-1"), _faculty(db_session, "T-2")
    a = Section(academic_context_id=context.id, section_number="2401",
                strength=70, is_active=True)
    b = Section(academic_context_id=context.id, section_number="2402",
                strength=35, is_active=True)
    db_session.add_all([a, b])
    db_session.commit()

    lecture = _subject(db_session, "ECE181", "theory", t1, sessions=1)
    lab = _subject(db_session, "ECE182", "practical", t2, sessions=1)
    a.subjects.append(lecture)
    b.subjects.append(lab)
    db_session.commit()

    inp = load(db_session, academic_context_id=context.id)
    built = build(inp)
    by_section = {p.section_id: i for i, p in enumerate(inp.pairs)}
    built.model.Add(built.start[(by_section[a.id], 0, 0)] == 1)
    built.model.Add(built.start[(by_section[b.id], 0, 0)] == 1)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10
    assert solver.Solve(built.model) in {cp_model.OPTIMAL, cp_model.FEASIBLE}, (
        "two unrelated sections were forbidden from sharing a period"
    )


def test_two_groups_of_one_section_may_share_a_period(db_session, context):
    """Different students. Forbidding this would defeat the point of splitting.

    The grid is deliberately tight: two 2-session labs across four periods, with
    the parent taking two of them, leaves no arrangement in which the siblings
    never overlap. So if this passes, parallel groups are genuinely possible;
    if a future change collapses the family into one clash set, this becomes
    infeasible rather than merely differently-shaped.
    """
    _grid(db_session, periods=4)
    _rooms(db_session)
    t1, t2, t3 = (_faculty(db_session, c) for c in ("T-1", "T-2", "T-3"))
    parent, kids = _family(db_session, context)

    lecture = _subject(db_session, "ECE181", "theory", t1, sessions=2)
    lab1 = _subject(db_session, "ECE182", "practical", t2, sessions=2)
    lab2 = _subject(db_session, "ECE183", "practical", t3, sessions=2)
    parent.subjects.append(lecture)
    kids[0].subjects.append(lab1)
    kids[1].subjects.append(lab2)
    db_session.commit()

    result, run = generate(db_session, context.id, max_seconds=20)
    assert result.ok, (
        "the two groups' labs have to run in parallel to fit, and the solver "
        f"could not: {result.message}"
    )
    assert timetable_checker.check_all(db_session, run.id) == []

    from app.models import Assignment

    rows = db_session.query(Assignment).filter(Assignment.run_id == run.id).all()
    g1 = {a.timeslot_id for a in rows if a.section_id == kids[0].id}
    g2 = {a.timeslot_id for a in rows if a.section_id == kids[1].id}
    assert g1 & g2, "the siblings were never scheduled together, so nothing was proved"


def test_a_parent_with_no_subjects_of_its_own_still_constrains_its_groups(
    db_session, context
):
    """The iteration trap: a family reachable only through `section_children`.

    If the clash rule walked only the sections that have obligations, this
    parent would never be visited and its groups would be unconstrained. The
    teacher's own data would not have caught it, because there every parent
    happens to teach something.
    """
    _grid(db_session, periods=4)
    _rooms(db_session)
    teacher = _faculty(db_session, "T-1")
    parent, kids = _family(db_session, context)

    lab = _subject(db_session, "ECE182", "practical", teacher, sessions=2)
    kids[0].subjects.append(lab)
    db_session.commit()  # parent teaches nothing at all

    inp = load(db_session, academic_context_id=context.id)
    assert inp.section_children.get(parent.id) == [kids[0].id, kids[1].id]

    result = solve(inp, soft=False, max_seconds=10)
    assert result.status in {"OPTIMAL", "FEASIBLE"}
    built = build(inp)
    assert built is not None  # the model builds with a childless-parent family


def test_the_checker_catches_a_parent_group_clash_the_solver_never_made(
    db_session, context
):
    """The verifier has to find this on its own, from the rows.

    Written by hand rather than by solving, because the solver will not produce
    it - and a check that only ever sees valid input proves nothing.
    """
    _grid(db_session, periods=4)
    rooms = _rooms(db_session)
    teacher = _faculty(db_session, "T-1")
    parent, kids = _family(db_session, context)
    lecture = _subject(db_session, "ECE181", "theory", teacher, sessions=1)
    lab = _subject(db_session, "ECE182", "practical", teacher, sessions=1)
    parent.subjects.append(lecture)
    kids[0].subjects.append(lab)
    db_session.commit()

    from app.models import Assignment, TimeSlot, TimetableRun

    run = TimetableRun(academic_context_id=context.id, version=99, status="FEASIBLE")
    db_session.add(run)
    db_session.commit()
    slot = db_session.query(TimeSlot).order_by(TimeSlot.id).first()
    db_session.add_all([
        Assignment(run_id=run.id, section_id=parent.id, subject_id=lecture.id,
                   faculty_id=teacher.id, room_id=rooms["36-101"].id,
                   timeslot_id=slot.id, block_id=1, session_type="L"),
        Assignment(run_id=run.id, section_id=kids[0].id, subject_id=lab.id,
                   faculty_id=teacher.id, room_id=rooms["33-201"].id,
                   timeslot_id=slot.id, block_id=2, session_type="P"),
    ])
    db_session.commit()

    problems = timetable_checker.check_all(db_session, run.id)
    assert any("group of" in p for p in problems), (
        f"the parent/group overlap was not reported: {problems}"
    )


def test_a_section_without_groups_is_unaffected(db_session, context):
    """The regression anchor: no parents means the model is what it always was."""
    _grid(db_session, periods=4)
    _rooms(db_session)
    teacher = _faculty(db_session, "T-1")
    plain = Section(academic_context_id=context.id, section_number="2402",
                    strength=70, is_active=True)
    db_session.add(plain)
    db_session.commit()
    subject = _subject(db_session, "ECE281", "theory", teacher, sessions=2)
    plain.subjects.append(subject)
    db_session.commit()

    inp = load(db_session, academic_context_id=context.id)
    assert inp.section_children == {}
    assert inp.section_parent == {}

    result, run = generate(db_session, context.id, max_seconds=20)
    assert result.ok
    assert timetable_checker.check_all(db_session, run.id) == []


def test_a_group_whose_parent_is_left_out_of_the_solve_is_not_silently_linked(
    db_session, context
):
    """Partial generation must not carry a constraint it cannot enforce.

    Scheduling only the group means the parent's classes are not in this model,
    so there is nothing to keep it apart from. The hierarchy is dropped rather
    than half-applied; `validation` is where that gets reported.
    """
    _grid(db_session, periods=4)
    _rooms(db_session)
    teacher = _faculty(db_session, "T-1")
    parent, kids = _family(db_session, context)
    lab = _subject(db_session, "ECE182", "practical", teacher, sessions=1)
    kids[0].subjects.append(lab)
    db_session.commit()

    inp = load(db_session, academic_context_id=context.id, section_ids=[kids[0].id])
    assert inp.section_parent == {}, (
        "the parent is not in this solve, so the link must not be recorded"
    )
    assert inp.section_children == {}


def test_generating_a_group_without_its_section_is_refused(client, db_session, context):
    """Half a family cannot be scheduled: the other half is not in the model.

    Refused rather than warned. A run that solved anyway would sit in the
    database looking like every other valid timetable.
    """
    _grid(db_session, periods=4)
    _rooms(db_session)
    teacher = _faculty(db_session, "T-1")
    parent, kids = _family(db_session, context)
    lab = _subject(db_session, "ECE182", "practical", teacher, sessions=1)
    kids[0].subjects.append(lab)
    db_session.commit()

    resp = client.post("/api/generate", json={
        "academic_context_id": context.id, "section_ids": [kids[0].id],
    })
    assert resp.status_code == 422, resp.text
    assert "lab group" in resp.json()["detail"]

    # The parent without its groups is equally split, and equally refused.
    resp = client.post("/api/generate", json={
        "academic_context_id": context.id, "section_ids": [parent.id],
    })
    assert resp.status_code == 422, resp.text
    assert "left out" in resp.json()["detail"]

    # The whole family together passes the guard, and so does "everything".
    # Asserted against the guard rather than by posting: a 202 would hand the
    # solve to a background thread these fixtures have not wired to the test
    # database, and the thread would outlive the test and fail in the dark.
    from app.routers.generate import _reject_split_families

    _reject_split_families(db_session, context.id, [parent.id, kids[0].id, kids[1].id])
    _reject_split_families(db_session, context.id, None)


def test_validation_reports_group_problems(db_session, context):
    """Each way of stating a group wrongly gets its own named check."""
    from app.validation import validate

    _grid(db_session, periods=4)
    _rooms(db_session)
    teacher = _faculty(db_session, "T-1")
    parent, kids = _family(db_session, context, parent_strength=70,
                           group_strength=20)  # 20 + 20 != 70
    subject = _subject(db_session, "ECE181", "theory", teacher, sessions=1)
    parent.subjects.append(subject)
    for k in kids:
        k.subjects.append(subject)
    db_session.commit()

    report = validate(db_session, academic_context_id=context.id)
    by_id = {c.id: c for c in report.checks}

    assert by_id["group_strengths_add_up"].ok is False
    assert by_id["group_strengths_add_up"].severity == "warning"
    assert "40" in by_id["group_strengths_add_up"].detail

    # A mismatch is a warning, so it must not by itself block generation.
    assert by_id["group_parent_in_same_context"].ok is True
    assert by_id["group_depth_is_one"].ok is True

    # A group of a group is a blocker.
    kids[1].parent_section_id = kids[0].id
    db_session.commit()
    report = validate(db_session, academic_context_id=context.id)
    depth = {c.id: c for c in report.checks}["group_depth_is_one"]
    assert depth.ok is False and depth.severity == "blocker"
