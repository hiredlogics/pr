"""FastAPI routes: /admin/api/db/* — admin-only, read-only, audited."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/admin/api/db", tags=["admin-database"])


def _auth(authorization: Optional[str], x_admin_token: Optional[str]) -> str:
    from .auth import require_admin
    require_admin(authorization, x_admin_token)
    # Identity for audit — token presence only, never the secret.
    return "admin"


def _audit(user: str, page: str, case_id: Optional[str] = None, **detail: Any) -> None:
    from .audit import record_access
    record_access(admin_user=user, page=page, case_id=case_id, detail=detail or None)


@router.get("/status")
def db_status(
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    user = _auth(authorization, x_admin_token)
    from . import catalog
    out = catalog.status()
    _audit(user, "db/status", connected=out.get("connected"))
    return out


@router.get("/tables")
def db_tables(
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    counts: bool = Query(True),
):
    user = _auth(authorization, x_admin_token)
    from . import catalog
    try:
        out = catalog.list_tables(include_counts=counts)
    except Exception as exc:
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
    _audit(user, "db/tables", n=out.get("n"))
    return out


@router.get("/tables/{schema}/{table}")
def db_table_meta(
    schema: str,
    table: str,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    user = _auth(authorization, x_admin_token)
    from . import catalog
    try:
        out = catalog.get_table(schema, table)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
    _audit(user, f"db/tables/{schema}/{table}")
    return out


@router.get("/tables/{schema}/{table}/rows")
def db_table_rows(
    schema: str,
    table: str,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort: Optional[str] = None,
    sort_dir: str = Query("desc"),
    search: Optional[str] = None,
    columns: Optional[str] = Query(None, description="comma-separated"),
    show_sensitive: bool = Query(False),
    show_raw_vectors: bool = Query(False),
):
    user = _auth(authorization, x_admin_token)
    from . import browse
    col_list = [c.strip() for c in columns.split(",")] if columns else None
    try:
        out = browse.browse_table(
            schema, table, page=page, page_size=page_size, sort=sort,
            sort_dir=sort_dir, search=search, columns=col_list,
            show_sensitive=show_sensitive, show_raw_vectors=show_raw_vectors,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
    _audit(user, f"db/tables/{schema}/{table}/rows",
           page_no=page, show_sensitive=show_sensitive)
    return out


@router.get("/cases")
def db_cases(
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    show_sensitive: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
):
    user = _auth(authorization, x_admin_token)
    from . import cases_view
    try:
        out = cases_view.case_overview(show_sensitive=show_sensitive, limit=limit)
    except Exception as exc:
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
    _audit(user, "db/cases", show_sensitive=show_sensitive)
    return out


@router.get("/cases/{case_id}")
def db_case_detail(
    case_id: str,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    show_sensitive: bool = Query(False),
):
    user = _auth(authorization, x_admin_token)
    from . import cases_view
    try:
        out = cases_view.case_detail(case_id, show_sensitive=show_sensitive)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
    _audit(user, "db/cases/detail", case_id=case_id, show_sensitive=show_sensitive)
    return out


@router.get("/knowledge")
def db_knowledge(
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    q: Optional[str] = None,
    role: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    user = _auth(authorization, x_admin_token)
    from . import knowledge_view
    try:
        out = knowledge_view.list_knowledge(q=q, role=role, page=page, page_size=page_size)
    except Exception as exc:
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
    _audit(user, "db/knowledge", q=bool(q))
    return out


@router.get("/vector/status")
def vector_status(
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    user = _auth(authorization, x_admin_token)
    from . import vectors
    try:
        out = vectors.status()
    except Exception as exc:
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
    _audit(user, "db/vector/status", installed=out.get("installed"))
    return out


@router.get("/vector/columns")
def vector_cols(
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    user = _auth(authorization, x_admin_token)
    from . import catalog
    try:
        out = catalog.vector_columns()
    except Exception as exc:
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
    _audit(user, "db/vector/columns")
    return out


@router.get("/vector/indexes")
def vector_idxs(
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    user = _auth(authorization, x_admin_token)
    from . import catalog
    try:
        out = catalog.vector_indexes()
    except Exception as exc:
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
    _audit(user, "db/vector/indexes")
    return out


@router.get("/vector/embeddings")
def vector_embeddings(
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    show_raw_vector: bool = Query(False),
    q: Optional[str] = None,
):
    user = _auth(authorization, x_admin_token)
    from . import vectors
    try:
        out = vectors.embeddings(
            page=page, page_size=page_size, show_raw_vector=show_raw_vector, q=q)
    except Exception as exc:
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
    _audit(user, "db/vector/embeddings", show_raw=show_raw_vector)
    return out


@router.get("/vector/health")
def vector_health(
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    user = _auth(authorization, x_admin_token)
    from . import vectors
    try:
        out = vectors.health()
    except Exception as exc:
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
    _audit(user, "db/vector/health")
    return out


class SimilarityIn(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    limit: int = Field(10, ge=1, le=25)


@router.post("/vector/search")
def vector_search(
    body: SimilarityIn,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    user = _auth(authorization, x_admin_token)
    from . import vectors
    try:
        out = vectors.similarity_search(body.query, limit=body.limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
    _audit(user, "db/vector/search", n=len(out.get("results") or []))
    return out


@router.get("/diagnostics")
def diagnostics_list(
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    user = _auth(authorization, x_admin_token)
    from . import sql_safe
    _audit(user, "db/diagnostics")
    return {"queries": sql_safe.list_predefined()}


@router.get("/diagnostics/{query_id}")
def diagnostics_run(
    query_id: str,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    show_sensitive: bool = Query(False),
):
    user = _auth(authorization, x_admin_token)
    from . import sql_safe
    try:
        out = sql_safe.run_predefined(query_id, show_sensitive=show_sensitive)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
    _audit(user, f"db/diagnostics/{query_id}")
    return out


class AdhocSqlIn(BaseModel):
    sql: str = Field(..., min_length=6, max_length=4000)
    row_limit: int = Field(100, ge=1, le=200)


@router.post("/diagnostics/select")
def diagnostics_select(
    body: AdhocSqlIn,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    show_sensitive: bool = Query(False),
):
    user = _auth(authorization, x_admin_token)
    from . import sql_safe
    try:
        out = sql_safe.run_select_only(
            body.sql, show_sensitive=show_sensitive, row_limit=body.row_limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
    _audit(user, "db/diagnostics/select")
    return out
