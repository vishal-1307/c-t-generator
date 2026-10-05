"""The templates a teacher downloads have to import cleanly.

They are the first file anyone uploads. A template that has drifted from what
the importer accepts would fail on a teacher's very first try, in front of
them, with an error about a file they did not write - so it fails here instead.
"""
from __future__ import annotations

from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[2] / "frontend" / "public" / "templates"
LOAD = TEMPLATES / "LOAD_TEMPLATE.xlsx"
INFRA = TEMPLATES / "INFRA_TEMPLATE.xlsx"


def _upload(client, path):
    return client.post(
        path,
        files={
            "load": ("LOAD_TEMPLATE.xlsx", LOAD.read_bytes(), "application/vnd.ms-excel"),
            "infra": ("INFRA_TEMPLATE.xlsx", INFRA.read_bytes(), "application/vnd.ms-excel"),
        },
    )


def test_both_templates_exist():
    assert LOAD.is_file() and INFRA.is_file(), "run: python datasets/build_templates.py"


def test_the_templates_import_and_are_ready_to_generate(client):
    resp = _upload(client, "/api/teacher/preview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_errors"] is False, [p for o in body["outcomes"] for p in o["problems"]]
    assert body["readiness"]["ready"] is True, body["readiness"]["blockers"]


def test_the_load_template_shows_different_weekly_counts(client):
    body = _upload(client, "/api/teacher/preview").json()
    summary = body["summary"]
    # 4 + 5 + 3 + 4 theory classes, two groups x 2 two-period labs, two groups
    # x 1 three-period lab.
    assert summary["classes"] == 4 + 5 + 3 + 4 + 2 * 2 + 2 * 1
    assert summary["required_periods"] == 4 + 5 + 3 + 4 + 2 * 2 * 2 + 2 * 1 * 3


def test_the_load_template_explains_every_column():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.load_workbook(LOAD)
    headers = [c.value for c in wb["Load"][1]]
    explained = [r[0].value for r in wb["How to fill this in"].iter_rows(min_row=5)]
    assert headers == explained
    assert "Classes Per Week" in headers
