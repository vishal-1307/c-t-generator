"""The two-file teacher import, against the department's real files.

The fixtures here are the actual `Load.xlsx` and `Infra.xlsx` shapes, typos
included - `Strenght`, `Faculty  UID` with two spaces - because those are what
will be uploaded, and a test that quietly fixes them first would be testing a
file that does not exist.

Faculty names and IDs are pseudonyms. Everything else - subject codes,
sections, strengths, types, BYOD, durations and the room list - is the
department's own data, because that is what the scheduling depends on. The
real files themselves are tested by `test_teacher_real_files.py`, which reads
them from outside the repository.

What matters most is not that a well-formed file imports. It is that a
malformed one is *reported against the row it came from*: everything after
parsing happens on sheets generated in memory, and "Sections row 4" is not
something anyone can act on.
"""
from __future__ import annotations

import io

import openpyxl
import pytest

from app.models import Faculty, Room, Section, Subject
from app.teacher_import import explode, read_sheet

# The department's layout, typos included, plus the Classes Per Week column
# every real load file now has to carry. The counts are 4 throughout, which is
# what the department's timetable has always been built on - stated in the
# file now, where it used to be assumed.
LOAD_HEADERS = [
    "Name of faculty", "Faculty  UID", "Subject Name", "Subject Code",
    "Section", "Strenght", "Type", "BYOD", "No of Hrs/Duration", "Classes Per Week",
]
LOAD_ROWS = [
    ["Faculty A", 90001, "Embedded systems", "ECE181", 2401, 68, "Class", "Yes", 1, 4],
    ["Faculty B", 90002, "Internet of Things", "ECE281", 2402, 38, "Class", "No", 1, 4],
    ["Faculty C", 90003, "Communication Systems", "ECE101", 2403, 71, "Class", "Yes", 1, 4],
    ["Faculty A", 90001, "Embedded systems lab", "ECE182", 24011, 36, "Lab", "Yes", 2, 4],
    ["Faculty C", 90003, "Communication Systems lab", "ECE102", 24031, 36, "Lab", "Yes", 3, 4],
    ["Faculty D", 90004, "Microprocessors ", "ECE310", 2502, 72, "Class", "No", 1, 4],
    ["Faculty E", 90005, "Micro controllers", "ECE322", 2503, 72, "Class", "No", 1, 4],
    ["Faculty F", 90006, "Communication systems", "ECE151", 2405, 72, "Class", "No", 1, 4],
    ["Faculty G", 90007, "Signals and systems ", "ECE212", 2323, 120, "Class", "No", 2, 4],
]

INFRA_HEADERS = ["Block", "Room", "Strenght", "Type", "BYOD"]
INFRA_ROWS = (
    [[36, 300 + i, cap, "Class room", byod] for i, (cap, byod) in enumerate(
        [(72, "Yes"), (60, "No"), (72, "Yes"), (60, "No"), (72, "Yes"),
         (72, "No"), (60, "Yes"), (72, "No"), (120, "Yes")], start=1)]
    + [[36, 400 + i, cap, "Class room", byod] for i, (cap, byod) in enumerate(
        [(72, "No"), (60, "Yes"), (72, "No"), (60, "Yes"), (72, "No"),
         (72, "Yes"), (60, "No"), (72, "Yes"), (120, "No")], start=1)]
    + [[33, 100 + i, 36, "Lab", "Yes"] for i in range(1, 13)]
)

CONTEXT = {
    "academic_year": "2025-26", "semester": 1,
    "program": "B.Tech ECE", "department": "Electronics",
}


def _xlsx(headers, rows) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def files():
    return _xlsx(LOAD_HEADERS, LOAD_ROWS), _xlsx(INFRA_HEADERS, INFRA_ROWS)


def _exploded(load_bytes, infra_bytes, **overrides):
    kwargs = dict(
        academic_year="2025-26", semester=1, program="B.Tech ECE",
        department="Electronics",
    )
    kwargs.update(overrides)
    return explode(
        read_sheet(load_bytes, "Load.xlsx"),
        read_sheet(infra_bytes, "Infra.xlsx"),
        **kwargs,
    )


def _post(client, path, files_, **form):
    load_bytes, infra_bytes = files_
    return client.post(
        path,
        files={
            "load": ("Load.xlsx", load_bytes, "application/vnd.ms-excel"),
            "infra": ("Infra.xlsx", infra_bytes, "application/vnd.ms-excel"),
        },
        data={**CONTEXT, **form},
    )


