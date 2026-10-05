"""The rules the teacher confirmed, each pinned so it cannot quietly stop holding.

* A **theory** subject meets a section at most once a day. Several different
  theory subjects on one day is normal and allowed.
* A **practical** may meet more than once in a day - a lab can run two or three
  sessions back to back when its demand needs it.
* Classes are scheduled **Monday to Friday** only.
* **BYOD** is a physical capability of a room - charging points at the benches -
  so a class that needs it, lecture or lab, goes only to a room that has it.

The shape of these tests matters more than their number. A solver asked for a
timetable and found to have spread a subject over five days proves nothing:
the objective already discourages repeats, so a correct-looking answer can
come from the soft terms with the hard rule switched off. So each rule is
tested by building a model where it is the *only* thing that can forbid the
outcome, then showing the outcome is forbidden - and, where it is cheap, that
removing the rule makes it allowed again.
"""
from __future__ import annotations

import pytest
from ortools.sat.python import cp_model

from app import manual_edit, timetable_checker, validation
from app.grid import build_grid
from app.models import (
    Assignment,
    Faculty,
    Room,
    Section,
    Subject,
    TimeSlot,
    TimetableRun,
)
from app.solver import model as model_module
from app.solver.data import load
from app.solver.model import UnschedulablePair, build
from app.solver.run import generate


# ----------------------------------------------------------------- fixtures


def _grid(db, days=("Monday",), periods=4):
    db.add_all(build_grid(days=list(days), periods=periods))
    db.commit()


def _room(db, number, *, kind="theory", capacity=80, byod=False, block="36"):
    r = Room(room_number=number, block=block, room_code=f"{block}-{number}",
             capacity=capacity, room_type=kind, is_active=True, byod=byod)
    db.add(r)
    db.commit()
    return r


def _faculty(db, code):
    f = Faculty(name=code, faculty_code=code, is_active=True)
    db.add(f)
    db.commit()
    return f


def _subject(db, code, kind, teacher, *, sessions=1, length=1, byod=False,
             lecture=None, practical=None, section=None):
    fields = dict(name=code, code=code, type=kind, sessions_per_week=sessions,
                  session_length_hours=length, byod_required=byod)
    if kind == "mixed":
        fields.update(
            lecture_sessions_per_week=lecture[0], lecture_session_length=lecture[1],
            practical_sessions_per_week=practical[0],
            practical_session_length=practical[1],
        )
    s = Subject(**fields)
    db.add(s)
    db.commit()
    teacher.subjects.append(s)
    db.commit()
    if section is not None:
        section.subjects.append(s)
        db.commit()
    return s


def _section(db, context, number="2401", strength=60):
    s = Section(academic_context_id=context.id, section_number=number,
                strength=strength, is_active=True)
    db.add(s)
    db.commit()
    return s


def _slot(db, assignment) -> TimeSlot:
    return db.get(TimeSlot, assignment.timeslot_id)


def _feasible(built, seconds=10) -> bool:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    return solver.Solve(built.model) in {cp_model.OPTIMAL, cp_model.FEASIBLE}


def _without_theory_rule(monkeypatch):
    monkeypatch.setattr(model_module, "_add_theory_once_per_day", lambda built: None)


# ---------------------------------------------------- theory: once a day


def test_a_theory_subject_cannot_meet_a_section_twice_in_one_day(
    db_session, context, monkeypatch
):
    """One teaching day, a theory subject that meets twice a week.

    Four free periods and nothing else in the model, so the only thing that
    can refuse this is the once-a-day rule - and switching that rule off has
    to make the same model solvable, or the test was never about the rule.
    """
    _grid(db_session, days=["Monday"], periods=4)
    _room(db_session, "101")
    t = _faculty(db_session, "T-1")
    section = _section(db_session, context)
    _subject(db_session, "ECE281", "theory", t, sessions=2, section=section)
    db_session.commit()

    inp = load(db_session, academic_context_id=context.id)
    assert not _feasible(build(inp)), (
        "ECE281 was allowed to meet 2401 twice on Monday"
    )

    _without_theory_rule(monkeypatch)
    assert _feasible(build(inp)), (
        "the model is infeasible even without the once-a-day rule, so the "
        "assertion above was not testing that rule"
    )


