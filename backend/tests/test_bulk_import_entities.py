"""Phase 10 PARTS 3-11: bulk import for faculty, subjects, sections, the two
mapping tables, and availability - all built on the same reusable engine
Phase 9 built for rooms (app/bulk_import.py), one adapter per entity.
"""
from __future__ import annotations

import io

from app.models import (
    AcademicContext,
    Faculty,
    FacultySubject,
    FacultyUnavailability,
    Section,
    SectionSubject,
    Subject,
)


def upload(client, entity: str, content: str, filename: str, action: str, mode: str = "add_update", confirm=False):
    form = {"mode": mode}
    if action == "apply":
        form["confirm_deactivations"] = "true" if confirm else "false"
    return client.post(
        f"/api/bulk-import/{entity}/{action}",
        files={"file": (filename, io.BytesIO(content.encode()), "text/csv")},
        data=form,
    )


# ------------------------------------------------------------------- faculty


FACULTY_CSV = (
    "faculty_id,name,department,is_active\n"
    "T-1,Dr. A. Sharma,CSE,true\n"
    "T-4,Dr. B. Iyer,ECE,true\n"
)


def test_faculty_import_creates_by_stable_code(client, db_session):
    resp = upload(client, "faculty", FACULTY_CSV, "f.csv", "apply")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["counts"]["create"] == 2
    rows = db_session.query(Faculty).order_by(Faculty.faculty_code).all()
    assert [(r.faculty_code, r.name) for r in rows] == [("T-1", "Dr. A. Sharma"), ("T-4", "Dr. B. Iyer")]


def test_faculty_import_updates_by_code_not_name(client, db_session):
    upload(client, "faculty", FACULTY_CSV, "f.csv", "apply")
    renamed = "faculty_id,name,department,is_active\nT-1,Dr. A. Sharma (renamed),CSE,true\n"
    resp = upload(client, "faculty", renamed, "f2.csv", "apply")
    body = resp.json()
    assert body["counts"]["update"] == 1
    assert db_session.query(Faculty).count() == 2  # no duplicate created
    row = db_session.query(Faculty).filter(Faculty.faculty_code == "T-1").one()
    assert row.name == "Dr. A. Sharma (renamed)"


def test_faculty_missing_name_is_invalid(client):
    resp = upload(client, "faculty", "faculty_id,name\nT-9,\n", "f.csv", "preview")
    body = resp.json()
    assert body["counts"]["invalid"] == 1
    assert "name is required" in body["rows"][0]["message"]


def test_faculty_full_sync_deactivates_absent_faculty_without_deleting(client, db_session):
    upload(client, "faculty", FACULTY_CSV, "f.csv", "apply")
    only_one = "faculty_id,name,department,is_active\nT-1,Dr. A. Sharma,CSE,true\n"
    resp = upload(client, "faculty", only_one, "f3.csv", "apply", mode="full_sync", confirm=True)
    assert resp.json()["committed"] is True
    assert db_session.query(Faculty).count() == 2
    t4 = db_session.query(Faculty).filter(Faculty.faculty_code == "T-4").one()
    assert t4.is_active is False


# ------------------------------------------------------------------ subjects


SUBJECTS_CSV = (
    "subject_code,subject_name,type,sessions_per_week,duration_slots,required_lab_type,is_active\n"
    "CS201,Data Structures,theory,3,1,,true\n"
    "CS251,DS Lab,practical,1,2,COMPUTING,true\n"
)


def test_subject_import_creates_by_code(client, db_session):
    resp = upload(client, "subjects", SUBJECTS_CSV, "s.csv", "apply")
    assert resp.json()["counts"]["create"] == 2
    lab = db_session.query(Subject).filter(Subject.code == "CS251").one()
    assert lab.name == "DS Lab"
    assert lab.type == "practical"
    assert lab.session_length_hours == 2
    assert lab.required_lab_type == "COMPUTING"


