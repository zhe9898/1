"""Semantic and keyword search across memory facts and assets."""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.deps import get_current_user, get_tenant_db

logger = logging.getLogger("zen70.search")

router = APIRouter(prefix="/api/v1/search", tags=["search"])


def _escape_like_term(term: str) -> str:
    escaped = term.replace("\\", "\\\\")
    escaped = escaped.replace("%", "\\%").replace("_", "\\_")
    return escaped


def _coerce_vector(raw: Any) -> list[float] | None:
    if isinstance(raw, (list, tuple)):
        try:
            return [float(value) for value in raw]
        except (TypeError, ValueError):
            return None
    return None


def _user_claim(user: Any, key: str) -> str:
    if isinstance(user, dict):
        return str(user.get(key) or "")
    return str(getattr(user, key, "") or "")


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


class SemanticSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    scope: str = Field("all", pattern="^(memory|assets|all)$")
    limit: int = Field(20, ge=1, le=100)


class SearchHit(BaseModel):
    id: str
    source: str
    text: str
    score: float
    meta: dict[str, Any] = Field(default_factory=dict)


class SemanticSearchResponse(BaseModel):
    hits: list[SearchHit]
    query: str
    scope: str
    model_available: bool


def _encode_query(query_text: str) -> list[float] | None:
    try:
        from backend.workers.ai_worker import HAS_MODEL, get_model

        model = get_model()
        if model is None or not HAS_MODEL:
            return None
        vec = model.encode(query_text)
        return vec.tolist()  # type: ignore[no-any-return]
    except Exception:
        logger.warning("Embedding model unavailable for search", exc_info=True)
        return None


async def _memory_semantic_search(
    db: AsyncSession,
    query_vector: Sequence[float],
    user: Any,
    limit: int,
) -> list[SearchHit]:
    candidate_limit = min(max(limit * 20, 100), 500)
    sql = text("""
        SELECT id::text, text, vec384
        FROM memory_facts
        WHERE tenant_id = :tid
          AND user_sub = :uid
          AND deprecated = false
          AND vec384 IS NOT NULL
        ORDER BY created_at DESC
        LIMIT :lim
        """)
    rows = (
        await db.execute(
            sql,
            {"tid": _user_claim(user, "tenant_id"), "uid": _user_claim(user, "sub"), "lim": candidate_limit},
        )
    ).all()

    hits: list[SearchHit] = []
    for row in rows:
        stored_vector = _coerce_vector(row[2])
        if stored_vector is None:
            continue
        score = _cosine_similarity(query_vector, stored_vector)
        hits.append(
            SearchHit(
                id=row[0],
                source="memory",
                text=row[1] or "",
                score=round(score, 4),
            )
        )

    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits[:limit]


async def _memory_keyword_search(
    db: AsyncSession,
    query_text: str,
    user: Any,
    limit: int,
) -> list[SearchHit]:
    pattern = f"%{_escape_like_term(query_text)}%"
    sql = text("""
        SELECT id::text, text
        FROM memory_facts
        WHERE tenant_id = :tid
          AND user_sub = :uid
          AND deprecated = false
          AND text ILIKE :pat ESCAPE '\\'
        ORDER BY created_at DESC
        LIMIT :lim
        """)
    rows = (
        await db.execute(
            sql,
            {"tid": _user_claim(user, "tenant_id"), "uid": _user_claim(user, "sub"), "pat": pattern, "lim": limit},
        )
    ).all()
    return [SearchHit(id=row[0], source="memory", text=row[1] or "", score=0.5) for row in rows]


@router.post("/semantic", response_model=SemanticSearchResponse)
async def semantic_search(
    body: SemanticSearchRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: Any = Depends(get_current_user),
) -> SemanticSearchResponse:
    query_vector = _encode_query(body.query)
    if query_vector is None:
        return await _keyword_fallback(db, body, user)

    hits: list[SearchHit] = []
    if body.scope in ("memory", "all"):
        memory_hits = await _memory_semantic_search(db, query_vector, user, body.limit)
        if memory_hits:
            hits.extend(memory_hits)
        else:
            hits.extend(await _memory_keyword_search(db, body.query, user, body.limit))

    if body.scope in ("assets", "all"):
        hits.extend(await _asset_keyword_search(db, body.query, user, body.limit))

    hits.sort(key=lambda hit: hit.score, reverse=True)
    return SemanticSearchResponse(
        hits=hits[: body.limit],
        query=body.query,
        scope=body.scope,
        model_available=True,
    )


@router.get("/assets")
async def search_assets(
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_tenant_db),
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    hits = await _asset_keyword_search(db, q, user, limit)
    return {"hits": [hit.model_dump() for hit in hits], "query": q, "count": len(hits)}


async def _keyword_fallback(
    db: AsyncSession,
    body: SemanticSearchRequest,
    user: Any,
) -> SemanticSearchResponse:
    hits: list[SearchHit] = []

    if body.scope in ("memory", "all"):
        hits.extend(await _memory_keyword_search(db, body.query, user, body.limit))

    if body.scope in ("assets", "all"):
        hits.extend(await _asset_keyword_search(db, body.query, user, body.limit))

    return SemanticSearchResponse(
        hits=hits[: body.limit],
        query=body.query,
        scope=body.scope,
        model_available=False,
    )


async def _asset_keyword_search(
    db: AsyncSession,
    query_text: str,
    user: Any,
    limit: int,
) -> list[SearchHit]:
    pattern = f"%{_escape_like_term(query_text)}%"
    sql = text("""
        SELECT id::text, COALESCE(label, original_filename, file_path) AS display,
               asset_type, ai_tags
        FROM assets
        WHERE tenant_id = :tid
          AND is_deleted = false
          AND (
            label ILIKE :pat ESCAPE '\\'
            OR original_filename ILIKE :pat ESCAPE '\\'
            OR ai_tags::text ILIKE :pat ESCAPE '\\'
          )
        ORDER BY created_at DESC
        LIMIT :lim
        """)
    rows = (await db.execute(sql, {"tid": _user_claim(user, "tenant_id"), "pat": pattern, "lim": limit})).all()
    return [
        SearchHit(
            id=row[0],
            source="asset",
            text=row[1] or "",
            score=0.4,
            meta={"asset_type": row[2], "ai_tags": row[3]},
        )
        for row in rows
    ]