def test_different_theory_subjects_may_share_a_day(db_session, context):
    """The rule is per subject, not a cap on theory per day.

    Three theory subjects, one day, one session each: all three must fit on
    Monday, because there is nowhere else for them to go.
    """
    _grid(db_session, days=["Monday"], periods=4)
    _room(db_session, "101")
    section = _section(db_session, context)
    for code, teacher in (("ECE281", "T-1"), ("MAT102", "T-2"), ("CSE201", "T-3")):
        _subject(db_session, code, "theory", _faculty(db_session, teacher), section=section)
    db_session.commit()

    result, run = generate(db_session, context.id, max_seconds=20)
    assert result.ok, result.message
    assert timetable_checker.check_all(db_session, run.id) == []
    days = {
        _slot(db_session, a).day for a in
        db_session.query(Assignment).filter(Assignment.run_id == run.id)
    }
    assert days == {"Monday"}


def test_a_two_period_theory_session_is_one_occurrence(db_session, context):
    """Duration is the length of one class, so a 2-period lecture is one class.

    Counting periods rather than sessions would forbid every multi-period
    lecture outright - the teacher's own file has one (ECE212, two periods).
    """
    _grid(db_session, days=["Monday"], periods=4)
    _room(db_session, "101")
    section = _section(db_session, context)
    _subject(db_session, "ECE212", "theory", _faculty(db_session, "T-1"),
                 sessions=1, length=2, section=section)
    db_session.commit()

    result, run = generate(db_session, context.id, max_seconds=20)
    assert result.ok, result.message
    assert timetable_checker.check_all(db_session, run.id) == []


def test_theory_sessions_land_on_different_days_when_there_are_enough(
    db_session, context
):
    """Four sessions a week, five days: every session on its own day."""
    _grid(db_session, days=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
          periods=4)
    _room(db_session, "101")
    section = _section(db_session, context)
    _subject(db_session, "ECE181", "theory", _faculty(db_session, "T-1"), sessions=4, section=section)
    db_session.commit()

    result, run = generate(db_session, context.id, max_seconds=20)
    assert result.ok, result.message
    days = [
        _slot(db_session, a).day for a in
        db_session.query(Assignment).filter(Assignment.run_id == run.id)
    ]
    assert len(days) == 4
    assert len(set(days)) == 4, f"ECE181 met twice on one day: {sorted(days)}"


# ------------------------------------------------ practical: may repeat


def test_a_lab_may_meet_twice_in_one_day(db_session, context):
    """The same one-day grid that refuses a repeated lecture accepts a lab.

    This is the control for the first test: if the rule were applied to every
    subject, this would be infeasible too.
    """
    # Five periods, not four: two two-period labs on a four-period day would
    # fill it, and the break rule - not the once-a-day rule - would refuse it.
    _grid(db_session, days=["Monday"], periods=5)
    _room(db_session, "201", kind="lab", capacity=40)
    section = _section(db_session, context, strength=36)
    _subject(db_session, "ECE182", "practical", _faculty(db_session, "T-1"),
                 sessions=2, length=2, section=section)
    db_session.commit()

    result, run = generate(db_session, context.id, max_seconds=20)
    assert result.ok, result.message
    assert timetable_checker.check_all(db_session, run.id) == []
    rows = db_session.query(Assignment).filter(Assignment.run_id == run.id).all()
    assert len(rows) == 4
    assert {_slot(db_session, a).day for a in rows} == {"Monday"}
    assert len({a.block_id for a in rows}) == 2


def test_a_three_period_lab_stays_one_contiguous_block(db_session, context):
    _grid(db_session, days=["Monday", "Tuesday"], periods=4)
    _room(db_session, "201", kind="lab", capacity=40)
    section = _section(db_session, context, strength=36)
    _subject(db_session, "ECE102", "practical", _faculty(db_session, "T-1"),
                 sessions=2, length=3, section=section)
    db_session.commit()

    result, run = generate(db_session, context.id, max_seconds=20)
    assert result.ok, result.message
    assert timetable_checker.check_all(db_session, run.id) == []
    blocks: dict[int, list] = {}
    for a in db_session.query(Assignment).filter(Assignment.run_id == run.id):
        blocks.setdefault(a.block_id, []).append(_slot(db_session, a))
    assert len(blocks) == 2
    for slots in blocks.values():
        assert len({s.day for s in slots}) == 1
        periods = sorted(s.period_index for s in slots)
        assert periods == list(range(periods[0], periods[0] + 3))