# ------------------------------------------------------- reading the files


def test_one_load_row_becomes_five_records(files):
    result = _exploded(*files)
    assert result.ok, [p.message for p in result.problems]

    counts = {name: len(rows) for name, (_, rows) in result.sheets.items()}
    assert counts == {
        "Rooms": 30,
        "Faculty": 7,             # Faculty A and Faculty C each teach twice
        "Subjects": 9,
        "Sections": 9,
        "Faculty and subjects": 9,
        "Sections and subjects": 9,
        "Section groups": 2,      # 24011 and 24031
        "Academic context": 1,
    }


def test_duration_is_a_session_length_and_frequency_comes_from_the_file(files):
    """The distinction the whole import turns on.

    "No of Hrs/Duration" is how long one session runs, not how often it runs.
    Read the other way, ECE102's three-period lab becomes three separate
    one-period classes scattered across the week - a timetable that is wrong
    and still looks completely plausible.
    """
    result = _exploded(*files)
    subjects = {r["subject_code"]: r for _, r in
                zip(range(99), result.sheets["Subjects"][1])}

    assert subjects["ECE102"]["duration_slots"] == "3"
    assert subjects["ECE182"]["duration_slots"] == "2"
    assert subjects["ECE181"]["duration_slots"] == "1"
    links = result.sheets["Sections and subjects"][1]
    assert {r["sessions_per_week"] for r in links} == {"4"}


# ------------------------------------------------------- classes per week


def _with_per_week(counts: dict[int, object] | None = None, *, drop_column=False):
    """The fixture load, with some rows' Classes Per Week changed, or the
    column taken out altogether."""
    headers = list(LOAD_HEADERS)
    rows = [list(r) for r in LOAD_ROWS]
    for i, value in (counts or {}).items():
        rows[i][-1] = value
    if drop_column:
        headers = headers[:-1]
        rows = [r[:-1] for r in rows]
    return _xlsx(headers, rows), _xlsx(INFRA_HEADERS, INFRA_ROWS)


def test_a_file_without_classes_per_week_is_refused_as_a_whole():
    """Nothing in the file can say how often a class meets, so nothing is
    guessed: one problem, naming the column, not one per row."""
    result = _exploded(*_with_per_week(drop_column=True))
    assert not result.ok
    assert len(result.problems) == 1
    assert "Classes Per Week" in result.problems[0].message


def test_each_row_must_give_a_whole_number(files):
    result = _exploded(*_with_per_week({0: "", 1: "four", 2: 0, 5: 2.5}))
    messages = sorted((p.row_number, p.message) for p in result.problems)
    assert [row for row, _ in messages] == [2, 3, 4, 7]
    assert all("Classes Per Week" in m for _, m in messages)


def test_two_sections_can_take_one_subject_a_different_number_of_times(
    db_session, client
):
    rows = [list(r) for r in LOAD_ROWS]
    rows.append(["Faculty H", 90008, "Embedded systems", "ECE181", 2402, 38,
                 "Class", "No", 1, 3])
    rows[0][-1] = 5
    files_ = (_xlsx(LOAD_HEADERS, rows), _xlsx(INFRA_HEADERS, INFRA_ROWS))
    body = _post(client, "/api/teacher/apply", files_).json()
    assert body["committed"] is True, body

    from app.models import Section, SectionSubject
    counts = {
        (link.section.section_number, link.subject.code): link.sessions_per_week
        for link in db_session.query(SectionSubject).all()
    }
    assert counts[("2401", "ECE181")] == 5
    assert counts[("2402", "ECE181")] == 3

    # And the solver is asked for exactly those.
    from app.solver.data import load
    inp = load(db_session, academic_context_id=body["academic_context_id"])
    sessions = {(p.section_number, p.subject_code): p.sessions for p in inp.pairs}
    assert sessions[("2401", "ECE181")] == 5
    assert sessions[("2402", "ECE181")] == 3


def test_one_section_given_two_counts_for_one_subject_is_refused():
    rows = [list(r) for r in LOAD_ROWS]
    rows.append(list(rows[0]))
    rows[-1][-1] = 3
    result = _exploded(_xlsx(LOAD_HEADERS, rows), _xlsx(INFRA_HEADERS, INFRA_ROWS))
    assert any("4 times" in p.message or "3 times" in p.message for p in result.problems), \
        [p.message for p in result.problems]


