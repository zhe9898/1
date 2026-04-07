from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.models.memory import MemoryFact
from backend.workers.memory_summarizer import (
    CONFLICT_SIM_THRESHOLD,
    DLQ_STREAM_KEY,
    MAX_RETRIES,
    STREAM_KEY,
    MemorySummarizerWorker,
    _extract_facts,
    _is_filler,
    _should_run_now,
    _summarize_day,
    _system_prompt_daily_summary,
    _system_prompt_extract_facts,
)


class TestIsFillerDetection:
    def test_known_fillers(self) -> None:
        for word in ["ok", "OK", "谢谢", "收到"]:
            assert _is_filler(word) is True

    def test_empty_and_whitespace(self) -> None:
        assert _is_filler("") is True
        assert _is_filler("  ") is True
        assert _is_filler("\n") is True

    def test_single_char_is_filler(self) -> None:
        assert _is_filler("x") is True

    def test_real_content_not_filler(self) -> None:
        assert _is_filler("我叫张三") is False
        assert _is_filler("明天早上八点开会") is False


class TestExtractFactsHeuristic:
    @pytest.mark.anyio
    async def test_filler_returns_empty(self) -> None:
        assert await _extract_facts("ok") == []

    @pytest.mark.anyio
    async def test_keyword_match_returns_fact(self) -> None:
        result = await _extract_facts("我叫张三")
        assert len(result) >= 1
        assert result[0]["text"] == "我叫张三"
        assert 0 <= result[0]["confidence"] <= 1

    @pytest.mark.anyio
    async def test_no_keyword_returns_empty(self) -> None:
        assert await _extract_facts("今天天气真不错啊朋友们") == []


class TestSummarizeDayHeuristic:
    @pytest.mark.anyio
    async def test_empty_texts_returns_empty(self) -> None:
        assert await _summarize_day([]) == ""

    @pytest.mark.anyio
    async def test_real_texts_truncated_to_180(self) -> None:
        texts = ["这是一段真实的对话内容"] * 50
        result = await _summarize_day(texts)
        assert len(result) <= 180


class TestShouldRunNow:
    def test_function_is_callable(self) -> None:
        assert isinstance(_should_run_now(), bool)


class TestSystemPrompts:
    def test_extract_facts_prompt_is_json_only(self) -> None:
        prompt = _system_prompt_extract_facts()
        assert "JSON" in prompt
        assert "facts" in prompt

    def test_daily_summary_prompt_is_json_only(self) -> None:
        prompt = _system_prompt_daily_summary()
        assert "JSON" in prompt
        assert "summary" in prompt


class TestWorkerConstants:
    def test_stream_keys_prefixed(self) -> None:
        assert STREAM_KEY.startswith("zen70:")
        assert DLQ_STREAM_KEY.startswith("zen70:")

    def test_max_retries_reasonable(self) -> None:
        assert 1 <= MAX_RETRIES <= 10


@pytest.mark.asyncio
async def test_select_supersede_candidate_ids_respects_similarity_threshold() -> None:
    worker = MemorySummarizerWorker()
    near_fact = SimpleNamespace(id=uuid.uuid4())
    far_fact = SimpleNamespace(id=uuid.uuid4())
    session = AsyncMock()
    session.execute.return_value = MagicMock(all=MagicMock(return_value=[(near_fact, 0.05), (far_fact, 0.35)]))

    result = await worker._select_supersede_candidate_ids(
        session,
        tenant_id="default",
        user_sub="user-1",
        vec384=[0.1, 0.2, 0.3],
    )

    assert result == [str(near_fact.id)]
    assert (1.0 - 0.05) >= CONFLICT_SIM_THRESHOLD
    assert (1.0 - 0.35) < CONFLICT_SIM_THRESHOLD


@pytest.mark.asyncio
async def test_process_and_save_facts_deprecates_only_selected_candidates() -> None:
    worker = MemorySummarizerWorker()
    worker._select_supersede_candidate_ids = AsyncMock(return_value=["old-fact"])  # type: ignore[method-assign]
    embedder = MagicMock()
    embedder.encode.return_value.tolist.return_value = [0.1, 0.2, 0.3]

    old_row = SimpleNamespace(deprecated=False, superseded_by=None)
    session = AsyncMock()
    session.get.return_value = old_row
    session.add = MagicMock()

    await worker._process_and_save_facts(
        session,
        "default",
        "user-1",
        [{"text": "我叫张三", "confidence": 0.9}],
        embedder,
        {},
    )

    worker._select_supersede_candidate_ids.assert_awaited_once()
    session.get.assert_awaited_once_with(MemoryFact, "old-fact")
    assert old_row.deprecated is True
