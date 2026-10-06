"""Application settings, loaded from environment / .env.

Phase 13 introduced an explicit ``APP_ENV``. Before it, every production-unsafe
default (a random JWT key, localhost CORS, a well-known bootstrap password) was
guarded only by a warning that a deployment could ignore. The problem with
warnings is that nobody reads them at 2am, and every one of those defaults is
individually reasonable in development - so the fix is not to remove them but
to make the *environment* decide which apply.

``APP_ENV=development`` (the default) keeps a fresh checkout working with no
setup at all. ``APP_ENV=production`` refuses to start until the unsafe defaults
have been replaced, and says exactly which ones and how. Failing at startup is
the point: a misconfigured deployment that boots is far more dangerous than one
that does not.
"""
import os
import secrets
import warnings

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_CORS_DEFAULT = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001"


def normalise_database_url(url: str) -> str:
    """Return ``url`` with a PostgreSQL driver SQLAlchemy can actually load.

    Managed providers (Render, Heroku, and others) hand out ``postgres://`` or
    ``postgresql://``. SQLAlchemy has no driver of its own, and for a bare
    ``postgresql://`` it defaults to the **psycopg2** dialect - which this
    project does not install, because it uses psycopg v3. The failure is
    `ModuleNotFoundError: No module named 'psycopg2'`, which points at a
    missing package rather than at the URL that selected it.

    Applied as a field validator on ``Settings.database_url``, so the value is
    already correct everywhere it is read. That placement is deliberate: an
    earlier version normalised in a separate ``sqlalchemy_url`` property, and
    Alembic's env.py went on reading the raw ``database_url`` and selected
    psycopg2 in production. One value that is always right cannot be got wrong
    at a new call site; two values where only one is right will be.

    Only the leading scheme is rewritten, so a password that happens to
    contain a scheme-like substring is untouched. An explicit ``+driver`` is
    always respected, and non-PostgreSQL URLs (SQLite) pass through unchanged.
    """
    url = url.strip()
    for scheme in ("postgres://", "postgresql://"):
        if url.startswith(scheme):
            return "postgresql+psycopg://" + url[len(scheme):]
    return url