def test_a_mixed_subject_limits_its_lecture_but_not_its_practical(
    db_session, context
):
    """The rule follows the component, not the subject's label.

    One day. A mixed subject whose practical meets twice is fine; the same
    subject with a lecture that meets twice is not.
    """
    _grid(db_session, days=["Monday"], periods=6)
    _room(db_session, "101")
    _room(db_session, "201", kind="lab", capacity=80)
    t = _faculty(db_session, "T-1")
    section = _section(db_session, context)

    ok = _subject(db_session, "CAP460", "mixed", t, lecture=(1, 1), practical=(2, 2))
    section.subjects.append(ok)
    db_session.commit()
    assert _feasible(build(load(db_session, academic_context_id=context.id)))

    section.subjects.remove(ok)
    bad = _subject(db_session, "CAP461", "mixed", t, lecture=(2, 1), practical=(1, 2))
    section.subjects.append(bad)
    db_session.commit()
    assert not _feasible(build(load(db_session, academic_context_id=context.id)))


# -------------------------------------------- the rule outside the solver


def _hand_made_run(db, context, section, subject, teacher, room, slots):
    """A run written directly, so the checker is tested without the solver."""
    run = TimetableRun(academic_context_id=context.id, version=1, status="FEASIBLE",
                       publish_status="DRAFT")
    db.add(run)
    db.commit()
    for block, slot in enumerate(slots, start=1):
        db.add(Assignment(
            run_id=run.id, section_id=section.id, subject_id=subject.id,
            faculty_id=teacher.id, room_id=room.id, timeslot_id=slot.id,
            block_id=block,
            session_type="P" if subject.type == "practical" else "L",
        ))
    db.commit()
    return run


def test_the_independent_checker_catches_a_repeated_lecture(db_session, context):
    """`check_all` re-derives every rule from rows, so it must know this one.

    Without it, a manual edit - which is re-verified by `check_all` before it
    commits - could put a second lecture on a day and nothing would notice.
    """
    _grid(db_session, days=["Monday", "Tuesday"], periods=4)
    room = _room(db_session, "101")
    t = _faculty(db_session, "T-1")
    section = _section(db_session, context)
    subject = _subject(db_session, "ECE281", "theory", t, sessions=2)
    section.subjects.append(subject)
    db_session.commit()
    monday = db_session.query(TimeSlot).filter_by(day="Monday").order_by(
        TimeSlot.period_index).all()

    run = _hand_made_run(db_session, context, section, subject, t, room,
                         [monday[0], monday[2]])
    problems = timetable_checker.check_all(db_session, run.id)
    assert any("ECE281" in p and "Monday" in p and "once a day" in p
               for p in problems), problems


def test_the_checker_does_not_flag_a_lab_that_meets_twice_a_day(db_session, context):
    _grid(db_session, days=["Monday"], periods=4)
    room = _room(db_session, "201", kind="lab", capacity=40)
    t = _faculty(db_session, "T-1")
    section = _section(db_session, context, strength=36)
    subject = _subject(db_session, "ECE182", "practical", t, sessions=2)
    section.subjects.append(subject)
    db_session.commit()
    monday = db_session.query(TimeSlot).order_by(TimeSlot.period_index).all()

    run = _hand_made_run(db_session, context, section, subject, t, room,
                         [monday[0], monday[2]])
    assert not [p for p in timetable_checker.check_all(db_session, run.id)
                if "once a day" in p]


def test_validation_refuses_a_theory_subject_that_needs_more_days_than_exist(
    db_session, context
):
    """Six lectures a week on a five-day week cannot be once a day.

    Caught before solving, with a sentence, rather than as a bare INFEASIBLE
    after the whole time budget has been spent proving it.
    """
    _grid(db_session, days=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
          periods=6)
    _room(db_session, "101")
    section = _section(db_session, context)
    _subject(db_session, "ECE281", "theory", _faculty(db_session, "T-1"), sessions=6, section=section)
    db_session.commit()

    report = validation.validate(db_session, academic_context_id=context.id)
    blockers = [c for c in report.checks if not c.ok and c.severity == "blocker"]
    assert report.ready is False
    text = " ".join(c.detail for c in blockers)
    assert "ECE281" in text and "once a day" in text, text