def test_the_summary_counts_classes_from_each_rows_own_number(files):
    base = _exploded(*files).summary
    changed = _exploded(*_with_per_week({0: 5, 3: 2})).summary
    # ECE181 for 2401 goes 4 -> 5 (+1 class, +1 period); ECE182 is a 2-period
    # lab going 4 -> 2 (-2 classes, -4 periods).
    assert changed["classes"] == base["classes"] + 1 - 2
    assert changed["required_periods"] == base["required_periods"] + 1 - 4


def test_class_and_lab_become_theory_and_practical(files, db_session, client):
    resp = _post(client, "/api/teacher/apply", files)
    assert resp.status_code == 200, resp.text
    assert resp.json()["committed"] is True

    by_code = {s.code: s for s in db_session.query(Subject).all()}
    assert by_code["ECE181"].type == "theory"
    assert by_code["ECE182"].type == "practical"
    assert by_code["ECE102"].session_length_hours == 3
    assert by_code["ECE102"].sessions_per_week == 4


def test_rooms_get_a_code_so_two_rooms_numbered_101_differ(files, db_session, client):
    resp = _post(client, "/api/teacher/apply", files)
    assert resp.status_code == 200, resp.text

    rooms = db_session.query(Room).all()
    assert len(rooms) == 30
    assert {r.room_type for r in rooms} == {"theory", "lab"}
    assert len([r for r in rooms if r.room_type == "lab"]) == 12
    codes = {r.room_code for r in rooms}
    assert "36-301" in codes and "33-101" in codes


def test_a_group_is_linked_to_the_section_it_belongs_to(files, db_session, client):
    resp = _post(client, "/api/teacher/apply", files)
    assert resp.status_code == 200, resp.text

    by_number = {s.section_number: s for s in db_session.query(Section).all()}
    assert by_number["24011"].parent_section_id == by_number["2401"].id
    assert by_number["24031"].parent_section_id == by_number["2403"].id
    # And a section that merely looks like a group of nothing is left alone.
    assert by_number["2502"].parent_section_id is None


def test_what_was_inferred_is_said_out_loud(files, client):
    """A convention read off a number is a conclusion, not a fact in the file."""
    resp = _post(client, "/api/teacher/preview", files)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    inferences = " ".join(body["inferences"])
    assert "24011 as a lab group of 2401" in inferences
    assert "24031 as a lab group of 2403" in inferences
    # Expected behaviour, so not presented as something to fix.
    assert not any("lab group of" in w for w in body["warnings"])


def test_group_sizes_that_do_not_add_up_are_reported(files, client):
    """2401 has 68 students and one listed group of 36. Half are unaccounted for."""
    resp = _post(client, "/api/teacher/preview", files)
    warnings = " ".join(resp.json()["warnings"])
    assert "2401 has 68 students" in warnings
    # The two halves of a section are its groups 1 and 2; the file lists only
    # the first of each, so the second is named - not invented.
    assert "No rows for 24012: section 2401 has 68 students" in warnings
    assert "No rows for 24032: section 2403" in warnings
    assert "32 of 2401's students have no lab" in warnings
    # Said once: the general readiness check does not repeat it.
    readiness_warnings = resp.json()["readiness"]["warnings"]
    assert not [w for w in readiness_warnings if w["label"] == "Lab group sizes match their section"]
    # A warning, not a refusal: a partial file is legitimate.
    assert resp.json()["can_apply"] is True


def test_preview_writes_nothing(files, client, db_session):
    resp = _post(client, "/api/teacher/preview", files)
    assert resp.status_code == 200, resp.text
    assert resp.json()["committed"] is False
    assert db_session.query(Subject).count() == 0
    assert db_session.query(Room).count() == 0
    assert db_session.query(Section).count() == 0


def test_importing_twice_changes_nothing(files, client, db_session):
    first = _post(client, "/api/teacher/apply", files)
    assert first.status_code == 200, first.text
    assert first.json()["counts"]["creates"] > 0

    second = _post(client, "/api/teacher/apply", files)
    assert second.status_code == 200, second.text
    counts = second.json()["counts"]
    assert counts["creates"] == 0
    assert counts["updates"] == 0
    assert db_session.query(Subject).count() == 9
    assert db_session.query(Section).count() == 9
    assert db_session.query(Faculty).count() == 7


# ------------------------------------------------- disagreements in the file