class ConfigurationError(RuntimeError):
    """Raised at startup when production configuration is unsafe or missing.

    Deliberately fatal. Everything it reports is something that would
    otherwise become a silent security weakness in a running system.
    """


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # development | production. Anything else is rejected at startup rather
    # than silently treated as development, since a typo in the one setting
    # that gates every other guard should not fail open.
    app_env: str = "development"

    database_url: str = "sqlite:///./timetable.db"

    # Phase 10: JWT signing key. The generated fallback is random *per process
    # start*, so restarting the API invalidates every existing token and two
    # replicas would each mint tokens the other rejects. Fine for a dev
    # checkout; refused outright in production (see _check_production).
    jwt_secret_key: str = secrets.token_urlsafe(48)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12  # 12 hours

    # Both spellings of the dev host: a browser treats localhost:3000 and
    # 127.0.0.1:3000 as different origins, and developers use them
    # interchangeably - allowing only one silently breaks every API call from
    # the other with a CORS error that looks like a hung request.
    cors_origins: str = DEV_CORS_DEFAULT

    # Phase 13: the first-run admin account. In development these defaults let
    # a fresh checkout log in immediately. In production the bootstrap runs
    # only if BOTH are set explicitly - otherwise no account is created at all
    # and the operator is told how to create one. A well-known password must
    # never be reachable on a deployed system.
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str | None = None
    # Only used when app_env is development.
    dev_bootstrap_admin_password: str = "admin123"

    # Upload ceiling for every file this API accepts.
    # One setting so the two can never drift apart.
    max_upload_bytes: int = 10 * 1024 * 1024

    # Solver defaults (phases 3-5). Overridable per generate request.
    # Workers=4 is safe on Standard plan (2GB RAM). Override via
    # SOLVER_WORKERS env var: set to 1 for Free tier (512MB), 8 for larger.
    solver_max_seconds: float = 120.0
    solver_workers: int = 4
    # DEFAULT 0 = do not set CP-SAT's max_memory_in_mb, because measurement
    # showed it does not do what it was added to do.
    #
    # It was introduced to stop a large context OOM-killing the whole process
    # on the 512MB free instance. Measured against the real BCA model
    # (11 sections / 154 solver pairs / 407 periods, ~207k variables) on
    # ortools 9.15, one worker:
    #
    #   max_memory_in_mb=200 -> peak RSS 1175MB, status UNKNOWN
    #   max_memory_in_mb=400 -> peak RSS 1404MB, status OPTIMAL
    #   unset                -> peak RSS 1427MB, status OPTIMAL
    #
    # So it bounds neither the process nor the solver - peak ran to 3-6x the
    # figure given - while the tighter value cost the run its answer: the same
    # model that solves to OPTIMAL uncapped came back UNKNOWN. A limit that
    # cannot prevent the crash but can lose a valid timetable is worse than no
    # limit, so it is off by default.
    #
    # Kept as a setting (rather than deleted) so an existing
    # SOLVER_MAX_MEMORY_MB in a deployment's environment stays valid, and so
    # a future OR-Tools that honours it can be switched on without a code
    # change. The real ceiling is the instance: see
    # SOLVER_SCALING_INVESTIGATION.md for the measured memory-vs-size curve
    # and what each context actually needs.
    solver_max_memory_mb: int = 0
    # 0 disables CP-SAT's probing presolve pass. Phase 5 measured this as a
    # pure presolve-effort dial (never changes what is feasible - probing only
    # tries to derive additional implied clauses to help search, it does not
    # touch the model's constraints) that consistently and reproducibly halved
    # solve time on this problem's structure across multiple seeds at 4 and 12
    # sections, including one seed that flipped UNKNOWN -> OPTIMAL within the
    # same budget. See TIMETABLE_LOGIC_SPEC.md's solver-scalability section.
    solver_probing_level: int = 0
    # None = nondeterministic (CP-SAT's normal multi-worker portfolio search).
    # Set to an int for reproducible benchmarking/debugging - pins the search
    # to a single deterministic worker, per Phase 5 spec item 17. Must stay
    # None in normal operation: semester persistence comes from
    # SectionSubjectAssignment, never from a fixed seed standing in for it.
    solver_random_seed: int | None = None

    # Soft-constraint weights (phase 5).
    #
    # (There is deliberately no default for how many times a week a class
    # meets. It used to be 4, applied to any teaching load that did not say -
    # a number nobody chose for any particular class. The load file now states
    # Classes Per Week on every row, and a file without it is refused.)

    # Whether a lecture whose subject needs BYOD has to be in a BYOD room.
    #
    # On. The department confirmed what BYOD means: charging points at the
    # benches, so students can power their own devices. That is a property of
    # the room, and it matters to a lecture exactly as much as to a lab - a
    # BYOD class in a room without charging points is simply in the wrong room.
    #
    # It stays a setting because it used to be off (capability filtering was
    # first written for practicals only), and an institution whose BYOD mark
    # means something weaker can turn it back. `tests/test_byod_on_lectures.py`
    # pins both positions.
    enforce_byod_on_lectures: bool = True

    # How long a run of teaching the department would rather not exceed.
    #
    # A *preference*, weighted in the objective (`w_long_run`), not a rule: a
    # fifth period in a row is avoided where possible, never a reason to refuse
    # a timetable. 0 turns the preference off.
    faculty_soft_max_consecutive: int = 4

    # Failed sign-ins allowed per address and per username within the window
    # before further attempts are refused with 429. 0 turns the limit off.
    login_max_attempts: int = 10
    login_window_seconds: int = 15 * 60

    # The longest run of teaching allowed at all - a *rule*.
    #
    # The break (a free period on any day a teacher teaches) is nearly vacuous
    # on a nine-period day: a teacher could teach eight in a row, and the live
    # stress run did exactly that on three days, because the preference above
    # is outweighed by a single gap in a section's day. So past this length a
    # run is not a matter of taste. 0 removes the cap.
    faculty_max_consecutive: int = 6

    # Gap weight is the highest because students' #1 complaint is idle
    # periods between classes. At 50 the solver will strongly prefer
    # compact schedules over every other soft preference.
    w_gap: int = 50
    w_spread: int = 3
    w_repeat: int = 5
    # What a period over the preferred run length costs, against the others.
    # Above the gap weight: an 8-in-a-row day for a teacher is worse than one
    # idle period in a section's day, and at 2 the solver traded them freely.
    w_long_run: int = 15
    # Per block a section uses beyond its first, and per step between the
    # lowest and highest block numbers it uses. Below the gap weight on
    # purpose: a compact day for students matters more than a shorter walk.
    w_proximity: int = 1

    # Phase 13: the demo dataset and its reset endpoint. Off in production by
    # default - a "reset the data" button has no business existing on a system
    # holding a real semester's timetable.
    enable_demo_data: bool = True

    # Gemini AI assistant settings
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.6-flash"

    @field_validator("database_url")
    @classmethod
    def _normalise_database_url(cls, v: str) -> str:
        """Normalise at the boundary, so no reader can pick the wrong form."""
        return normalise_database_url(v)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"

    @property
    def demo_features_enabled(self) -> bool:
        """Demo seeding and reset are development affordances.

        Enabled explicitly in production only if someone sets both flags -
        which a demo deployment for the teacher legitimately might want, so it
        is possible rather than forbidden, but never the default.
        """
        return self.enable_demo_data and not self.is_production


settings = Settings()


def _is_loopback(origin: str) -> bool:
    host = origin.split("://", 1)[-1].split(":")[0].strip("[]")
    return host in ("localhost", "127.0.0.1", "::1")