def test_a_manual_move_cannot_put_a_lecture_on_a_day_it_already_has(
    db_session, context
):
    """Preview and apply must agree - a preview that says yes to a move apply
    then refuses is the exact bug this codebase has already had once."""
    _grid(db_session, days=["Monday", "Tuesday"], periods=4)
    _room(db_session, "101")
    section = _section(db_session, context)
    _subject(db_session, "ECE281", "theory", _faculty(db_session, "T-1"), sessions=2, section=section)
    db_session.commit()

    result, run = generate(db_session, context.id, max_seconds=20)
    assert result.ok, result.message
    rows = db_session.query(Assignment).filter(Assignment.run_id == run.id).all()
    by_day = {_slot(db_session, a).day: a for a in rows}
    assert set(by_day) == {"Monday", "Tuesday"}

    # Move Tuesday's lecture to a free Monday period.
    monday_taken = _slot(db_session, by_day["Monday"]).period_index
    target = next(
        s for s in db_session.query(TimeSlot).filter_by(day="Monday")
        if s.period_index != monday_taken
    )
    tuesday = by_day["Tuesday"]

    preview = manual_edit.preview_move(db_session, tuesday.id, target.id)
    assert not preview.ok
    assert any("once a day" in r for r in preview.issues), preview.issues

    outcome = manual_edit.apply_move(db_session, tuesday.id, target.id)
    assert not outcome.ok
    assert any("once a day" in r for r in outcome.issues), outcome.issues


# -------------------------------------------------------- Monday-Friday


def test_nothing_is_ever_scheduled_on_a_saturday(db_session, context):
    """A grid with a Saturday in it does not make Saturday a teaching day.

    Monday has one period and Saturday one. A lab meeting twice would fit
    exactly if Saturday counted, so the only thing that can make this
    unschedulable is Saturday being excluded.
    """
    _grid(db_session, days=["Monday", "Saturday"], periods=1)
    _room(db_session, "201", kind="lab", capacity=40)
    section = _section(db_session, context, strength=36)
    _subject(db_session, "ECE182", "practical", _faculty(db_session, "T-1"),
                 sessions=2, section=section)
    db_session.commit()

    inp = load(db_session, academic_context_id=context.id)
    assert {s.day for s in inp.slots} == {"Monday"}
    assert not _feasible(build(inp))


def test_validation_says_when_the_grid_has_days_that_are_not_used(
    db_session, context
):
    _grid(db_session, days=["Monday", "Tuesday", "Saturday"], periods=4)
    _room(db_session, "101")
    section = _section(db_session, context)
    _subject(db_session, "ECE281", "theory", _faculty(db_session, "T-1"), section=section)
    db_session.commit()

    report = validation.validate(db_session, academic_context_id=context.id)
    warnings = [c for c in report.checks if not c.ok and c.severity == "warning"]
    assert any("Saturday" in c.detail for c in warnings), [c.detail for c in warnings]
    assert report.ready is True


# ------------------------------------------------------------------- BYOD


def test_byod_is_enforced_on_lectures_by_default():
    """BYOD means charging points at the benches - a property of the room -
    so it applies to whatever is taught there, not only to labs."""
    from app.config import Settings

    assert Settings().enforce_byod_on_lectures is True


def test_a_byod_lecture_only_goes_to_a_room_with_charging_points(db_session, context):
    _grid(db_session, days=["Monday"], periods=4)
    _room(db_session, "101", byod=False)
    capable = _room(db_session, "102", byod=True)
    section = _section(db_session, context)
    _subject(db_session, "ECE181", "theory", _faculty(db_session, "T-1"), byod=True, section=section)
    db_session.commit()

    inp = load(db_session, academic_context_id=context.id)
    assert inp.pairs[0].room_ids == [capable.id]


def test_a_byod_lecture_with_no_capable_room_is_reported_not_placed(
    db_session, context
):
    _grid(db_session, days=["Monday"], periods=4)
    _room(db_session, "101", byod=False)
    section = _section(db_session, context)
    _subject(db_session, "ECE181", "theory", _faculty(db_session, "T-1"), byod=True, section=section)
    db_session.commit()

    with pytest.raises(UnschedulablePair):
        build(load(db_session, academic_context_id=context.id))
    report = validation.validate(db_session, academic_context_id=context.id)
    assert report.ready is False


