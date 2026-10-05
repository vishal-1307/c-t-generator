"""The general import API (CSV/XLSX, one entity or a whole workbook).

No screen uses these endpoints any more. The two-file workflow
(``routers/teacher_import.py`` and ``routers/infrastructure.py``) is how data
arrives, and the old "Workbook import" page that called these routes was a
second, confusing way of doing the same thing, so it was removed.

The routes stay, admin-only, for two reasons:

* the engine behind them (``app/bulk_import``) *is* the two-file workflow's
  engine - Load.xlsx and Infra.xlsx are exploded into these sheets and written
  by exactly this code - and the integration tests drive it through here;
* a scripted repair of one imported table (for example a corrected room list
  as a single sheet) needs no screen, and is recorded in the import history.

``_read`` and ``_workbook_out`` are shared with those two routers.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import bulk_import as bi
from .. import schemas
from ..auth import require_admin
from ..config import settings
from ..database import get_db
from ..models import ImportHistory, User
from ..schemas import BulkEntitySpecOut, BulkImportPreviewOut, ImportHistoryOut

router = APIRouter(prefix="/api/bulk-import", tags=["bulk-import"])

# One setting shared by every upload path, so they cannot drift apart.
MAX_BYTES = settings.max_upload_bytes


def _adapter(entity: str) -> bi.EntityAdapter:
    adapter = bi.ADAPTERS.get(entity)
    if adapter is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown bulk-import type '{entity}'. Valid types: {', '.join(bi.ADAPTERS)}",
        )
    return adapter


def _to_out(preview: bi.ImportPreview) -> BulkImportPreviewOut:
    return BulkImportPreviewOut(
        entity=preview.entity,
        mode=preview.mode,
        filename=preview.filename,
        columns=preview.columns,
        rows=[
            {
                "row_number": r.row_number,
                "identity": r.identity,
                "verdict": r.verdict,
                "message": r.message,
                "changes": [{"field": c.field, "old": c.old, "new": c.new} for c in r.changes],
            }
            for r in preview.rows
        ],
        deactivations=[{"id": d.id, "identity": d.identity} for d in preview.deactivations],
        counts=preview.counts,
        has_errors=preview.has_errors,
        can_apply=preview.can_apply,
        committed=preview.committed,
    )


# No .xlsm: nothing here needs a macro-enabled workbook.
ALLOWED_EXTENSIONS = (".csv", ".xlsx")


async def _read(file: UploadFile) -> bytes:
    # PART 20: extension allowlist. Belt-and-suspenders, not the only
    # defense - nothing here is ever written to disk or executed (parsing
    # goes straight from bytes in memory into csv.DictReader or
    # openpyxl.load_workbook(data_only=True), which reads cached cell
    # values and never evaluates a formula or a macro), so an extension
    # mismatch alone can't lead to code execution. It still catches an
    # obviously-wrong upload before spending any parse effort on it.
    name = (file.filename or "").lower()
    if not name.endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(
            status_code=422,
            detail=f"Only {', '.join(ALLOWED_EXTENSIONS)} files are accepted, got {file.filename!r}",
        )
    too_big = HTTPException(
        status_code=413,
        detail=f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.",
    )
    # Refuse before reading when the size is declared, and never read more
    # than one byte past the limit when it is not.
    if file.size is not None and file.size > MAX_BYTES:
        raise too_big
    raw = await file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise too_big
    if not raw:
        raise HTTPException(status_code=422, detail="The uploaded file is empty.")
    return raw


@router.get("/entities", response_model=list[BulkEntitySpecOut])
def list_entities():
    return [
        BulkEntitySpecOut(
            key=a.key, label=a.label, columns=a.columns,
            required=a.required, description=a.description, sample=a.sample,
        )
        for a in bi.ADAPTERS.values()
    ]


# Declared before the `{entity}` routes below, because "/workbook/apply" also
# matches "/{entity}/apply" - and whichever is declared first wins. Left in the
# other order, every workbook import was answered by the single-entity route
# looking for an adapter named "workbook".
# --------------------------------------------------------------- whole workbook


def _workbook_out(result) -> schemas.WorkbookPreviewOut:
    return schemas.WorkbookPreviewOut(
        filename=result.filename,
        sheets=[
            schemas.WorkbookSheetOut(
                sheet=m.sheet, status=m.status, entity=m.entity, label=m.label,
                row_count=m.row_count, reason=m.reason, confidence=m.confidence,
                unrecognised_columns=m.unrecognised_columns,
            )
            for m in result.sheets
        ],
        outcomes=[
            schemas.WorkbookSheetOutcomeOut(
                sheet=o.sheet, entity=o.entity, label=o.label,
                creates=o.creates, updates=o.updates, unchanged=o.unchanged,
                invalid=o.invalid, duplicates=o.duplicates,
                problems=[
                    schemas.WorkbookProblemOut(
                        sheet=p.sheet, row_number=p.row_number, identity=p.identity,
                        message=p.message, column=p.column, suggestion=p.suggestion,
                    )
                    for p in o.problems
                ],
                notes=o.notes,
            )
            for o in result.outcomes
        ],
        decisions=[
            schemas.WorkbookDecisionOut(
                kind=d.kind, label=d.label, detail=d.detail, destructive=d.destructive
            )
            for d in result.decisions
        ],
        blockers=result.blockers,
        counts=result.counts,
        has_errors=result.has_errors,
        can_apply=result.can_apply,
        committed=result.committed,
    )


@router.post("/workbook/analyse", response_model=schemas.WorkbookPreviewOut)
async def analyse_workbook_endpoint(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Read a whole workbook and report what importing it would do.

    Writes nothing. Every sheet is accounted for - detected, ignored,
    unrecognised or empty - and every problem carries the sheet, row and column
    it belongs to.
    """
    data = await _read(file)
    try:
        result = bi.analyse_workbook(db, data, file.filename or "workbook.xlsx")
    except bi.ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _workbook_out(result)