def _check_cors_origins(s: Settings) -> list[str]:
    """Validate CORS_ORIGINS strictly, because getting it wrong is silent.

    A browser sends `Origin: https://app.vercel.app` and the middleware does a
    byte-for-byte comparison against this list. Anything that does not match
    exactly - a trailing slash, a missing scheme, a path - fails in the
    browser with an opaque CORS error and a backend that looks perfectly
    healthy in its own logs.

    Only the untouched development default used to be rejected, which caught
    the one case where nobody had tried yet and let every near-miss boot into
    a deployment no front end could call.
    """
    raw = s.cors_origins.strip()

    if raw == DEV_CORS_DEFAULT:
        return [
            "CORS_ORIGINS is still the development default (localhost:3000). "
            "Set it to the real front-end origin(s), comma-separated - e.g. "
            "https://your-app.vercel.app"
        ]

    origins = s.cors_origin_list
    if not origins:
        return [
            "CORS_ORIGINS is empty. With no allowed origin the API refuses "
            "every browser request, so the deployment would look healthy and "
            "be unusable. Set it to the front-end origin, e.g. "
            "https://your-app.vercel.app"
        ]

    problems: list[str] = []
    for origin in origins:
        if not origin.startswith(("http://", "https://")):
            problems.append(
                f"CORS_ORIGINS entry {origin!r} has no scheme. An origin is "
                "exactly scheme://host[:port] - the browser sends "
                "'https://your-app.vercel.app' and the comparison is literal."
            )
            continue
        if origin.endswith("/"):
            problems.append(
                f"CORS_ORIGINS entry {origin!r} has a trailing slash. The "
                "browser's Origin header never has one, so this would match "
                "nothing. Drop the slash."
            )
            continue
        rest = origin.split("://", 1)[1]
        if any(c in rest for c in "/?#"):
            problems.append(
                f"CORS_ORIGINS entry {origin!r} includes a path or query. An "
                "origin is scheme://host[:port] only."
            )

    if not problems and all(_is_loopback(o) for o in origins):
        problems.append(
            "Every CORS_ORIGINS entry is a loopback address. A deployed "
            "front end is not served from localhost, so no browser could "
            "call this API. Set the real origin (a loopback entry alongside "
            "it is fine for local debugging)."
        )
    return problems


def _check_production(s: Settings) -> list[str]:
    """Every production misconfiguration, collected rather than raised one at
    a time - an operator fixing a deployment wants the whole list, not a
    game of whack-a-mole across four restarts."""
    problems: list[str] = []

    if "JWT_SECRET_KEY" not in os.environ and "jwt_secret_key" not in _dotenv_keys():
        problems.append(
            "JWT_SECRET_KEY is not set. In production it must be an explicit, "
            "stable secret - the development fallback is random per process, so "
            "every login would be invalidated on restart and two replicas would "
            "reject each other's tokens. Generate one with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )

    problems.extend(_check_cors_origins(s))

    if s.bootstrap_admin_password is not None and len(s.bootstrap_admin_password) < 12:
        problems.append(
            "BOOTSTRAP_ADMIN_PASSWORD is shorter than 12 characters."
        )

    if s.bootstrap_admin_password == s.dev_bootstrap_admin_password:
        problems.append(
            "BOOTSTRAP_ADMIN_PASSWORD is the development default. Choose a real one."
        )

    if s.database_url.startswith("sqlite"):
        problems.append(
            "DATABASE_URL is SQLite. Use PostgreSQL in production - SQLite has "
            "no concurrent-writer story and the deployment would corrupt or "
            "block under two API workers."
        )

    return problems


def _dotenv_keys() -> set[str]:
    """Keys present in .env, so a secret set there counts as explicitly
    configured even though it never reaches os.environ."""
    path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(path):
        return set()
    keys = set()
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    keys.add(line.split("=", 1)[0].strip().lower())
    except OSError:
        return set()
    return keys


def validate_startup_configuration(s: Settings = settings) -> None:
    """Called once at import of app.main. Raises in production, warns in dev."""
    env = s.app_env.strip().lower()
    if env not in ("development", "production"):
        raise ConfigurationError(
            f"APP_ENV must be 'development' or 'production', got {s.app_env!r}."
        )

    if s.is_production:
        problems = _check_production(s)
        if problems:
            listing = "\n".join(f"  - {p}" for p in problems)
            raise ConfigurationError(
                "Refusing to start: APP_ENV=production but the configuration "
                f"is not production-safe.\n{listing}\n"
                "See backend/.env.example and DEPLOYMENT.md."
            )
        return

    # Development: the same conditions, as warnings rather than errors, so the
    # gap between a working checkout and a deployable one stays visible.
    if "JWT_SECRET_KEY" not in os.environ and "jwt_secret_key" not in _dotenv_keys():
        warnings.warn(
            "JWT_SECRET_KEY is not set - using a random per-process key. Every "
            "existing login token will be invalidated on restart. Set "
            "JWT_SECRET_KEY before deploying (APP_ENV=production requires it).",
            stacklevel=2,
        )