# ------------------------------------------------- faculty daily break
#
# There is no common lunch. Instead every teacher gets a free period on any day
# they teach, so each break falls wherever that teacher's own day puts it. How
# long a run may be is capped below; shorter runs are the objective's
# preference - see `test_soft_constraints.py`.


def _without_break_rule(monkeypatch):
    monkeypatch.setattr(model_module, "_add_faculty_breaks", lambda built: None)


def _repeatable_lab(db, section, code, teacher, sessions):
    """A one-period practical meeting `sessions` times. Labs may repeat in a
    day, so the once-a-day lecture rule cannot interfere with these tests."""
    return _subject(db, code, "practical", teacher, sessions=sessions,
                    section=section)


def test_a_teacher_is_never_left_without_a_free_period(
    db_session, context, monkeypatch
):
    """Five periods, one teacher who would have to fill all five.

    Nothing else in the model objects, so the break rule is the only thing that
    can refuse it - and without the rule the same model has to solve.
    """
    _grid(db_session, days=["Monday"], periods=5)
    _room(db_session, "201", kind="lab", capacity=40)
    section = _section(db_session, context, strength=36)
    _repeatable_lab(db_session, section, "ECE182", _faculty(db_session, "T-1"), 5)

    inp = load(db_session, academic_context_id=context.id)
    assert not _feasible(build(inp)), "T-1 was given a day with no free period"

    _without_break_rule(monkeypatch)
    assert _feasible(build(inp)), (
        "infeasible even without the break rule, so the assertion above did "
        "not test it"
    )


def test_eight_periods_in_a_nine_period_day_leave_a_break(db_session, context):
    """Eight of nine is allowed - one period stays free, which is the rule -
    as long as no run is longer than the cap (below)."""
    _grid(db_session, days=["Monday"], periods=9)
    _room(db_session, "201", kind="lab", capacity=40)
    section = _section(db_session, context, strength=36)
    teacher = _faculty(db_session, "T-1")
    _repeatable_lab(db_session, section, "ECE182", teacher, 8)

    result, run = generate(db_session, context.id, max_seconds=20)
    assert result.ok, result.message
    assert timetable_checker.check_all(db_session, run.id) == []

    busy = sorted(
        _slot(db_session, a).period_index
        for a in db_session.query(Assignment).filter(Assignment.run_id == run.id)
    )
    assert len(busy) == 8
    assert set(range(9)) - set(busy), "T-1 was left no free period"


# ------------------------------------------------- longest run of teaching
#
# The break alone is nearly vacuous on a nine-period day: eight in a row still
# leaves a free period, and the live stress run did exactly that on three days.
# So runs are capped as a rule (`faculty_max_consecutive`, 6), next to the
# break, and preferred shorter by the objective.


def test_a_run_longer_than_the_cap_is_refused_by_the_model(db_session, context):
    """Eight one-period labs on a nine-period day fit the break, but not a cap
    of three: with no run over three, nine periods hold at most seven."""
    _grid(db_session, days=["Monday"], periods=9)
    _room(db_session, "201", kind="lab", capacity=40)
    section = _section(db_session, context, strength=36)
    _repeatable_lab(db_session, section, "ECE182", _faculty(db_session, "T-1"), 8)

    inp = load(db_session, academic_context_id=context.id)
    inp.faculty_max_consecutive = 3
    assert not _feasible(build(inp)), "a run of four or more was allowed under a cap of 3"

    inp.faculty_max_consecutive = 0
    assert _feasible(build(inp)), "infeasible without the cap, so the cap was not tested"


def test_a_generated_timetable_never_has_a_run_over_the_cap(db_session, context):
    from app.config import settings

    _grid(db_session, days=["Monday"], periods=9)
    _room(db_session, "201", kind="lab", capacity=40)
    section = _section(db_session, context, strength=36)
    _repeatable_lab(db_session, section, "ECE182", _faculty(db_session, "T-1"), 8)

    result, run = generate(db_session, context.id, max_seconds=20)
    assert result.ok, result.message
    longest = timetable_checker.longest_faculty_run(db_session, run.id)
    assert 0 < longest <= settings.faculty_max_consecutive == 6
    assert timetable_checker.check_all(db_session, run.id) == []


