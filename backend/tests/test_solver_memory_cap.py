"""CP-SAT's `max_memory_in_mb`: off by default, because it was measured and
it does not do what it was added to do.

It was introduced to stop a large context OOM-killing the whole Render process
(the orphaned-run symptom app/jobs.py::reconcile_orphaned_runs() recovers
from - see test_orphaned_runs.py). Measured afterwards against the real BCA
model (11 sections / 154 solver pairs / 407 periods, ~207k variables) on
ortools 9.15, one worker:

    max_memory_in_mb=200 -> peak RSS 1175MB, status UNKNOWN
    max_memory_in_mb=400 -> peak RSS 1404MB, status OPTIMAL
    unset                -> peak RSS 1427MB, status OPTIMAL

It bounds neither the process nor the solver - peak ran to 3-6x the number
given - and the tighter setting cost the run its answer, turning a model that
solves to OPTIMAL into UNKNOWN. A cap that cannot prevent the crash but can
lose a valid timetable is strictly worse than no cap.

These tests pin two things: that the default is off, and that the wiring is
still correct and consistent across every CpSolver this codebase creates, so
the setting can be switched on without a code change if a future OR-Tools
honours it. Full measurements: SOLVER_SCALING_INVESTIGATION.md.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from ortools.sat.python import cp_model

from app.config import settings
from app.models import AcademicContext, Faculty, Room, Section, Subject
from app.solver.data import load
from app.solver.diagnostics import find_conflicting_pairs, largest_schedulable_subset
from app.solver.run import generate

# What OR-Tools itself defaults to when nothing overrides it.
ORTOOLS_DEFAULT_MB = 10000


def test_the_memory_cap_is_off_by_default():
    """It was measured as unable to bound memory and able to lose answers."""
    assert settings.solver_max_memory_mb == 0


@pytest.fixture
def tiny(db_session):
    """One trivially schedulable pair - fast regardless of what solver
    parameters are under test."""
    from app.grid import build_grid

    ctx = AcademicContext(academic_year="2026-27", semester=1, program="BCA",
                          department="CSE")
    db_session.add(ctx)
    db_session.flush()

    subject = Subject(name="CS201", code="CS201", type="theory",
                      session_length_hours=1, sessions_per_week=1)
    faculty = Faculty(name="F1", faculty_code="F1")
    section = Section(academic_context_id=ctx.id, section_number="S1", strength=10)
    room = Room(room_number="A1", block="A", capacity=70, room_type="theory")
    db_session.add_all([subject, faculty, section, room, *build_grid()])
    db_session.commit()
    faculty.subjects.append(subject)
    section.subjects.append(subject)
    db_session.commit()
    return ctx


def _capture_memory_param(monkeypatch, attr="Solve"):
    """Patches CpSolver.<attr> to record parameters.max_memory_in_mb without
    depending on how long a real solve takes."""
    captured: dict[str, int] = {}
    real = getattr(cp_model.CpSolver, attr)

    def fake(self, *args, **kwargs):
        captured["max_memory_in_mb"] = self.parameters.max_memory_in_mb
        return real(self, *args, **kwargs)

    monkeypatch.setattr(cp_model.CpSolver, attr, fake)
    return captured


def test_the_default_leaves_or_tools_own_ceiling_untouched(
    db_session, tiny, monkeypatch
):
    """0 must mean "don't override", not a literal 0MB cap that would make
    every solve fail instantly - the `if settings.solver_max_memory_mb:` guard
    at each call site exists for exactly this."""
    captured = _capture_memory_param(monkeypatch)
    result, _run = generate(db_session, tiny.id, soft=False, max_seconds=5)
    assert result.status in ("OPTIMAL", "FEASIBLE"), result.message
    assert captured["max_memory_in_mb"] == ORTOOLS_DEFAULT_MB


def test_generate_still_honours_an_explicit_setting(db_session, tiny, monkeypatch):
    """Turning it back on must remain a config change, not a code change."""
    captured = _capture_memory_param(monkeypatch)
    with patch.object(settings, "solver_max_memory_mb", 400):
        result, _run = generate(db_session, tiny.id, soft=False, max_seconds=5)
    assert result.status in ("OPTIMAL", "FEASIBLE"), result.message
    assert captured["max_memory_in_mb"] == 400


def test_diagnostics_find_conflicting_pairs_uses_the_same_setting(
    db_session, tiny, monkeypatch
):
    captured = _capture_memory_param(monkeypatch)
    with patch.object(settings, "solver_max_memory_mb", 400):
        inp = load(db_session, tiny.id)
        find_conflicting_pairs(inp, max_seconds=2)
    assert captured["max_memory_in_mb"] == 400


def test_diagnostics_largest_schedulable_subset_uses_the_same_setting(
    db_session, tiny, monkeypatch
):
    captured = _capture_memory_param(monkeypatch)
    with patch.object(settings, "solver_max_memory_mb", 400):
        inp = load(db_session, tiny.id)
        largest_schedulable_subset(inp, max_seconds=2)
    assert captured["max_memory_in_mb"] == 400
