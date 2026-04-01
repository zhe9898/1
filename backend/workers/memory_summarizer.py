"""
ZEN70 Memory Summarizer Worker - 对话记忆提取与日摘要。
"""

from __future__ import annotations

import datetime
import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.memory import MemoryFact

logger = logging.getLogger("zen70.memory_summarizer")

STREAM_KEY = "zen70:memory:stream"
DLQ_STREAM_KEY = "zen70:memory:dlq"
MAX_RETRIES = 3
CONFLICT_SIM_THRESHOLD = 0.9

_FILLER_WORDS = {"ok", "OK", "好", "嗯", "谢谢", "收到", "哦", "呢", "啊", "哈"}
_KEYWORD_PATTERNS = [
    re.compile(r"我叫|我是|我的名字"),
    re.compile(r"我住在|我在.+工作"),
    re.compile(r"我喜欢|我不喜欢|我讨厌"),
    re.compile(r"我的.+是|我有"),
    re.compile(r"生日|出生|年龄"),
]


def _is_filler(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) <= 1:
        return True
    return stripped in _FILLER_WORDS


async def _extract_facts(text: str) -> list[dict[str, Any]]:
    if _is_filler(text):
        return []
    for pattern in _KEYWORD_PATTERNS:
        if pattern.search(text):
            return [{"text": text, "confidence": 0.85}]
    return []


async def _summarize_day(texts: list[str]) -> str:
    if not texts:
        return ""
    joined = " | ".join(texts)
    return joined[:180]


def _should_run_now() -> bool:
    now = datetime.datetime.now()
    return now.hour >= 2 and now.hour < 5


def _system_prompt_extract_facts() -> str:
    return 'Extract personal facts from conversation. Return JSON array of {"facts": [...]}.'


def _system_prompt_daily_summary() -> str:
    return 'Summarize daily conversation. Return JSON {"summary": "..."}.'


class MemorySummarizerWorker:
    async def _select_supersede_candidate_ids(
        self,
        session: AsyncSession,
        tenant_id: str,
        user_sub: str,
        vec384: list[float],
    ) -> list[str]:
        result = await session.execute(
            # Placeholder query - real impl uses pgvector cosine distance
            ...  # type: ignore[call-overload]
        )
        rows = result.all()
        candidates = []
        for row_fact, distance in rows:
            similarity = 1.0 - distance
            if similarity >= CONFLICT_SIM_THRESHOLD:
                candidates.append(str(row_fact.id))
        return candidates

    async def _process_and_save_facts(
        self,
        session: AsyncSession,
        tenant_id: str,
        user_sub: str,
        facts: list[dict[str, Any]],
        embedder: Any,
        context: dict[str, Any],
    ) -> None:
        for fact_data in facts:
            vec = embedder.encode(fact_data["text"]).tolist()
            supersede_ids = await self._select_supersede_candidate_ids(session, tenant_id, user_sub, vec)
            for old_id in supersede_ids:
                old_fact = await session.get(MemoryFact, old_id)
                if old_fact:
                    old_fact.deprecated = True  # type: ignore[assignment]

            new_fact = MemoryFact(
                tenant_id=tenant_id,
                user_sub=user_sub,
                text=fact_data["text"],
                confidence=fact_data.get("confidence", 0.0),
                vec384=vec,
            )
            session.add(new_fact)
