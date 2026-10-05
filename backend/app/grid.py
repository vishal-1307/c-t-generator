"""Weekly grid construction, shared by the seed script and the seed endpoint.

The solver reads contiguity (hard constraint 9) straight off
``(day_index, period_index)``, so there is exactly one place that decides how a
week is laid out. Keeping this in one function stops the API and the demo seed
from drifting apart.

Lunch is deliberately **not** decided here. The generator lays down a uniform
run of back-to-back periods and marks none of them as lunch; which slot is the
break is a per-slot toggle the admin sets on the Time Slots page. That keeps the
break position a data decision rather than something baked into the generator.
"""
from __future__ import annotations

import datetime as dt

from .models import TimeSlot


# The working week is Monday to Friday - confirmed by the department, and
# TIMETABLE_LOGIC_SPEC.md #17. A grid *may* contain other days (an admin can
# seed one), but nothing is ever scheduled on them: the solver, the validator
# and the checker all read the week through `is_working_day`.
DEFAULT_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
WORKING_DAYS = frozenset(DEFAULT_DAYS)

# Matched on the first three letters, so "Mon", "Tues" and "Thursday" all
# count. Grids in the wild name days both ways, and the first version of this
# check compared full names only - which silently dropped every slot of a grid
# written "Mon".."Fri" and left nothing to schedule.
_WORKING_STEMS = frozenset(day[:3].lower() for day in DEFAULT_DAYS)


def is_working_day(day: str) -> bool:
    """Whether classes may be scheduled on this day of a grid."""
    return str(day).strip().lower()[:3] in _WORKING_STEMS

# 9 x 50min from 09:30 lands exactly on 17:00.
DEFAULT_PERIODS = 9
DEFAULT_START_HOUR = 9
DEFAULT_START_MINUTE = 30
DEFAULT_PERIOD_MINUTES = 50


def build_grid(
    days: list[str] | None = None,
    periods: int = DEFAULT_PERIODS,
    start_hour: int = DEFAULT_START_HOUR,
    start_minute: int = DEFAULT_START_MINUTE,
    period_minutes: int = DEFAULT_PERIOD_MINUTES,
) -> list[TimeSlot]:
    """Build one week of back-to-back slots.

    ``periods`` is the total number of slots per day - every one of them is
    teachable on creation. No gaps are inserted between periods: each starts
    where the previous ended.
    """
    days = DEFAULT_DAYS if days is None else days
    slots: list[TimeSlot] = []
    delta = dt.timedelta(minutes=period_minutes)

    for day_index, day in enumerate(days):
        cursor = dt.datetime(2000, 1, 1, start_hour, start_minute)
        for period_index in range(periods):
            end = cursor + delta
            slots.append(
                TimeSlot(
                    day=day,
                    day_index=day_index,
                    period_index=period_index,
                    start_time=cursor.time(),
                    end_time=end.time(),
                    is_lunch=False,
                )
            )
            cursor = end

    return slots
