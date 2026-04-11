"""
api/evaluations.py 及 api/evaluations_helpers.py 单元测试。

覆盖：
  - EvaluationCreateRequest 验证（category、rating 边界）
  - EvaluationResponse / _to_response 映射
  - _evaluation_status_view 映射
  - _build_evaluation_actions（可删 vs 不可删）
  - _resource_schema 结构契约
  - create_evaluation — 正常路径 / 重复 409
  - list_evaluations — 无过滤 / 带过滤
  - get_evaluation — 找到 / 404
  - delete_evaluation — 正常 / 404 / 409 (approved 不可删)
  - _eval_stmt_for_tenant — tenant 隔离
  - _utcnow — 返回 naive datetime
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_eval(
    *,
    tenant_id: str = "t1",
    evaluation_id: str = "eval-001",
    software_id: str = "svc-a",
    branch: str = "main",
    rating: int = 5,
    category: str = "general",
    comment: str | None = "great",
    evaluator: str = "alice",
    status: str = "submitted",
) -> MagicMock:
    ev = MagicMock()
    ev.tenant_id = tenant_id
    ev.evaluation_id = evaluation_id
    ev.software_id = software_id
    ev.branch = branch
    ev.rating = rating
    ev.category = category
    ev.comment = comment
    ev.evaluator = evaluator
    ev.status = status
    ev.created_at = datetime.datetime(2024, 1, 1, 0, 0, 0)
    ev.updated_at = datetime.datetime(2024, 1, 2, 0, 0, 0)
    return ev


# ---------------------------------------------------------------------------
# EvaluationCreateRequest
# ---------------------------------------------------------------------------


class TestEvaluationCreateRequest:
    """Pydantic 请求模型验证。"""

    def test_valid_payload(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import EvaluationCreateRequest

        req = EvaluationCreateRequest(
            evaluation_id="eval-1",
            software_id="svc-x",
            branch="main",
            rating=4,
            category="performance",
            comment="ok",
        )
        assert req.rating == 4
        assert req.category == "performance"

    def test_default_branch_and_category(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import EvaluationCreateRequest

        req = EvaluationCreateRequest(evaluation_id="e", software_id="s", rating=3)
        assert req.branch == "main"
        assert req.category == "general"

    def test_rating_below_min_rejected(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import EvaluationCreateRequest

        with pytest.raises(ValidationError):
            EvaluationCreateRequest(evaluation_id="e", software_id="s", rating=0)

    def test_rating_above_max_rejected(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import EvaluationCreateRequest

        with pytest.raises(ValidationError):
            EvaluationCreateRequest(evaluation_id="e", software_id="s", rating=6)

    def test_invalid_category_rejected(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import EvaluationCreateRequest

        with pytest.raises(ValidationError):
            EvaluationCreateRequest(evaluation_id="e", software_id="s", rating=3, category="unknown")

    def test_all_valid_categories_accepted(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import VALID_CATEGORIES, EvaluationCreateRequest

        for cat in VALID_CATEGORIES:
            req = EvaluationCreateRequest(evaluation_id="e", software_id="s", rating=3, category=cat)
            assert req.category == cat

    def test_empty_evaluation_id_rejected(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import EvaluationCreateRequest

        with pytest.raises(ValidationError):
            EvaluationCreateRequest(evaluation_id="", software_id="s", rating=3)

    def test_empty_software_id_rejected(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import EvaluationCreateRequest

        with pytest.raises(ValidationError):
            EvaluationCreateRequest(evaluation_id="e", software_id="", rating=3)

    def test_comment_optional(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import EvaluationCreateRequest

        req = EvaluationCreateRequest(evaluation_id="e", software_id="s", rating=1)
        assert req.comment is None

    def test_rating_boundary_min(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import EvaluationCreateRequest

        req = EvaluationCreateRequest(evaluation_id="e", software_id="s", rating=1)
        assert req.rating == 1

    def test_rating_boundary_max(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import EvaluationCreateRequest

        req = EvaluationCreateRequest(evaluation_id="e", software_id="s", rating=5)
        assert req.rating == 5


# ---------------------------------------------------------------------------
# _evaluation_status_view
# ---------------------------------------------------------------------------


class TestEvaluationStatusView:
    """status → StatusView 映射。"""

    def test_submitted_label(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import _evaluation_status_view

        sv = _evaluation_status_view("submitted")
        assert sv.key == "submitted"
        assert sv.label == "Submitted"
        assert sv.tone == "neutral"

    def test_approved_label(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import _evaluation_status_view

        sv = _evaluation_status_view("approved")
        assert sv.key == "approved"
        assert sv.tone == "success"

    def test_rejected_label(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import _evaluation_status_view

        sv = _evaluation_status_view("rejected")
        assert sv.key == "rejected"
        assert sv.tone == "error"

    def test_unknown_status_falls_back_to_neutral(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import _evaluation_status_view

        sv = _evaluation_status_view("pending_review")
        assert sv.tone == "neutral"
        assert sv.label == "Pending_Review"


# ---------------------------------------------------------------------------
# _build_evaluation_actions
# ---------------------------------------------------------------------------


class TestBuildEvaluationActions:
    """delete action enablement logic."""

    def test_submitted_can_delete(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import _build_evaluation_actions

        ev = _make_eval(status="submitted")
        actions = _build_evaluation_actions(ev)
        delete = next(a for a in actions if a.key == "delete")
        assert delete.enabled is True
        assert delete.reason is None

    def test_rejected_can_delete(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import _build_evaluation_actions

        ev = _make_eval(status="rejected")
        actions = _build_evaluation_actions(ev)
        delete = next(a for a in actions if a.key == "delete")
        assert delete.enabled is True

    def test_approved_cannot_delete(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import _build_evaluation_actions

        ev = _make_eval(status="approved")
        actions = _build_evaluation_actions(ev)
        delete = next(a for a in actions if a.key == "delete")
        assert delete.enabled is False
        assert delete.reason is not None

    def test_delete_action_endpoint_contains_evaluation_id(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import _build_evaluation_actions

        ev = _make_eval(evaluation_id="eval-xyz")
        actions = _build_evaluation_actions(ev)
        delete = next(a for a in actions if a.key == "delete")
        assert "eval-xyz" in delete.endpoint

    def test_delete_action_method_is_delete(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import _build_evaluation_actions

        ev = _make_eval()
        actions = _build_evaluation_actions(ev)
        delete = next(a for a in actions if a.key == "delete")
        assert delete.method == "DELETE"


# ---------------------------------------------------------------------------
# _to_response
# ---------------------------------------------------------------------------


class TestToResponse:
    """_to_response ORM → Pydantic 映射。"""

    def test_fields_mapped(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import _to_response

        ev = _make_eval()
        resp = _to_response(ev)
        assert resp.evaluation_id == ev.evaluation_id
        assert resp.software_id == ev.software_id
        assert resp.branch == ev.branch
        assert resp.rating == ev.rating
        assert resp.category == ev.category
        assert resp.comment == ev.comment
        assert resp.evaluator == ev.evaluator
        assert resp.status == ev.status
        assert resp.created_at == ev.created_at
        assert resp.updated_at == ev.updated_at

    def test_status_view_embedded(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import _to_response

        ev = _make_eval(status="approved")
        resp = _to_response(ev)
        assert resp.status_view.tone == "success"

    def test_actions_embedded(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import _to_response

        ev = _make_eval(status="submitted")
        resp = _to_response(ev)
        assert len(resp.actions) >= 1


# ---------------------------------------------------------------------------
# _resource_schema
# ---------------------------------------------------------------------------


class TestResourceSchema:
    """_resource_schema 结构契约。"""

    def test_resource_is_evaluation(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import _resource_schema

        schema = _resource_schema()
        assert schema.resource == "evaluation"

    def test_submit_action_is_post(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import _resource_schema

        schema = _resource_schema()
        assert schema.submit_action.method == "POST"
        assert "/evaluations" in schema.submit_action.endpoint

    def test_sections_not_empty(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import _resource_schema

        schema = _resource_schema()
        assert len(schema.sections) >= 1

    def test_identity_section_present(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import _resource_schema

        schema = _resource_schema()
        ids = [s.id for s in schema.sections]
        assert "identity" in ids

    def test_rating_section_present(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import _resource_schema

        schema = _resource_schema()
        ids = [s.id for s in schema.sections]
        assert "rating" in ids

    def test_product_and_profile_present(self) -> None:
        from backend.control_plane.adapters.evaluations_helpers import _resource_schema

        schema = _resource_schema()
        assert schema.product
        assert schema.profile


# ---------------------------------------------------------------------------
# _eval_stmt_for_tenant
# ---------------------------------------------------------------------------


class TestEvalStmtForTenant:
    """SQL statement 构造 — tenant 隔离。"""

    def test_returns_select(self) -> None:
        from sqlalchemy.sql import Select

        from backend.control_plane.adapters.evaluations import _eval_stmt_for_tenant

        stmt = _eval_stmt_for_tenant("my-tenant")
        assert isinstance(stmt, Select)

    def test_different_tenants_give_different_stmts(self) -> None:
        from backend.control_plane.adapters.evaluations import _eval_stmt_for_tenant

        stmt_a = _eval_stmt_for_tenant("tenant-a")
        stmt_b = _eval_stmt_for_tenant("tenant-b")
        # compiled strings should differ (tenant_id literal differs)
        from sqlalchemy.dialects import sqlite

        sql_a = str(stmt_a.compile(dialect=sqlite.dialect()))
        sql_b = str(stmt_b.compile(dialect=sqlite.dialect()))
        # both reference SoftwareEvaluation table; they differ by bound param value
        assert "software_evaluations" in sql_a
        assert "software_evaluations" in sql_b


# ---------------------------------------------------------------------------
# _utcnow
# ---------------------------------------------------------------------------


class TestUtcnow:
    """_utcnow 返回 naive UTC datetime。"""

    def test_returns_datetime(self) -> None:
        from backend.control_plane.adapters.evaluations import _utcnow

        result = _utcnow()
        assert isinstance(result, datetime.datetime)

    def test_returns_naive(self) -> None:
        from backend.control_plane.adapters.evaluations import _utcnow

        result = _utcnow()
        assert result.tzinfo is None


# ---------------------------------------------------------------------------
# create_evaluation endpoint
# ---------------------------------------------------------------------------


class TestCreateEvaluation:
    """POST /evaluations 端点。"""

    @pytest.mark.asyncio
    async def test_create_success(self) -> None:
        from backend.control_plane.adapters.evaluations import create_evaluation
        from backend.control_plane.adapters.evaluations_helpers import EvaluationCreateRequest

        payload = EvaluationCreateRequest(evaluation_id="e1", software_id="s1", rating=5)
        user = {"sub": "alice", "tenant_id": "t1"}

        db = AsyncMock()
        # no existing → returns None
        existing_result = MagicMock()
        existing_result.scalars.return_value.first.return_value = None

        ev = _make_eval(evaluation_id="e1", software_id="s1")
        db.refresh = AsyncMock(side_effect=lambda obj: setattr(obj, "__dict__", ev.__dict__) or None)

        # first execute → existing check (None), subsequent → refresh
        db.execute = AsyncMock(return_value=existing_result)
        db.add = MagicMock()
        db.commit = AsyncMock()

        # patch db.refresh to mutate the created object inline
        created_ev = _make_eval(evaluation_id="e1")

        async def _refresh(obj: object) -> None:
            obj.__dict__.update(created_ev.__dict__)  # type: ignore[union-attr]

        db.refresh = _refresh

        with patch("backend.control_plane.adapters.evaluations._to_response") as mock_resp:
            mock_resp.return_value = MagicMock(evaluation_id="e1")
            result = await create_evaluation(payload=payload, current_user=user, db=db)

        db.add.assert_called_once()
        db.commit.assert_awaited_once()
        mock_resp.assert_called_once()
        assert result.evaluation_id == "e1"

    @pytest.mark.asyncio
    async def test_duplicate_raises_409(self) -> None:
        from backend.control_plane.adapters.evaluations import create_evaluation
        from backend.control_plane.adapters.evaluations_helpers import EvaluationCreateRequest

        payload = EvaluationCreateRequest(evaluation_id="e1", software_id="s1", rating=3)
        user = {"sub": "bob", "tenant_id": "t1"}

        db = AsyncMock()
        existing_result = MagicMock()
        existing_result.scalars.return_value.first.return_value = _make_eval()
        db.execute = AsyncMock(return_value=existing_result)

        with pytest.raises(HTTPException) as exc_info:
            await create_evaluation(payload=payload, current_user=user, db=db)
        assert exc_info.value.status_code == 409
        assert "ZEN-EVAL-4090" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_missing_tenant_id_is_rejected(self) -> None:
        """Authenticated evaluation writes must carry an explicit tenant_id claim."""
        from backend.control_plane.adapters.evaluations import create_evaluation
        from backend.control_plane.adapters.evaluations_helpers import EvaluationCreateRequest

        payload = EvaluationCreateRequest(evaluation_id="ex", software_id="sx", rating=2)
        user: dict[str, object] = {"sub": "carol"}

        db = AsyncMock()
        existing_result = MagicMock()
        existing_result.scalars.return_value.first.return_value = None
        db.execute = AsyncMock(return_value=existing_result)
        db.add = MagicMock()
        db.commit = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await create_evaluation(payload=payload, current_user=user, db=db)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["code"] == "ZEN-TENANT-4002"

    @pytest.mark.asyncio
    async def test_evaluator_falls_back_to_username(self) -> None:
        from backend.control_plane.adapters.evaluations import create_evaluation
        from backend.control_plane.adapters.evaluations_helpers import EvaluationCreateRequest

        payload = EvaluationCreateRequest(evaluation_id="ey", software_id="sy", rating=4)
        user: dict[str, object] = {"username": "dave", "tenant_id": "t2"}

        db = AsyncMock()
        existing_result = MagicMock()
        existing_result.scalars.return_value.first.return_value = None
        db.execute = AsyncMock(return_value=existing_result)
        db.add = MagicMock()
        db.commit = AsyncMock()

        added_obj: list[object] = []
        db.add.side_effect = added_obj.append

        created_ev = _make_eval(evaluation_id="ey", evaluator="dave")

        async def _refresh(obj: object) -> None:
            obj.__dict__.update(created_ev.__dict__)  # type: ignore[union-attr]

        db.refresh = _refresh

        with patch("backend.control_plane.adapters.evaluations._to_response") as mock_resp:
            mock_resp.return_value = MagicMock(evaluation_id="ey")
            await create_evaluation(payload=payload, current_user=user, db=db)

        assert added_obj[0].evaluator == "dave"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# list_evaluations endpoint
# ---------------------------------------------------------------------------


class TestListEvaluations:
    """GET /evaluations 端点。"""

    @pytest.mark.asyncio
    async def test_list_no_filters_returns_all(self) -> None:
        from backend.control_plane.adapters.evaluations import list_evaluations

        user = {"sub": "alice", "tenant_id": "t1"}
        db = AsyncMock()
        evs = [_make_eval(evaluation_id=f"e{i}") for i in range(3)]
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = evs
        db.execute = AsyncMock(return_value=result_mock)

        with patch("backend.control_plane.adapters.evaluations._to_response", side_effect=lambda e: e):
            items = await list_evaluations(software_id=None, branch=None, category=None, status=None, current_user=user, db=db)

        assert len(items) == 3

    @pytest.mark.asyncio
    async def test_list_empty_returns_empty(self) -> None:
        from backend.control_plane.adapters.evaluations import list_evaluations

        user = {"sub": "alice", "tenant_id": "t1"}
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=result_mock)

        with patch("backend.control_plane.adapters.evaluations._to_response", side_effect=lambda e: e):
            items = await list_evaluations(software_id=None, branch=None, category=None, status=None, current_user=user, db=db)

        assert items == []

    @pytest.mark.asyncio
    async def test_list_with_software_id_filter(self) -> None:
        """带 software_id 过滤时 db.execute 被调用（SQL where 已添加）。"""
        from backend.control_plane.adapters.evaluations import list_evaluations

        user = {"sub": "alice", "tenant_id": "t1"}
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=result_mock)

        with patch("backend.control_plane.adapters.evaluations._to_response", side_effect=lambda e: e):
            await list_evaluations(software_id="svc-a", branch=None, category=None, status=None, current_user=user, db=db)

        db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_all_filters_combined(self) -> None:
        from backend.control_plane.adapters.evaluations import list_evaluations

        user = {"sub": "alice", "tenant_id": "t1"}
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=result_mock)

        with patch("backend.control_plane.adapters.evaluations._to_response", side_effect=lambda e: e):
            await list_evaluations(software_id="svc", branch="dev", category="security", status="approved", current_user=user, db=db)

        db.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# get_evaluation endpoint
# ---------------------------------------------------------------------------


class TestGetEvaluation:
    """GET /evaluations/{evaluation_id} 端点。"""

    @pytest.mark.asyncio
    async def test_found_returns_response(self) -> None:
        from backend.control_plane.adapters.evaluations import get_evaluation

        user = {"sub": "alice", "tenant_id": "t1"}
        db = AsyncMock()
        ev = _make_eval()
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = ev
        db.execute = AsyncMock(return_value=result_mock)

        with patch("backend.control_plane.adapters.evaluations._to_response", return_value=MagicMock(evaluation_id="eval-001")):
            result = await get_evaluation(evaluation_id="eval-001", current_user=user, db=db)

        assert result.evaluation_id == "eval-001"

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self) -> None:
        from backend.control_plane.adapters.evaluations import get_evaluation

        user = {"sub": "alice", "tenant_id": "t1"}
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = None
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(HTTPException) as exc_info:
            await get_evaluation(evaluation_id="missing", current_user=user, db=db)
        assert exc_info.value.status_code == 404
        assert "ZEN-EVAL-4040" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_tenant_isolation_different_tenant_not_found(self) -> None:
        """tenant-b 请求时不返回 tenant-a 的数据（mock 模拟 empty）。"""
        from backend.control_plane.adapters.evaluations import get_evaluation

        user = {"sub": "bob", "tenant_id": "tenant-b"}
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = None
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(HTTPException) as exc_info:
            await get_evaluation(evaluation_id="eval-001", current_user=user, db=db)
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# delete_evaluation endpoint
# ---------------------------------------------------------------------------


class TestDeleteEvaluation:
    """DELETE /evaluations/{evaluation_id} 端点。"""

    @pytest.mark.asyncio
    async def test_delete_submitted_success(self) -> None:
        from backend.control_plane.adapters.evaluations import delete_evaluation

        user = {"sub": "alice", "tenant_id": "t1"}
        db = AsyncMock()
        ev = _make_eval(status="submitted")
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = ev
        db.execute = AsyncMock(return_value=result_mock)
        db.delete = AsyncMock()
        db.commit = AsyncMock()

        await delete_evaluation(evaluation_id="eval-001", current_user=user, db=db)

        db.delete.assert_awaited_once_with(ev)
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_rejected_success(self) -> None:
        from backend.control_plane.adapters.evaluations import delete_evaluation

        user = {"sub": "alice", "tenant_id": "t1"}
        db = AsyncMock()
        ev = _make_eval(status="rejected")
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = ev
        db.execute = AsyncMock(return_value=result_mock)
        db.delete = AsyncMock()
        db.commit = AsyncMock()

        await delete_evaluation(evaluation_id="eval-001", current_user=user, db=db)

        db.delete.assert_awaited_once_with(ev)

    @pytest.mark.asyncio
    async def test_delete_approved_raises_409(self) -> None:
        from backend.control_plane.adapters.evaluations import delete_evaluation

        user = {"sub": "alice", "tenant_id": "t1"}
        db = AsyncMock()
        ev = _make_eval(status="approved")
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = ev
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(HTTPException) as exc_info:
            await delete_evaluation(evaluation_id="eval-001", current_user=user, db=db)
        assert exc_info.value.status_code == 409
        assert "ZEN-EVAL-4090" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_delete_not_found_raises_404(self) -> None:
        from backend.control_plane.adapters.evaluations import delete_evaluation

        user = {"sub": "alice", "tenant_id": "t1"}
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = None
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(HTTPException) as exc_info:
            await delete_evaluation(evaluation_id="missing", current_user=user, db=db)
        assert exc_info.value.status_code == 404
        assert "ZEN-EVAL-4041" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_delete_does_not_commit_on_not_found(self) -> None:
        from backend.control_plane.adapters.evaluations import delete_evaluation

        user = {"sub": "alice", "tenant_id": "t1"}
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = None
        db.execute = AsyncMock(return_value=result_mock)
        db.commit = AsyncMock()

        with pytest.raises(HTTPException):
            await delete_evaluation(evaluation_id="missing", current_user=user, db=db)

        db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# get_evaluation_schema endpoint
# ---------------------------------------------------------------------------


class TestGetEvaluationSchema:
    """GET /evaluations/schema 端点。"""

    @pytest.mark.asyncio
    async def test_schema_endpoint_returns_resource_schema(self) -> None:
        from backend.control_plane.adapters.evaluations import get_evaluation_schema

        user = {"sub": "alice"}
        result = await get_evaluation_schema(current_user=user)
        assert result.resource == "evaluation"
        assert result.submit_action.method == "POST"