def test_the_checker_catches_a_run_over_the_cap(db_session, context):
    _grid(db_session, days=["Monday"], periods=9)
    room = _room(db_session, "201", kind="lab", capacity=40)
    t = _faculty(db_session, "T-1")
    section = _section(db_session, context, strength=36)
    subject = _repeatable_lab(db_session, section, "ECE182", t, 7)
    monday = db_session.query(TimeSlot).filter_by(day="Monday").order_by(
        TimeSlot.period_index).all()

    run = _hand_made_run(db_session, context, section, subject, t, room, monday[0:7])
    problems = timetable_checker.check_all(db_session, run.id)
    assert any("7 periods in a row" in p and "Monday" in p for p in problems), problems
    assert timetable_checker.longest_faculty_run(db_session, run.id) == 7


def test_validation_refuses_a_session_longer_than_the_cap(
    db_session, context, monkeypatch
):
    """A session is at most three periods, so under the default cap of six this
    cannot happen; a stricter cap can make it happen, and validation says so."""
    from app.config import settings

    monkeypatch.setattr(settings, "faculty_max_consecutive", 2)
    _grid(db_session, days=["Monday", "Tuesday"], periods=9)
    _room(db_session, "201", kind="lab", capacity=40)
    section = _section(db_session, context, strength=36)
    _subject(db_session, "ECE182", "practical", _faculty(db_session, "T-1"),
             sessions=1, length=3, section=section)

    report = validation.validate(db_session, academic_context_id=context.id)
    check = next(c for c in report.checks if c.id == "faculty_breaks_fit")
    assert not check.ok
    assert "longer than the 2 periods in a row" in check.detail


def test_validation_counts_the_cap_in_a_teachers_weekly_room(
    db_session, context, monkeypatch
):
    from app.config import settings

    _grid(db_session, days=["Monday"], periods=9)
    _room(db_session, "201", kind="lab", capacity=40)
    section = _section(db_session, context, strength=36)
    _repeatable_lab(db_session, section, "ECE182", _faculty(db_session, "T-1"), 8)

    fits = validation.validate(db_session, academic_context_id=context.id)
    assert next(c for c in fits.checks if c.id == "faculty_breaks_fit").ok

    monkeypatch.setattr(settings, "faculty_max_consecutive", 3)
    tight = validation.validate(db_session, academic_context_id=context.id)
    check = next(c for c in tight.checks if c.id == "faculty_breaks_fit")
    assert not check.ok and "at most 7" in check.detail, check.detail


def test_a_manual_change_cannot_create_a_run_over_the_cap(db_session, context):
    _grid(db_session, days=["Monday"], periods=9)
    teacher = _faculty(db_session, "T-1")
    assert manual_edit._break_issue(db_session, teacher, "Monday", set(range(6))) is None
    issue = manual_edit._break_issue(db_session, teacher, "Monday", set(range(7)))
    assert issue and "7 periods in a row" in issue
    # Eight with a gap is two runs, neither over the cap, and one free period.
    assert manual_edit._break_issue(
        db_session, teacher, "Monday", {0, 1, 2, 3, 4, 5, 7, 8}) is None


def test_breaks_fall_at_different_times_and_there_is_no_common_lunch(
    db_session, context
):
    """One section busy every one of nine periods, taught by two teachers.

    The section never rests, so at every period exactly one of the two teachers
    is teaching - which means their breaks *cannot* coincide. And nine of nine
    periods in use means no period is a lunch anyone shares: a fixed lunch at
    P5 would make this unschedulable.
    """
    _grid(db_session, days=["Monday"], periods=9)
    _room(db_session, "201", kind="lab", capacity=40)
    section = _section(db_session, context, strength=36)
    t1, t2 = _faculty(db_session, "T-1"), _faculty(db_session, "T-2")
    _repeatable_lab(db_session, section, "ECE182", t1, 4)
    _repeatable_lab(db_session, section, "ECE183", t2, 5)

    result, run = generate(db_session, context.id, max_seconds=20)
    assert result.ok, result.message
    assert timetable_checker.check_all(db_session, run.id) == []

    rows = db_session.query(Assignment).filter(Assignment.run_id == run.id).all()
    busy = {t1.id: set(), t2.id: set()}
    for a in rows:
        busy[a.faculty_id].add(_slot(db_session, a).period_index)
    all_periods = set(range(9))
    assert busy[t1.id] | busy[t2.id] == all_periods, "a period went unused"
    free_1, free_2 = all_periods - busy[t1.id], all_periods - busy[t2.id]
    assert free_1 and free_2
    assert not free_1 & free_2, "the two teachers were given the same break"