def test_a_theory_or_practical_subjects_own_name_is_never_lost(client, db_session):
    """A subject that isn't mixed still has to keep its own name.

    Found on the real demo workbook: every theory subject in it came back
    named the literal string "practical_per_week", and every practical
    subject would have come back "lecture_per_week" - the column label for
    whichever component that subject does *not* have, not anything a person
    typed.

    The cause was a local `name, value = ...` unpacking, meant only to build a
    validation-error message, that shadowed the subject's real `name` from
    earlier in the same function - unconditionally, whether or not an error
    was actually about to be raised. It ran for every non-mixed subject, which
    is the common case, not an edge one; the row that reproduces it needs no
    lecture_per_week/practical_per_week columns at all, because the bug fires
    on their *absence* just as surely as on their presence.
    """
    csv = "\n".join([
        "subject_code,subject_name,type,sessions_per_week",
        "CS301,Operating Systems,theory,3",
        "CS351,Operating Systems Lab,practical,1",
        "",
    ])
    resp = upload(client, "subjects", csv, "s.csv", "apply")
    assert resp.json()["counts"]["create"] == 2

    theory = db_session.query(Subject).filter(Subject.code == "CS301").one()
    practical = db_session.query(Subject).filter(Subject.code == "CS351").one()
    assert theory.name == "Operating Systems"
    assert practical.name == "Operating Systems Lab"
    # The specific corrupted values this bug produced, named explicitly so a
    # future refactor that reintroduces the shadowing fails loudly rather than
    # merely differently.
    assert theory.name != "practical_per_week"
    assert practical.name != "lecture_per_week"


