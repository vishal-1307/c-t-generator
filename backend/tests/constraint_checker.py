"""Re-export of the independent hard-constraint checker.

Phase 6 promoted the implementation to ``app/timetable_checker.py`` so the
manual-edit service could reuse it - "one implementation, not two" for
exactly the same rules this test suite already trusts. This module keeps the
old import path working (``from constraint_checker import check_all, ...``)
for every existing test file without touching them.
"""
from __future__ import annotations

from app.timetable_checker import (  # noqa: F401
    check_all,
    one_faculty_per_pair,
    working_days_are_within,
)
