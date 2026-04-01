"""Tests for dispatch_lifecycle — pipeline abstraction and placement hints."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest

from backend.core.dispatch_lifecycle import (
    DispatchContext,
    DispatchPipeline,
    DispatchResult,
    apply_placement_hints,
    get_dispatch_pipeline,
)


def _utcnow() -> datetime.datetime:
    return datetime.datetime(2025, 1, 15, 12, 0, 0)


class TestDispatchContext:
    def test_defaults(self) -> None:
        ctx = DispatchContext(
            tenant_id="t1",
            node_id="n1",
            now=_utcnow(),
        )
        assert ctx.tenant_id == "t1"
        assert ctx.burst_active is False
        assert ctx.filtered_count == 0
        assert len(ctx.placement_hints) == 0

    def test_record_stage_time(self) -> None:
        ctx = DispatchContext(tenant_id="t1", node_id="n1", now=_utcnow())
        ctx.record_stage_time("test_stage")
        assert "test_stage" in ctx._stage_timings

    def test_total_ms(self) -> None:
        ctx = DispatchContext(tenant_id="t1", node_id="n1", now=_utcnow())
        ctx._stage_timings = {"a": 10.0, "b": 20.0}
        assert ctx.total_ms == 30.0


class TestDispatchResult:
    def test_success_when_admitted(self) -> None:
        r = DispatchResult(admitted=True)
        assert r.success is True

    def test_failure_when_not_admitted(self) -> None:
        r = DispatchResult(admitted=False, admission_reason="quarantined")
        assert r.success is False


class TestDispatchPipeline:
    @pytest.mark.asyncio
    async def test_all_pass_pipeline(self) -> None:
        class OkStage:
            name = "ok"

            async def execute(self, ctx: DispatchContext) -> bool:
                return True

        pipeline = DispatchPipeline.__new__(DispatchPipeline)
        pipeline.stages = [OkStage()]
        ctx = DispatchContext(tenant_id="t1", node_id="n1", now=_utcnow())
        result = await pipeline.execute(ctx)
        assert result.admitted is True

    @pytest.mark.asyncio
    async def test_stage_short_circuit(self) -> None:
        class DenyStage:
            name = "deny"

            async def execute(self, ctx: DispatchContext) -> bool:
                return False

        class NeverReachedStage:
            name = "never"

            async def execute(self, ctx: DispatchContext) -> bool:
                ctx.filtered_count = 999
                return True

        pipeline = DispatchPipeline(stages=[DenyStage(), NeverReachedStage()])
        ctx = DispatchContext(tenant_id="t1", node_id="n1", now=_utcnow())
        result = await pipeline.execute(ctx)
        assert result.admitted is False
        assert "deny" in result.admission_reason
        assert ctx.filtered_count == 0  # never reached

    @pytest.mark.asyncio
    async def test_stage_exception_handled(self) -> None:
        class BrokenStage:
            name = "broken"

            async def execute(self, ctx: DispatchContext) -> bool:
                raise RuntimeError("boom")

        pipeline = DispatchPipeline(stages=[BrokenStage()])
        ctx = DispatchContext(tenant_id="t1", node_id="n1", now=_utcnow())
        result = await pipeline.execute(ctx)
        assert result.admitted is False
        assert "broken" in result.admission_reason


class TestApplyPlacementHints:
    def test_no_hints_passthrough(self) -> None:
        scored = [MagicMock(job=MagicMock(job_id="j1"), score=100)]
        result = apply_placement_hints(scored, {}, "n1")
        assert len(result) == 1

    def test_matching_hint_boosts_score(self) -> None:
        from dataclasses import dataclass
        from dataclasses import field as dc_field

        @dataclass
        class SJ:
            job: object
            score: int
            score_breakdown: dict = dc_field(default_factory=dict)

        j = MagicMock()
        j.job_id = "j1"
        bd: dict = {}
        sj = SJ(job=j, score=100, score_breakdown=bd)
        result = apply_placement_hints([sj], {"j1": "n1"}, "n1", bonus=50)
        assert result[0].score == 150
        assert bd.get("solver_hint") == 50

    def test_non_matching_hint_no_change(self) -> None:
        from dataclasses import dataclass

        @dataclass
        class SJ:
            job: object
            score: int
            score_breakdown: dict | None = None

        j = MagicMock()
        j.job_id = "j1"
        sj = SJ(job=j, score=100, score_breakdown={})
        result = apply_placement_hints([sj], {"j1": "n2"}, "n1", bonus=50)
        assert result[0].score == 100


class TestDispatchPipelineSingleton:
    def test_singleton(self) -> None:
        p1 = get_dispatch_pipeline()
        p2 = get_dispatch_pipeline()
        assert p1 is p2

    def test_has_default_stages(self) -> None:
        p = get_dispatch_pipeline()
        names = [s.name for s in p.stages]
        assert "admission" in names
        assert "filtering" in names
        assert "business" in names
        assert "placement" in names
        assert "post_dispatch" in names
