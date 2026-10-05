"""Search, sort and pagination for list endpoints.

Every entity list returned its whole table. That is fine for a demo and wrong
for an institution: a few thousand faculty, each carrying eagerly-loaded
subject and availability collections, is a multi-megabyte response for a page
that shows twenty rows.

The contract is deliberately **opt-in**. A request with none of `limit`,
`offset`, `q` or `sort` gets exactly what it got before - a plain JSON array,
byte for byte. Add any one of them and the response becomes an envelope with
the total. Existing callers, including every current page, are
therefore untouched, and clients migrate one at a time instead of in a single
flag day.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence, TypeVar

from fastapi import HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, lazyload

from .database import Base

ModelT = TypeVar("ModelT", bound=Base)

# A page nobody asked to be smaller. Large enough that the common case is one
# request, small enough that a mistake is not an outage.
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000


@dataclass(frozen=True)
class ListParams:
    """The pagination and search a request asked for, if any."""

    limit: int | None = None
    offset: int | None = None
    q: str | None = None
    sort: str | None = None
    brief: bool = False

    @property
    def requested(self) -> bool:
        """True when the caller used any part of the paged contract.

        This is what decides between the legacy array and the envelope, so it
        must stay a question about the *request*, never about the data.
        """
        return self.brief or any(
            v is not None for v in (self.limit, self.offset, self.q, self.sort)
        )

    @property
    def effective_limit(self) -> int:
        return DEFAULT_LIMIT if self.limit is None else self.limit

    @property
    def effective_offset(self) -> int:
        return 0 if self.offset is None else self.offset


def list_params(
    limit: int | None = Query(
        None, ge=1, le=MAX_LIMIT,
        description="Page size. Sending it switches the response to {total, rows}.",
    ),
    offset: int | None = Query(None, ge=0, description="Rows to skip."),
    q: str | None = Query(
        None, min_length=1, max_length=200,
        description="Case-insensitive substring match over this entity's name and code.",
    ),
    sort: str | None = Query(
        None, max_length=60,
        description="Sort column, optionally prefixed with '-' for descending.",
    ),
    brief: bool = Query(
        False,
        description=(
            "Return only identifying fields, and load none of the row's "
            "relationships. For pickers and typeaheads."
        ),
    ),
) -> ListParams:
    """FastAPI dependency; add it to a list route to make it paginable."""
    return ListParams(limit=limit, offset=offset, q=q, sort=sort, brief=brief)


@dataclass(frozen=True)
class Sortable:
    """What a given entity may be searched and sorted by.

    Sort columns are whitelisted rather than resolved by name off the model:
    an unchecked `sort` parameter is an invitation to order by a column that
    happens to exist but should not be exposed, and produces a 500 rather than
    a useful error when it does not exist at all.
    """

    search: Sequence[Any] = ()
    sorts: dict[str, Any] | None = None
    default_sort: Any = None

    def order_by(self, sort: str | None):
        if not sort:
            return self.default_sort
        descending = sort.startswith("-")
        name = sort[1:] if descending else sort
        column = (self.sorts or {}).get(name)
        if column is None:
            allowed = ", ".join(sorted(self.sorts or {})) or "(none)"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot sort by {name!r}. Available: {allowed}",
            )
        return column.desc() if descending else column.asc()


@dataclass(frozen=True)
class Page:
    """One page of rows plus the count of everything that matched."""

    total: int
    rows: list[Any]
    # Whether the caller asked for identity-only rows. Carried here rather
    # than read again in the router so the decision to load relationships and
    # the decision to serialise them cannot disagree.
    brief: bool = False


def apply(
    db: Session,
    model: type[ModelT],
    params: ListParams,
    spec: Sortable,
    *,
    base_query=None,
) -> list[ModelT] | Page:
    """Return a plain list for a legacy request, or a `Page` for a paged one.

    The two shapes are deliberate: routers hand the result straight back, and
    FastAPI serialises a list as a list and a `Page` as an envelope.
    """
    query = db.query(model) if base_query is None else base_query

    if params.brief:
        # Trimming the response schema alone would not help: the models declare
        # `lazy="selectin"`, so every relationship is fetched whether or not
        # anything reads it, and one subject drags its faculties and its
        # allowed rooms along behind it. A typeahead wants a code and a name.
        query = query.options(lazyload("*"))

    if params.q and spec.search:
        needle = f"%{params.q.strip()}%"
        query = query.filter(or_(*[col.ilike(needle) for col in spec.search]))

    order = spec.order_by(params.sort)
    if not params.requested:
        # Legacy contract: the whole table, in its established order.
        return query.order_by(order).all() if order is not None else query.all()

    # `.count()` over the filtered query, not a bare count of the table: a
    # caller may have narrowed the base query (sections by academic context,
    # say) and the total has to describe what they actually asked about.
    total = query.order_by(None).count()

    rows = query
    if order is not None:
        rows = rows.order_by(order)
    rows = rows.limit(params.effective_limit).offset(params.effective_offset).all()
    return Page(total=total, rows=rows, brief=params.brief)


def serialize(
    result: list[Any] | Page, schema: type, brief_schema: type | None = None
) -> Any:
    """Render either shape with the same response schema.

    Routes that use this declare no `response_model`, because the response is
    genuinely one of two shapes and FastAPI would have to guess between them.
    Converting here keeps that decision explicit and keeps validation - every
    row still goes through the schema.
    """
    if isinstance(result, Page):
        chosen = brief_schema if (result.brief and brief_schema) else schema
        return {
            "total": result.total,
            "rows": [chosen.model_validate(r) for r in result.rows],
        }
    return [schema.model_validate(r) for r in result]