def test_the_checker_flags_a_day_with_no_free_period(db_session, context):
    """Read back from the saved rows, not from anything the solver claimed."""
    _grid(db_session, days=["Monday"], periods=5)
    room = _room(db_session, "201", kind="lab", capacity=40)
    teacher = _faculty(db_session, "T-1")
    section = _section(db_session, context, strength=36)
    subject = _repeatable_lab(db_session, section, "ECE182", teacher, 5)
    p = db_session.query(TimeSlot).order_by(TimeSlot.period_index).all()

    filled = _hand_made_run(db_session, context, section, subject, teacher, room,
                            p[0:5])
    problems = timetable_checker.check_all(db_session, filled.id)
    assert any("T-1" in x and "free period" in x for x in problems), problems

    db_session.query(Assignment).delete()
    db_session.query(TimetableRun).delete()
    db_session.commit()
    # Four of five, all in a row, is fine: the rule is the free period.
    with_break = _hand_made_run(db_session, context, section, subject, teacher, room,
                                p[0:4])
    assert not [x for x in timetable_checker.check_all(db_session, with_break.id)
                if "FACULTY BREAK" in x]


def test_validation_says_when_a_teacher_cannot_get_a_break(db_session, context):
    """41 periods on a 45-period week fits the week but not the breaks: one
    free period a day leaves eight of nine, so a week holds 40.

    Refused before solving, with a sentence, because an INFEASIBLE after the
    whole time budget would not say why.
    """
    _grid(db_session, days=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
          periods=9)
    for n in ("201", "202", "203", "204", "205"):
        _room(db_session, n, kind="lab", capacity=40)
    teacher = _faculty(db_session, "T-1")
    for i, sessions in enumerate((9, 8, 8, 8, 8)):
        section = _section(db_session, context, number=f"S{i}", strength=36)
        _repeatable_lab(db_session, section, f"LAB{i}", teacher, sessions)

    report = validation.validate(db_session, academic_context_id=context.id)
    blockers = [c for c in report.checks if not c.ok and c.severity == "blocker"]
    assert report.ready is False
    text = " ".join(c.detail for c in blockers)
    assert "T-1" in text and "free period" in text, text


def test_a_manual_move_cannot_take_away_the_only_free_period(db_session, context):
    """T-1 teaches two of Monday's three periods and one on Tuesday. Moving
    the Tuesday class into Monday's last free period would fill Monday.

    A move within one day can never fill it - the count does not change - so
    the class has to come from another day, which is exactly how a person
    tidying a timetable would produce this. Nothing else objects to Monday P3:
    the section, the room and the teacher are all free then, so a refusal can
    only come from the break rule, and preview and apply have to say the same
    thing.
    """
    _grid(db_session, days=["Monday", "Tuesday"], periods=3)
    room = _room(db_session, "201", kind="lab", capacity=40)
    teacher = _faculty(db_session, "T-1")
    section = _section(db_session, context, strength=36)
    subject = _repeatable_lab(db_session, section, "ECE182", teacher, 3)
    p = db_session.query(TimeSlot).order_by(TimeSlot.day_index, TimeSlot.period_index).all()
    monday, tuesday = p[0:3], p[3:6]

    run = _hand_made_run(db_session, context, section, subject, teacher, room,
                         [monday[0], monday[1], tuesday[0]])
    away = next(
        a for a in db_session.query(Assignment).filter(Assignment.run_id == run.id)
        if a.timeslot_id == tuesday[0].id
    )

    preview = manual_edit.preview_move(db_session, away.id, monday[2].id)
    assert not preview.ok
    assert any("free period" in i for i in preview.issues), preview.issues

    outcome = manual_edit.apply_move(db_session, away.id, monday[2].id)
    assert not outcome.ok
    assert any("free period" in i for i in outcome.issues), outcome.issues