def test_two_strengths_for_one_section_is_reported_not_resolved(files):
    """Only the department can say which is right. Picking one would be a guess
    that produces a timetable built on a number nobody chose."""
    rows = [list(r) for r in LOAD_ROWS]
    rows[1][4] = 2401       # same section as row 2 ...
    rows[1][5] = 40         # ... with a different size
    result = _exploded(_xlsx(LOAD_HEADERS, rows), files[1])

    assert not result.ok
    problem = result.problems[0]
    assert problem.file == "Load.xlsx"
    assert problem.row_number == 3          # the second data row
    assert "40 students here" in problem.message
    assert "68" in problem.message


def test_one_code_describing_two_subjects_is_reported(files):
    rows = [list(r) for r in LOAD_ROWS]
    rows[1][3] = "ECE181"   # reuse row 1's code ...
    rows[1][8] = 2          # ... with a different session length
    result = _exploded(_xlsx(LOAD_HEADERS, rows), files[1])

    assert not result.ok
    assert "described differently" in result.problems[0].message
    assert "duration_slots" in result.problems[0].message


def test_two_names_under_one_faculty_uid_is_reported(files):
    rows = [list(r) for r in LOAD_ROWS]
    rows[3][0] = "F. A."      # UID 90001 is "Faculty A" on row 2
    result = _exploded(_xlsx(LOAD_HEADERS, rows), files[1])

    assert not result.ok
    assert "faculty UID 90001" in result.problems[0].message


def test_the_same_room_listed_twice_is_reported(files):
    rows = [list(r) for r in INFRA_ROWS]
    rows.append([36, 301, 72, "Class room", "Yes"])
    result = _exploded(files[0], _xlsx(INFRA_HEADERS, rows))

    assert not result.ok
    assert "listed twice" in result.problems[0].message


def test_a_yes_no_column_that_says_something_else_is_reported(files):
    rows = [list(r) for r in LOAD_ROWS]
    rows[0][7] = "maybe"
    result = _exploded(_xlsx(LOAD_HEADERS, rows), files[1])

    assert not result.ok
    assert "should say Yes or No" in result.problems[0].message
    assert result.problems[0].row_number == 2


def test_contradictions_are_refused_by_the_endpoint(files, client, db_session):
    rows = [list(r) for r in LOAD_ROWS]
    rows[1][4] = 2401
    rows[1][5] = 40
    resp = _post(client, "/api/teacher/apply",
                 (_xlsx(LOAD_HEADERS, rows), files[1]))

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["problems"][0]["file"] == "Load.xlsx"
    assert detail["problems"][0]["row_number"] == 3
    assert db_session.query(Section).count() == 0


def test_a_problem_points_at_the_teachers_row_not_a_generated_one(
    files, client, db_session
):
    """The importer validates sheets that only ever existed in memory.

    Left as it comes, an error would say "Sections row 4" - a row of a file the
    teacher does not have. This is the one thing an error message has to get
    right, so it is asserted rather than assumed.
    """
    rows = [list(r) for r in LOAD_ROWS]
    rows[5][5] = -5      # Microprocessors, row 7: an impossible strength
    resp = _post(client, "/api/teacher/preview",
                 (_xlsx(LOAD_HEADERS, rows), files[1]))
    assert resp.status_code == 200, resp.text

    body = resp.json()
    problems = [p for o in body["outcomes"] for p in o["problems"]]
    assert problems, "an impossible strength should have been rejected"
    assert any(
        p["sheet"] == "Load.xlsx" and p["row_number"] == 7 for p in problems
    ), problems


def test_an_unknown_column_is_reported_rather_than_silently_dropped(files):
    headers = LOAD_HEADERS + ["Preferred Room"]
    rows = [[*r, "36-301"] for r in LOAD_ROWS]
    result = _exploded(_xlsx(headers, rows), files[1])

    assert result.ok
    # The reader lower-cases headers, so the note echoes the column as it was
    # read rather than as it was typed.
    assert any("preferred room" in n.lower() for n in result.notes)


# ------------------------------------------- rooms that already exist


