"""List endpoints gained search, sort and pagination - without breaking.

Every entity list returned its whole table, which is fine for a demo and wrong
for an institution with thousands of faculty. The contract is opt-in: a request
that sends no pagination parameter gets exactly the array it always got, and
one that sends any of them gets `{total, rows}`.

That "without breaking" half is the part worth testing hardest. Every existing
page, every AI tool and most of this suite call these endpoints with no
parameters, and none of them should be able to tell the change happened.
"""
from __future__ import annotations

import pytest

PAGED = ["/api/faculty", "/api/subjects", "/api/rooms", "/api/sections"]


def _make_faculty(client, n):
    for i in range(n):
        resp = client.post("/api/faculty", json={
            "name": f"Dr Number {i:03d}",
            "faculty_code": f"T-{i:03d}",
            "department": "CSE" if i % 2 else "ECE",
        })
        assert resp.status_code == 201, resp.text


# ------------------------------------------------------- the legacy contract


@pytest.mark.parametrize("path", PAGED)
def test_no_parameters_still_returns_a_plain_array(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list), (
        "an unparameterised list must stay an array - every existing caller "
        "depends on it"
    )


def test_the_array_still_contains_every_row(client):
    _make_faculty(client, 12)
    rows = client.get("/api/faculty").json()
    assert len(rows) == 12


# ------------------------------------------------------------ the envelope


@pytest.mark.parametrize("path", PAGED)
@pytest.mark.parametrize("param", ["limit=5", "offset=0", "q=a", "sort=-"])
def test_any_pagination_parameter_switches_to_the_envelope(client, path, param):
    if param == "sort=-":
        return  # a bare '-' is not a column; covered by the sort tests below
    body = client.get(f"{path}?{param}").json()
    assert isinstance(body, dict)
    assert set(body) == {"total", "rows"}
    assert isinstance(body["rows"], list)


def test_total_counts_everything_matching_not_just_the_page(client):
    _make_faculty(client, 25)
    body = client.get("/api/faculty?limit=10").json()
    assert body["total"] == 25
    assert len(body["rows"]) == 10


def test_paging_walks_every_row_exactly_once(client):
    _make_faculty(client, 23)
    seen: list[str] = []
    for offset in range(0, 23, 7):
        body = client.get(f"/api/faculty?limit=7&offset={offset}").json()
        seen.extend(r["faculty_code"] for r in body["rows"])
    assert len(seen) == len(set(seen)) == 23


def test_an_offset_past_the_end_is_empty_but_still_reports_the_total(client):
    _make_faculty(client, 5)
    body = client.get("/api/faculty?limit=10&offset=999").json()
    assert body["rows"] == []
    assert body["total"] == 5


# ---------------------------------------------------------------- searching


def test_search_matches_a_substring_case_insensitively(client):
    _make_faculty(client, 3)
    client.post("/api/faculty", json={
        "name": "Professor Ramanujan", "faculty_code": "T-900", "department": "MATH",
    })
    body = client.get("/api/faculty?q=ramanujan").json()
    assert [r["faculty_code"] for r in body["rows"]] == ["T-900"]
    assert body["total"] == 1


def test_search_covers_the_code_and_not_only_the_name(client):
    _make_faculty(client, 5)
    body = client.get("/api/faculty?q=T-003").json()
    assert [r["faculty_code"] for r in body["rows"]] == ["T-003"]


def test_search_narrows_the_total_too(client):
    """A total that ignored the search would make pagination lie."""
    _make_faculty(client, 20)
    body = client.get("/api/faculty?q=CSE-does-not-appear-anywhere").json()
    assert body == {"total": 0, "rows": []}


def test_search_and_paging_compose(client):
    _make_faculty(client, 30)  # 15 CSE, 15 ECE
    body = client.get("/api/faculty?q=CSE&limit=4").json()
    assert body["total"] == 15
    assert len(body["rows"]) == 4


# ----------------------------------------------------------------- sorting


def test_sort_ascending_and_descending(client):
    _make_faculty(client, 6)
    asc = client.get("/api/faculty?sort=faculty_code&limit=100").json()["rows"]
    desc = client.get("/api/faculty?sort=-faculty_code&limit=100").json()["rows"]
    codes = [r["faculty_code"] for r in asc]
    assert codes == sorted(codes)
    assert [r["faculty_code"] for r in desc] == list(reversed(codes))


def test_an_unknown_sort_column_is_refused_with_the_allowed_list(client):
    """Resolving a sort column by name off the model would expose columns that
    are not meant to be sortable, and 500 when the name does not exist."""
    resp = client.get("/api/faculty?sort=hashed_password")
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "hashed_password" in detail
    assert "faculty_code" in detail, "the error should say what IS allowed"


def test_sorting_applies_across_pages_not_within_one(client):
    _make_faculty(client, 20)
    first = client.get("/api/faculty?sort=-faculty_code&limit=5&offset=0").json()["rows"]
    second = client.get("/api/faculty?sort=-faculty_code&limit=5&offset=5").json()["rows"]
    assert min(r["faculty_code"] for r in first) > max(r["faculty_code"] for r in second)


# ---------------------------------------------------- interaction with filters


