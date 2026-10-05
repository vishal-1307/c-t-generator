"""Generic CRUD helpers.

The five core entities differ only in their model class and unique field, so a
single generic implementation keeps the routers thin. Uniqueness is checked
explicitly rather than relying on an IntegrityError, so the API can return a
clear 409 naming the offending field.
"""
from __future__ import annotations

from typing import Any, TypeVar

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Base

ModelT = TypeVar("ModelT", bound=Base)


def get_or_404(db: Session, model: type[ModelT], obj_id: int) -> ModelT:
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{model.__name__} {obj_id} not found",
        )
    return obj


def list_all(db: Session, model: type[ModelT], order_by: Any = None) -> list[ModelT]:
    stmt = select(model)
    if order_by is not None:
        stmt = stmt.order_by(order_by)
    return list(db.scalars(stmt).all())


def _assert_unique(
    db: Session,
    model: type[ModelT],
    field: str | tuple[str, ...],
    data: Any,
    exclude_id: int | None = None,
) -> None:
    """Refuse a duplicate before the database has to.

    `field` may name several columns, in which case the *combination* must be
    unique - which is what a room number is: buildings each have a 101, and the
    schema has said so since room identity became block-scoped. Checking one
    column of a composite key rejects rows the database would happily accept,
    and does it with a message that sounds like a data error rather than a bug.
    """
    fields = (field,) if isinstance(field, str) else tuple(field)
    if isinstance(data, dict):
        values = {f: data.get(f) for f in fields}
    else:
        # Single-column callers still pass the value itself.
        values = {fields[0]: data}

    if any(v is None for v in values.values()):
        return

    stmt = select(model)
    for f, v in values.items():
        stmt = stmt.where(getattr(model, f) == v)
    existing = db.scalars(stmt).first()
    if existing is not None and getattr(existing, "id", None) != exclude_id:
        described = ", ".join(f"{f}={v!r}" for f, v in values.items())
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{model.__name__} with {described} already exists",
        )


def create(
    db: Session,
    model: type[ModelT],
    data: dict[str, Any],
    unique_field: str | tuple[str, ...] | None = None,
) -> ModelT:
    if unique_field:
        _assert_unique(db, model, unique_field, data)
    obj = model(**data)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def update(
    db: Session,
    model: type[ModelT],
    obj_id: int,
    data: dict[str, Any],
    unique_field: str | tuple[str, ...] | None = None,
) -> ModelT:
    obj = get_or_404(db, model, obj_id)
    if unique_field:
        fields = (unique_field,) if isinstance(unique_field, str) else unique_field
        if any(f in data for f in fields):
            # An update may name only part of a composite key, so the columns
            # it leaves alone are read from the row being edited - otherwise
            # changing a room's number would compare against a blank block.
            merged = {f: data.get(f, getattr(obj, f)) for f in fields}
            _assert_unique(db, model, unique_field, merged, exclude_id=obj_id)
    for key, value in data.items():
        setattr(obj, key, value)
    db.commit()
    db.refresh(obj)
    return obj


def delete(db: Session, model: type[ModelT], obj_id: int) -> None:
    obj = get_or_404(db, model, obj_id)
    db.delete(obj)
    db.commit()