@router.post("/workbook/apply", response_model=schemas.WorkbookPreviewOut)
async def apply_workbook_endpoint(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Import a whole workbook, or nothing at all.

    The file is re-analysed rather than trusting a preview the caller may have
    obtained against a database that has since changed. If anything is invalid,
    nothing is written.
    """
    data = await _read(file)
    try:
        result = bi.apply_workbook(
            db, data, file.filename or "workbook.xlsx", actor=admin.username
        )
    except bi.ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _workbook_out(result)


@router.get("/{entity}/template.csv")
def download_template_csv(entity: str):
    _adapter(entity)
    return Response(
        bi.template_csv(entity),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{entity}_template.csv"'},
    )


@router.get("/{entity}/template.xlsx")
def download_template_xlsx(entity: str):
    _adapter(entity)
    return Response(
        bi.template_xlsx(entity),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{entity}_template.xlsx"'},
    )


@router.post("/{entity}/preview", response_model=BulkImportPreviewOut)
async def preview(
    entity: str,
    file: UploadFile = File(...),
    mode: str = Form(bi.MODE_ADD_UPDATE),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    _adapter(entity)
    data = await _read(file)
    try:
        result = bi.analyse(db, entity, data, file.filename or "upload.csv", mode)
    except bi.ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_out(result)


@router.post("/{entity}/apply", response_model=BulkImportPreviewOut)
async def apply(
    entity: str,
    file: UploadFile = File(...),
    mode: str = Form(bi.MODE_ADD_UPDATE),
    confirm_deactivations: bool = Form(False),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _adapter(entity)
    data = await _read(file)
    try:
        result = bi.apply(
            db, entity, data, file.filename or "upload.csv", mode,
            confirm_deactivations=confirm_deactivations, actor=admin.username,
        )
    except bi.ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_out(result)


@router.get("/history", response_model=list[ImportHistoryOut])
def history(
    entity: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Who imported which file - names people, so administrators only."""
    stmt = select(ImportHistory).order_by(ImportHistory.created_at.desc()).limit(min(limit, 200))
    if entity:
        stmt = stmt.where(ImportHistory.entity == entity)
    return db.execute(stmt).scalars().all()
