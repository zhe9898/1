"""
ZEN70 Vector Search Router — semantic search across memory_facts and assets.

Part of the vector-pack runtime surface. Gated behind ``GATEWAY_PACKS``
containing ``vector-pack``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_tenant_db

logger = logging.getLogger("zen70.search")

router = APIRouter(prefix="/api/v1/search", tags=["search"])


def _escape_like_term(term: str) -> str:
    escaped = term.replace("\\", "\\\\")
    escaped = escaped.replace("%", "\\%").replace("_", "\\_")
    return escaped


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class SemanticSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    scope: str = Field(
        "all",
        description="Search scope: 'memory', 'assets', or 'all'",
        pattern="^(memory|assets|all)$",
    )
    limit: int = Field(20, ge=1, le=100)


class SearchHit(BaseModel):
    id: str
    source: str  # "memory" | "asset"
    text: str
    score: float
    meta: dict[str, Any] = {}


class SemanticSearchResponse(BaseModel):
    hits: list[SearchHit]
    query: str
    scope: str
    model_available: bool


# ---------------------------------------------------------------------------
# Embedding helper — reuses existing ai_worker model lazily
# ---------------------------------------------------------------------------


def _encode_query(query_text: str) -> list[float] | None:
    """Encode text to 384-dim vector using the shared sentence-transformer model."""
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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/semantic", response_model=SemanticSearchResponse)
async def semantic_search(
    body: SemanticSearchRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: Any = Depends(get_current_user),
) -> SemanticSearchResponse:
    """Semantic similarity search across memory facts and/or assets."""

    vec = _encode_query(body.query)

    if vec is None:
        # Model not available — fall back to keyword ILIKE search
        return await _keyword_fallback(db, body, user)

    hits: list[SearchHit] = []
    vec_literal = "[" + ",".join(str(v) for v in vec) + "]"

    if body.scope in ("memory", "all"):
        sql = text("""
            SELECT id::text, fact_text, 1 - (text_embedding <=> :vec ::vector(384)) AS score
            FROM memory_facts
            WHERE tenant_id = :tid
              AND user_sub = :uid
              AND deprecated = false
              AND text_embedding IS NOT NULL
            ORDER BY text_embedding <=> :vec ::vector(384)
            LIMIT :lim
            """)
        rows = (
            await db.execute(
                sql,
                {"vec": vec_literal, "tid": user.tenant_id, "uid": user.sub, "lim": body.limit},
            )
        ).all()
        for row in rows:
            hits.append(
                SearchHit(
                    id=row[0],
                    source="memory",
                    text=row[1],
                    score=round(float(row[2]), 4),
                )
            )

    if body.scope in ("assets", "all"):
        # Assets don't have text embeddings; use keyword match on label + ai_tags
        asset_hits = await _asset_keyword_search(db, body.query, user, body.limit)
        hits.extend(asset_hits)

    # Sort all hits by score descending, trim to limit
    hits.sort(key=lambda h: h.score, reverse=True)
    hits = hits[: body.limit]

    return SemanticSearchResponse(
        hits=hits,
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
    """Keyword search across assets (label, ai_tags, original_filename)."""
    hits = await _asset_keyword_search(db, q, user, limit)
    return {"hits": [h.model_dump() for h in hits], "query": q, "count": len(hits)}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _keyword_fallback(
    db: AsyncSession,
    body: SemanticSearchRequest,
    user: Any,
) -> SemanticSearchResponse:
    """Text-only ILIKE fallback when embedding model is unavailable."""
    hits: list[SearchHit] = []
    pattern = f"%{_escape_like_term(body.query)}%"

    if body.scope in ("memory", "all"):
        sql = text("""
            SELECT id::text, fact_text
            FROM memory_facts
            WHERE tenant_id = :tid AND user_sub = :uid
              AND deprecated = false
              AND fact_text ILIKE :pat ESCAPE '\\'
            ORDER BY created_at DESC
            LIMIT :lim
            """)
        rows = (await db.execute(sql, {"tid": user.tenant_id, "uid": user.sub, "pat": pattern, "lim": body.limit})).all()
        for row in rows:
            hits.append(SearchHit(id=row[0], source="memory", text=row[1], score=0.5))

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
    """ILIKE search across asset metadata fields."""
    pattern = f"%{_escape_like_term(query_text)}%"
    sql = text("""
        SELECT id::text, COALESCE(label, original_filename, file_path) AS display,
               asset_type, ai_tags
        FROM assets
        WHERE tenant_id = :tid
          AND is_deleted = false
          AND (label ILIKE :pat ESCAPE '\\' OR original_filename ILIKE :pat ESCAPE '\\'
               OR ai_tags::text ILIKE :pat ESCAPE '\\')
        ORDER BY created_at DESC
        LIMIT :lim
        """)
    rows = (await db.execute(sql, {"tid": user.tenant_id, "pat": pattern, "lim": limit})).all()
    hits: list[SearchHit] = []
    for row in rows:
        hits.append(
            SearchHit(
                id=row[0],
                source="asset",
                text=row[1] or "",
                score=0.4,
                meta={"asset_type": row[2], "ai_tags": row[3]},
            )
        )
    return hits