def test_a_context_filter_narrows_the_total(client, db_session):
    """Sections can be scoped to one academic context. A paged total must
    describe that context, not every section in the institution."""
    from app.models import AcademicContext, Section

    other = AcademicContext(
        academic_year="2027-28", semester=2, program="BSC", department="CS"
    )
    db_session.add(other)
    db_session.commit()

    ctx = client.post("/api/academic-contexts", json={
        "academic_year": "2026-27", "semester": 1,
        "program": "BCA", "department": "CSE",
    }).json()
    for i in range(4):
        client.post("/api/sections", json={
            "academic_context_id": ctx["id"],
            "section_number": f"D240{i}", "strength": 60,
        })
    db_session.add(Section(
        academic_context_id=other.id, section_number="X999", strength=60, is_active=True
    ))
    db_session.commit()

    body = client.get(f"/api/sections?academic_context_id={ctx['id']}&limit=2").json()
    assert body["total"] == 4, "the total must respect the context filter"
    assert len(body["rows"]) == 2


# ------------------------------------------------------------------- limits


def test_a_limit_above_the_ceiling_is_refused(client):
    """An unbounded page size would defeat the point of paginating at all."""
    assert client.get("/api/faculty?limit=100000").status_code == 422


def test_a_negative_offset_is_refused(client):
    assert client.get("/api/faculty?offset=-1").status_code == 422


# ------------------------------------------------------ narrowing by kind


def test_rooms_can_be_narrowed_to_one_kind(client):
    """A screen that only deals in labs should not have to read the whole
    building to find them.

    The mappings screen pins practicals to labs. Without this filter it fetched
    every room in the institution and discarded the classrooms in the browser,
    which is the same shape of bug as fetching every section and filtering by
    context on the client.
    """
    from test_crud import mk_room

    for i in range(3):
        mk_room(client, number=f"T{i}", room_type="theory", capacity=70)
    mk_room(client, number="L1", room_type="lab", capacity=70, lab_type="programming")
    mk_room(client, number="L2", room_type="lab", capacity=70, lab_type="networking")

    page = client.get("/api/rooms?room_type=lab&limit=50").json()
    assert page["total"] == 2, "total counts the labs, not every room"
    assert {r["room_number"] for r in page["rows"]} == {"L1", "L2"}


def test_the_kind_filter_composes_with_search(client):
    from test_crud import mk_room

    mk_room(client, number="L1", room_type="lab", capacity=70, lab_type="programming")
    mk_room(client, number="L2", room_type="lab", capacity=70, lab_type="networking")
    mk_room(client, number="L3", room_type="theory", capacity=70)

    page = client.get("/api/rooms?room_type=lab&q=networking&limit=50").json()
    assert [r["room_number"] for r in page["rows"]] == ["L2"]


def test_an_unknown_kind_is_refused_rather_than_returning_nothing(client):
    """Silently returning an empty page for a typo reads as "there are no
    labs", which is a different and much more alarming statement."""
    r = client.get("/api/rooms?room_type=labratory&limit=10")
    assert r.status_code == 422
    assert "theory" in r.text and "lab" in r.text


# ------------------------------------------------------- lightweight rows


def test_brief_returns_identity_without_the_relationships(client):
    """A typeahead needs a code and a name.

    Every subject row otherwise carries its faculties and its allowed rooms,
    because the model declares those `lazy="selectin"` and loads them whether
    or not anything reads them. At institution scale a popular subject drags
    dozens of nested objects behind it, once per option in the dropdown.
    """
    from test_crud import mk_faculty, mk_subject

    subject = mk_subject(client, code="CS201")
    fac = mk_faculty(client, code="FAC01")
    client.post(f"/api/faculty/{fac['id']}/subjects", json={"subject_id": subject["id"]})

    full = client.get("/api/subjects?limit=10").json()["rows"][0]
    assert "faculties" in full, "the ordinary row still carries everything it did"

    brief = client.get("/api/subjects?limit=10&brief=true").json()["rows"][0]
    assert brief["code"] == "CS201"
    assert brief["name"]
    assert "faculties" not in brief
    assert "allowed_rooms" not in brief


def test_brief_does_not_read_the_relationship_tables(client, db_session):
    """Trimming the response alone would not have helped - the rows are still
    fetched, just discarded after. The saving is the queries not run."""
    from sqlalchemy import event

    from test_crud import mk_faculty, mk_subject

    for i in range(5):
        subject = mk_subject(client, code=f"CS{200 + i}")
        fac = mk_faculty(client, code=f"FAC{i:02d}")
        client.post(
            f"/api/faculty/{fac['id']}/subjects", json={"subject_id": subject["id"]}
        )

    engine = db_session.get_bind()

    def queries_for(url: str) -> int:
        seen: list[str] = []

        def record(conn, cursor, statement, params, context, executemany):
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            assert client.get(url).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(seen)

    assert queries_for("/api/subjects?limit=10&brief=true") < queries_for(
        "/api/subjects?limit=10"
    )


def test_brief_still_pages_and_searches(client):
    from test_crud import mk_subject

    for i in range(6):
        mk_subject(client, code=f"CS{300 + i}")
    mk_subject(client, code="MA100")

    page = client.get("/api/subjects?brief=true&q=CS30&limit=3").json()
    assert page["total"] == 6, "total describes the match, not the page"
    assert len(page["rows"]) == 3
