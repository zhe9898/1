from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.control_plane.adapters import search


class _FakeResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self._rows


class _FakeSession:
    def __init__(self, responses: dict[str, list[tuple[object, ...]]]) -> None:
        self._responses = responses

    async def execute(self, sql: object, params: dict[str, object]) -> _FakeResult:
        del params
        query = str(sql)
        if "vec384 IS NOT NULL" in query:
            return _FakeResult(self._responses.get("semantic", []))
        if "text ILIKE" in query:
            return _FakeResult(self._responses.get("keyword", []))
        if "FROM assets" in query:
            return _FakeResult(self._responses.get("assets", []))
        return _FakeResult([])


def test_cosine_similarity_handles_empty_and_exact_match() -> None:
    assert search._cosine_similarity([], []) == 0.0
    assert search._cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_memory_semantic_search_ranks_by_vec384_similarity() -> None:
    db = _FakeSession(
        {
            "semantic": [
                ("fact-a", "alpha", [1.0, 0.0]),
                ("fact-b", "beta", [0.0, 1.0]),
                ("fact-c", "gamma", None),
            ]
        }
    )
    user = SimpleNamespace(tenant_id="tenant-a", sub="user-a")

    hits = await search._memory_semantic_search(db, [1.0, 0.0], user, limit=2)

    assert [hit.id for hit in hits] == ["fact-a", "fact-b"]
    assert hits[0].score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_semantic_search_falls_back_to_memory_keyword_hits_when_no_vectors_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search, "_encode_query", lambda query: [1.0, 0.0])
    db = _FakeSession(
        {
            "semantic": [],
            "keyword": [("fact-1", "remember this")],
            "assets": [],
        }
    )
    user = SimpleNamespace(tenant_id="tenant-a", sub="user-a")

    response = await search.semantic_search(
        search.SemanticSearchRequest(query="remember", scope="memory", limit=5),
        db=db,
        user=user,
    )

    assert response.model_available is True
    assert [hit.id for hit in response.hits] == ["fact-1"]
    assert response.hits[0].text == "remember this"