def test_the_real_demo_workbooks_theory_subjects_keep_their_names(client, db_session):
    """The exact failure as found, via the exact file it was found in."""
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "demo_workbook.xlsx"
    data = fixture.read_bytes()
    r = client.post(
        "/api/bulk-import/workbook/apply",
        files={"file": ("demo.xlsx", data,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["committed"] is True

    theory_subjects = db_session.query(Subject).filter(Subject.type == "theory").all()
    assert theory_subjects, "the fixture must still contain theory subjects for this to test anything"
    for subject in theory_subjects:
        assert subject.name not in ("practical_per_week", "lecture_per_week", ""), (
            f"{subject.code} has a column name instead of a subject name: {subject.name!r}"
        )


def test_subject_invalid_type_is_rejected(client):
    resp = upload(client, "subjects", "subject_code,subject_name,type\nX1,Foo,lecture\n", "s.csv", "preview")
    row = resp.json()["rows"][0]
    assert row["verdict"] == "invalid"
    assert "type must be one of" in row["message"]


def test_subject_lab_type_on_theory_is_rejected(client):
    resp = upload(
        client, "subjects",
        "subject_code,subject_name,type,required_lab_type\nX1,Foo,theory,COMPUTING\n",
        "s.csv", "preview",
    )
    row = resp.json()["rows"][0]
    assert row["verdict"] == "invalid"
    assert "required_lab_type" in row["message"]


def test_subject_duration_out_of_range_is_rejected(client):
    resp = upload(
        client, "subjects",
        "subject_code,subject_name,type,duration_slots\nX1,Foo,theory,5\n",
        "s.csv", "preview",
    )
    row = resp.json()["rows"][0]
    assert row["verdict"] == "invalid"
    assert "duration_slots must be between 1 and 3" in row["message"]


# ------------------------------------------------------------------ sections


def test_section_import_requires_an_existing_academic_context(client):
    csv = "section_number,academic_year,semester,program,department,strength\nD2402,2026-27,1,BCA,CSE,60\n"
    resp = upload(client, "sections", csv, "sec.csv", "preview")
    row = resp.json()["rows"][0]
    assert row["verdict"] == "invalid"
    assert "does not exist" in row["message"]


def test_section_import_creates_within_the_right_context(client, db_session, context):
    csv = (
        f"section_number,academic_year,semester,program,department,strength\n"
        f"D2402,{context.academic_year},{context.semester},{context.program},{context.department},60\n"
    )
    resp = upload(client, "sections", csv, "sec.csv", "apply")
    assert resp.json()["counts"]["create"] == 1
    row = db_session.query(Section).filter(Section.section_number == "D2402").one()
    assert row.academic_context_id == context.id


def test_same_section_number_in_two_different_semesters_is_not_the_same_section(client, db_session, context):
    """Spec PART 6's literal example: D2402/Semester 1 and D2402/Semester 3
    must be two distinct sections, never merged."""
    other_ctx = AcademicContext(
        academic_year=context.academic_year, semester=context.semester + 2,
        program=context.program, department=context.department,
    )
    db_session.add(other_ctx)
    db_session.commit()

    csv = (
        "section_number,academic_year,semester,program,department,strength\n"
        f"D2402,{context.academic_year},{context.semester},{context.program},{context.department},60\n"
        f"D2402,{other_ctx.academic_year},{other_ctx.semester},{other_ctx.program},{other_ctx.department},55\n"
    )
    resp = upload(client, "sections", csv, "sec.csv", "apply")
    body = resp.json()
    assert body["counts"]["create"] == 2  # not treated as a duplicate
    rows = db_session.query(Section).filter(Section.section_number == "D2402").all()
    assert len(rows) == 2
    assert {r.academic_context_id for r in rows} == {context.id, other_ctx.id}


# --------------------------------------------------------- faculty <-> subject


def test_faculty_subject_mapping_requires_both_sides_to_exist(client):
    resp = upload(client, "faculty_subjects", "faculty_id,subject_code\nT-1,ECE281\n", "m.csv", "preview")
    body = resp.json()
    assert body["counts"]["invalid"] == 1
    assert "faculty code" in body["rows"][0]["message"]
    assert "subject code" in body["rows"][0]["message"]


def test_faculty_subject_mapping_reports_every_missing_code_specifically(client, db_session):
    """Spec PART 11's exact worked example shape: name which codes are missing."""
    db_session.add(Faculty(name="Dr. A", faculty_code="T-1"))
    db_session.commit()
    resp = upload(client, "faculty_subjects", "faculty_id,subject_code\nT-1,ECE281\n", "m.csv", "preview")
    row = resp.json()["rows"][0]
    assert "ECE281" in row["message"]
    assert "does not exist" in row["message"]


def test_faculty_subject_mapping_creates_when_both_exist(client, db_session):
    fac = Faculty(name="Dr. A", faculty_code="T-1")
    subj = Subject(name="Circuits", code="ECE281", type="theory")
    db_session.add_all([fac, subj])
    db_session.commit()

    resp = upload(client, "faculty_subjects", "faculty_id,subject_code\nT-1,ECE281\n", "m.csv", "apply")
    assert resp.json()["counts"]["create"] == 1
    assert db_session.query(FacultySubject).count() == 1


def test_faculty_subject_duplicate_mapping_is_unchanged_not_error(client, db_session):
    fac = Faculty(name="Dr. A", faculty_code="T-1")
    subj = Subject(name="Circuits", code="ECE281", type="theory")
    db_session.add_all([fac, subj])
    db_session.commit()
    upload(client, "faculty_subjects", "faculty_id,subject_code\nT-1,ECE281\n", "m.csv", "apply")

    resp = upload(client, "faculty_subjects", "faculty_id,subject_code\nT-1,ECE281\n", "m2.csv", "apply")
    assert resp.json()["counts"]["unchanged"] == 1
    assert db_session.query(FacultySubject).count() == 1


def test_faculty_subject_full_sync_removes_mapping_but_not_faculty_or_subject(client, db_session):
    fac = Faculty(name="Dr. A", faculty_code="T-1")
    s1 = Subject(name="Circuits", code="ECE281", type="theory")
    s2 = Subject(name="Signals", code="ECE305", type="theory")
    db_session.add_all([fac, s1, s2])
    db_session.commit()
    upload(client, "faculty_subjects", "faculty_id,subject_code\nT-1,ECE281\nT-1,ECE305\n", "m.csv", "apply")
    assert db_session.query(FacultySubject).count() == 2

    resp = upload(
        client, "faculty_subjects", "faculty_id,subject_code\nT-1,ECE281\n", "m2.csv",
        "apply", mode="full_sync", confirm=True,
    )
    assert resp.json()["committed"] is True
    assert db_session.query(FacultySubject).count() == 1
    # neither the faculty nor either subject was touched
    assert db_session.query(Faculty).count() == 1
    assert db_session.query(Subject).count() == 2


# --------------------------------------------------------- section <-> subject


def test_section_subject_mapping_end_to_end(client, db_session, context):
    section = Section(academic_context_id=context.id, section_number="D2402", strength=60)
    subject = Subject(name="Data Structures", code="CS201", type="theory")
    db_session.add_all([section, subject])
    db_session.commit()

    csv = (
        "academic_year,semester,program,department,section_number,subject_code\n"
        f"{context.academic_year},{context.semester},{context.program},{context.department},D2402,CS201\n"
    )
    resp = upload(client, "section_subjects", csv, "ss.csv", "apply")
    assert resp.json()["counts"]["create"] == 1
    assert db_session.query(SectionSubject).count() == 1
    db_session.refresh(section)
    assert subject in section.subjects


def test_section_subject_mapping_rejects_unknown_section(client, db_session, context):
    subject = Subject(name="Data Structures", code="CS201", type="theory")
    db_session.add(subject)
    db_session.commit()
    csv = (
        "academic_year,semester,program,department,section_number,subject_code\n"
        f"{context.academic_year},{context.semester},{context.program},{context.department},NO-SUCH,CS201\n"
    )
    resp = upload(client, "section_subjects", csv, "ss.csv", "preview")
    row = resp.json()["rows"][0]
    assert row["verdict"] == "invalid"
    # Asserted by what the message identifies rather than its exact wording,
    # which changed when the sheet stopped having to repeat its context.
    assert "NO-SUCH" in row["message"]
    assert "not found" in row["message"] or "does not exist" in row["message"]


# --------------------------------------------------------------- availability


def test_availability_import_blocks_a_faculty_slot(client, db_session, context):
    from app.grid import build_grid

    db_session.add_all(build_grid())
    fac = Faculty(name="Dr. A", faculty_code="T-1")
    db_session.add(fac)
    db_session.commit()

    csv = "entity_type,entity_identifier,day,slot,reason\nfaculty,T-1,Monday,1,Meeting\n"
    resp = upload(client, "availability", csv, "avail.csv", "apply")
    assert resp.json()["counts"]["create"] == 1
    row = db_session.query(FacultyUnavailability).one()
    assert row.faculty_id == fac.id
    assert row.timeslot.day == "Monday"
    assert row.timeslot.period_index == 0
    assert row.reason == "Meeting"


def test_availability_import_rejects_unknown_day(client, db_session):
    from app.grid import build_grid

    db_session.add_all(build_grid())
    fac = Faculty(name="Dr. A", faculty_code="T-1")
    db_session.add(fac)
    db_session.commit()

    csv = "entity_type,entity_identifier,day,slot\nfaculty,T-1,Funday,1\n"
    resp = upload(client, "availability", csv, "avail.csv", "preview")
    row = resp.json()["rows"][0]
    assert row["verdict"] == "invalid"
    assert "no configured time slot" in row["message"]


def test_availability_import_does_not_silently_ignore_an_out_of_range_slot(client, db_session):
    """Spec PART 9: 'Do not silently ignore unknown slots.'"""
    from app.grid import build_grid

    db_session.add_all(build_grid())  # 9 periods/day by default
    fac = Faculty(name="Dr. A", faculty_code="T-1")
    db_session.add(fac)
    db_session.commit()

    csv = "entity_type,entity_identifier,day,slot\nfaculty,T-1,Monday,99\n"
    resp = upload(client, "availability", csv, "avail.csv", "preview")
    row = resp.json()["rows"][0]
    assert row["verdict"] == "invalid"
    assert "no configured time slot" in row["message"]


def test_availability_full_sync_unblocks_a_slot_absent_from_the_file(client, db_session):
    from app.grid import build_grid

    db_session.add_all(build_grid())
    fac = Faculty(name="Dr. A", faculty_code="T-1")
    db_session.add(fac)
    db_session.commit()

    csv = "entity_type,entity_identifier,day,slot\nfaculty,T-1,Monday,1\nfaculty,T-1,Tuesday,2\n"
    upload(client, "availability", csv, "avail.csv", "apply")
    assert db_session.query(FacultyUnavailability).count() == 2

    keep_one = "entity_type,entity_identifier,day,slot\nfaculty,T-1,Monday,1\n"
    resp = upload(client, "availability", keep_one, "avail2.csv", "apply", mode="full_sync", confirm=True)
    assert resp.json()["committed"] is True
    remaining = db_session.query(FacultyUnavailability).all()
    assert len(remaining) == 1
    assert remaining[0].timeslot.day == "Monday"
    # faculty itself is untouched
    assert db_session.query(Faculty).count() == 1
