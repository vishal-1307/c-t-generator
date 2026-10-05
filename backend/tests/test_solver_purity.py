"""The solver must not need a database in order to solve.

Importing `app.solver.model` used to pull in SQLAlchemy, `app.models`,
`app.config` and `app.database` - and `app.database` calls `create_engine` at
import time, while `app.config` refuses to load in production without a JWT
secret and a CORS list. So anything that wanted to solve first had to be able
to reach a database and satisfy web configuration, neither of which the solver
uses.

That is now untangled, and these tests are what keep it untangled. The import
check runs in a subprocess deliberately: inside the test process the whole
application is already imported, so `sys.modules` there proves nothing.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.config import settings
from app.solver.params import SolverParams

BACKEND = Path(__file__).resolve().parent.parent

PURE_MODULES = [
    "app.domain",
    "app.solver.types",
    "app.solver.params",
    "app.solver.model",
    "app.solver.objective",
    "app.solver.solve",
]

FORBIDDEN = ["sqlalchemy", "app.models", "app.database", "app.config"]


def _import_in_subprocess(modules: list[str]) -> set[str]:
    """Import `modules` in a fresh interpreter, return which FORBIDDEN ones came too."""
    script = (
        "import sys\n"
        f"for m in {modules!r}:\n"
        "    __import__(m)\n"
        f"print(','.join(m for m in {FORBIDDEN!r} if m in sys.modules))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        # No DATABASE_URL, no JWT secret: a module that needs one will say so.
        env={
            "PATH": __import__("os").environ.get("PATH", ""),
            "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
            "APPDATA": __import__("os").environ.get("APPDATA", ""),
            "USERPROFILE": __import__("os").environ.get("USERPROFILE", ""),
            "LOCALAPPDATA": __import__("os").environ.get("LOCALAPPDATA", ""),
        },
    )
    assert proc.returncode == 0, (
        f"importing the solver failed outside the application:\n{proc.stderr}"
    )
    out = proc.stdout.strip()
    return set(out.split(",")) if out else set()


def test_the_solver_imports_without_a_database():
    leaked = _import_in_subprocess(PURE_MODULES)
    assert not leaked, (
        "the solver reached the application through its imports: "
        + ", ".join(sorted(leaked))
        + ". Something in app/solver/ now imports the ORM or the settings; the "
        "point of solver/types.py and solver/params.py is that it does not."
    )


def test_the_loader_is_allowed_to_need_a_database():
    """The other half of the boundary: `data.load` reads rows, so it may."""
    leaked = _import_in_subprocess(["app.solver.data"])
    assert "sqlalchemy" in leaked, (
        "app.solver.data no longer imports SQLAlchemy - if the loader moved, "
        "this test is checking the wrong module."
    )


def test_solver_defaults_match_the_application_settings():
    """`SolverParams` restates config.py's defaults, so pin them together.

    Two places holding the same numbers drift. This fails the moment one moves
    without the other, which is the only way the restatement stays honest.
    """
    defaults = SolverParams()
    assert defaults.max_seconds == settings.solver_max_seconds
    assert defaults.workers == settings.solver_workers
    assert defaults.max_memory_mb == settings.solver_max_memory_mb
    assert defaults.probing_level == settings.solver_probing_level
    assert defaults.random_seed == settings.solver_random_seed
    assert defaults.w_gap == settings.w_gap
    assert defaults.w_spread == settings.w_spread
    assert defaults.w_repeat == settings.w_repeat
    assert defaults.w_long_run == settings.w_long_run
    assert defaults.w_proximity == settings.w_proximity


def test_deployment_configuration_still_reaches_the_solver():
    """A pure `solve()` must not mean an unconfigurable one.

    Render sets SOLVER_WORKERS=1 and SOLVER_MAX_SECONDS=180 as environment
    variables. If `from_settings` stopped being applied, the solver would
    silently run with library defaults on the deployed instance - eight workers
    on a 0.1-CPU box - and nothing would report it.
    """

    class FakeSettings:
        solver_max_seconds = 180.0
        solver_workers = 1
        solver_max_memory_mb = 256
        solver_probing_level = 2
        solver_random_seed = 7
        w_gap = 11
        w_spread = 12
        w_repeat = 13
        w_long_run = 14
        w_proximity = 15

    p = SolverParams.from_settings(FakeSettings())
    assert p.max_seconds == 180.0
    assert p.workers == 1
    assert p.max_memory_mb == 256
    assert p.probing_level == 2
    assert p.random_seed == 7
    assert (p.w_gap, p.w_spread, p.w_repeat, p.w_long_run, p.w_proximity) == (11, 12, 13, 14, 15)