def test_a_room_already_known_by_its_floor_is_matched_not_duplicated(
    files, client, db_session
):
    """The room list has no floor column; the database usually knows one.

    Room identity is (block, floor, room_number), so "36 / 301" and
    "36 / floor 3 / 301" look like different rooms - while the table's unique
    constraint on (block, room_number) says they cannot both exist. Left alone
    that is an IntegrityError on apply: a 500 at the end of an all-or-nothing
    import, with nothing said about which room caused it.

    They are the same room. The file simply does not carry the floor.
    """
    db_session.add(Room(
        room_number="301", block="36", floor="3", room_code="36-301",
        capacity=72, room_type="theory", is_active=True, byod=True, charging=True,
    ))
    db_session.commit()

    resp = _post(client, "/api/teacher/apply", files)
    assert resp.status_code == 200, resp.text
    assert resp.json()["committed"] is True

    matched = db_session.query(Room).filter(
        Room.block == "36", Room.room_number == "301"
    ).all()
    assert len(matched) == 1, "the existing room was duplicated instead of matched"
    assert matched[0].floor == "3", "the floor the database knew was discarded"
    assert db_session.query(Room).count() == 30


# ------------------------------------------------ changes to existing rooms


def _changes_for(resp, room):
    entry = next((c for c in resp.json()["room_changes"] if c["room"] == room), None)
    assert entry is not None, f"{room} was not reported: {resp.json()['room_changes']}"
    return {c["field"]: (c["before"], c["after"]) for c in entry["changes"]}


def test_changing_what_an_existing_room_is_gets_said_out_loud(
    files, client, db_session
):
    """36-309 is a faculty office here and a 120-seat classroom in the file.

    An import that quietly turned one into the other would change what another
    timetable is allowed to use, and nothing would report it. So each changed
    field is listed with its before and after, in the form a person notices:
    "Capacity 8 -> 120".
    """
    db_session.add(Room(
        room_number="309", block="36", floor="3", room_code="36-309",
        capacity=8, room_type="faculty", is_active=True, byod=False,
    ))
    db_session.commit()

    resp = _post(client, "/api/teacher/preview", files)
    assert resp.status_code == 200, resp.text
    changes = _changes_for(resp, "36-309")
    assert changes["room_type"] == ("Faculty room", "Classroom")
    assert changes["capacity"] == ("8", "120")
    assert changes["byod"] == ("No", "Yes")


def test_a_room_that_changes_only_in_byod_is_still_reported(files, client, db_session):
    """BYOD - charging points at the benches - decides which classes a room may
    host, exactly as capacity does, so it is listed like capacity is."""
    db_session.add(Room(
        room_number="302", block="36", floor="3", room_code="36-302",
        capacity=60, room_type="theory", is_active=True, byod=True, charging=True,
    ))
    db_session.commit()

    resp = _post(client, "/api/teacher/preview", files)
    assert _changes_for(resp, "36-302") == {"byod": ("Yes", "No")}


def test_a_room_the_file_agrees_with_is_not_listed(files, client, db_session):
    db_session.add(Room(
        room_number="301", block="36", floor="3", room_code="36-301",
        capacity=72, room_type="theory", is_active=True, byod=True,
    ))
    db_session.commit()

    resp = _post(client, "/api/teacher/preview", files)
    assert not [c for c in resp.json()["room_changes"] if c["room"] == "36-301"]


def test_an_out_of_use_room_the_file_lists_is_reported_as_back_in_use(
    files, client, db_session
):
    db_session.add(Room(
        room_number="301", block="36", floor="3", room_code="36-301",
        capacity=72, room_type="theory", is_active=False, byod=True,
    ))
    db_session.commit()

    resp = _post(client, "/api/teacher/preview", files)
    assert _changes_for(resp, "36-301") == {"is_active": ("No", "Yes")}


def test_an_import_does_not_blank_what_the_file_does_not_say(
    files, client, db_session
):
    """The room list has no floor, charging, sockets or lab type.

    The room importer writes every column it is handed and blanks the rest, so
    without care an import would silently strip those from every existing room
    it names. This one is a lab with a type, charging and a socket count - none
    of which the file mentions - and all of it has to survive.
    """
    db_session.add(Room(
        room_number="101", block="33", floor="1", room_code="33-101",
        capacity=36, room_type="lab", lab_type="electronics", is_active=True,
        byod=True, charging=True, charging_sockets=30,
    ))
    db_session.commit()

    resp = _post(client, "/api/teacher/apply", files)
    assert resp.status_code == 200, resp.text
    assert resp.json()["committed"] is True

    db_session.expire_all()
    room = db_session.query(Room).filter_by(block="33", room_number="101").one()
    assert room.floor == "1"
    assert room.lab_type == "electronics"
    assert room.charging is True
    assert room.charging_sockets == 30
    assert room.room_code == "33-101"
    assert db_session.query(Room).filter_by(block="33", room_number="101").count() == 1


