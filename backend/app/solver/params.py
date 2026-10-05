"""The knobs a solve runs with, as an argument rather than a global.

`solve()` used to read eight values straight off `settings`, which meant it
could only run somewhere `app.config` could be imported - and `app.config`
refuses to load in production without a JWT secret and a CORS list, neither of
which has anything to do with scheduling. Passing them in makes the solver a
function of its arguments.

The defaults here are the same numbers as `config.Settings`, and
`tests/test_solver_purity.py` asserts that field by field, so the two cannot
drift apart quietly. The reasoning behind each value lives in `config.py`'s
docstrings - this module deliberately does not restate it, because a copied
explanation is one that stops being true.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SolverParams:
    max_seconds: float = 60.0
    workers: int = 1
    max_memory_mb: int = 0
    probing_level: int = 0
    random_seed: int | None = None
    w_gap: int = 25
    w_spread: int = 3
    w_repeat: int = 5
    w_long_run: int = 15
    w_proximity: int = 1

    @classmethod
    def from_settings(cls, settings) -> SolverParams:
        """Build from an `app.config.Settings`.

        This is the only place the two representations meet, so it is the only
        thing that has to change when a knob is added.
        """
        return cls(
            max_seconds=settings.solver_max_seconds,
            workers=settings.solver_workers,
            max_memory_mb=settings.solver_max_memory_mb,
            probing_level=settings.solver_probing_level,
            random_seed=settings.solver_random_seed,
            w_gap=settings.w_gap,
            w_spread=settings.w_spread,
            w_repeat=settings.w_repeat,
            w_long_run=settings.w_long_run,
            w_proximity=settings.w_proximity,
        )