# ------------------------------------------------------- what it adds up to


def test_the_summary_counts_what_the_files_contain(files, client):
    resp = _post(client, "/api/teacher/preview", files)
    assert resp.json()["summary"] == {
        "faculty": 7,
        "subjects": 9,
        "sections": 9,         # seven sections and two lab groups
        "groups": 2,
        "classes": 36,         # nine rows, four sessions a week each
        # 4 sessions a week x each row's own duration: 7x4x1 + 2x4x2 ... = 52
        "required_periods": 52,
        "rooms": 30,
        "classrooms": 18,
        "labs": 12,
        "byod_rooms": 21,
    }


# ------------------------------------------ readiness, checked for real


def test_the_preview_runs_the_full_check_and_still_writes_nothing(
    files, client, db_session
):
    """"Data is ready" has to mean the solver can run, not only that the files
    parsed. So the preview imports inside a transaction, runs the same check
    that guards generation, and rolls back."""
    resp = _post(client, "/api/teacher/preview", files)
    body = resp.json()
    assert body["readiness"] is not None
    assert body["readiness"]["ready"] is True, body["readiness"]["blockers"]
    assert body["committed"] is False

    db_session.expire_all()
    assert db_session.query(Section).count() == 0
    assert db_session.query(Room).count() == 0
    from app.models import AcademicContext, TimeSlot
    assert db_session.query(AcademicContext).count() == 0
    assert db_session.query(TimeSlot).count() == 0


def test_a_file_that_imports_cleanly_can_still_be_not_ready(client):
    """A section of 130 fits the files' own rules but no room in the list:
    the files are fine, the timetable is impossible, and the preview says so
    before anyone presses Generate."""
    rows = [list(r) for r in LOAD_ROWS]
    big = next(r for r in rows if r[4] == 2323)
    big[5] = 130
    resp = _post(client, "/api/teacher/preview",
                 (_xlsx(LOAD_HEADERS, rows), _xlsx(INFRA_HEADERS, INFRA_ROWS)))
    body = resp.json()
    assert body["has_errors"] is False
    assert body["readiness"]["ready"] is False
    text = " ".join(b["detail"] for b in body["readiness"]["blockers"])
    assert "ECE212" in text and "130" in text, text


# ------------------------------------------------- the teaching week


def test_an_empty_database_gets_a_week_with_no_common_lunch(files, client, db_session):
    from app.models import TimeSlot

    resp = _post(client, "/api/teacher/apply", files)
    assert resp.json()["committed"] is True
    assert any("default is used" in w for w in resp.json()["warnings"])

    db_session.expire_all()
    slots = db_session.query(TimeSlot).all()
    assert len(slots) == 45
    assert {s.day for s in slots} == {
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}
    assert not [s for s in slots if s.is_lunch]


def test_a_common_lunch_is_announced_and_then_removed(files, client, db_session):
    """There is no common lunch; each teacher's break comes from the
    four-in-a-row rule. A week that still marks one is told, then fixed."""
    from app.grid import build_grid
    from app.models import TimeSlot

    slots = build_grid()
    for slot in slots:
        if slot.period_index == 4:
            slot.is_lunch = True
    db_session.add_all(slots)
    db_session.commit()

    preview = _post(client, "/api/teacher/preview", files)
    assert any("P5" in w and "no common lunch" in w for w in preview.json()["warnings"])
    db_session.expire_all()
    assert db_session.query(TimeSlot).filter_by(is_lunch=True).count() == 5, (
        "the preview changed the week"
    )

    _post(client, "/api/teacher/apply", files)
    db_session.expire_all()
    assert db_session.query(TimeSlot).filter_by(is_lunch=True).count() == 0


def test_apply_says_which_intake_it_wrote_and_whether_it_is_ready(files, client):
    body = _post(client, "/api/teacher/apply", files).json()
    assert body["committed"] is True
    assert isinstance(body["academic_context_id"], int)
    assert body["readiness"]["ready"] is True, body["readiness"]["blockers"]


def test_applying_still_records_the_import(files, client, db_session):
    """The grid is set up inside the import's transaction through a hook, not
    by skipping the importer's commit - which would also have skipped the
    history row that says who imported what."""
    from app.models import ImportHistory

    _post(client, "/api/teacher/apply", files)
    db_session.expire_all()
    history = db_session.query(ImportHistory).all()
    assert [h.status for h in history] == ["applied"]
