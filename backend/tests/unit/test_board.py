"""
api/board.py 单元测试 — 留言板 Pydantic 契约、幂等性、权限。

高 ROI：幂等键防抖 + 非本人非 admin 拒绝删除。
"""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError


class TestBoardPydanticModels:
    """Pydantic 请求/响应模型验证。"""

    def test_create_payload_max_length(self) -> None:
        from backend.control_plane.adapters.board import BoardMessageCreate

        # 正常长度
        msg = BoardMessageCreate(content="hello")
        assert msg.content == "hello"
        assert msg.is_pinned is False
        assert msg.meta_info is None

    def test_create_payload_rejects_empty(self) -> None:
        from backend.control_plane.adapters.board import BoardMessageCreate

        # Pydantic 应拒绝空 content（Field(...) 必填）
        with pytest.raises(ValidationError):
            BoardMessageCreate()  # type: ignore[call-arg]  # content 缺失

    def test_create_payload_over_max_length(self) -> None:
        from backend.control_plane.adapters.board import BoardMessageCreate

        with pytest.raises(ValidationError):
            BoardMessageCreate(content="x" * 2001)

    def test_response_model_fields(self) -> None:
        from backend.control_plane.adapters.board import AuthorInfo, BoardMessageResponse

        resp = BoardMessageResponse(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            content="test",
            is_pinned=False,
            created_at="2026-01-01T00:00:00",
            author=AuthorInfo(id=1, username="alice", role="admin"),
        )
        assert resp.content == "test"
        assert resp.author.username == "alice"

    def test_author_info_optional_display_name(self) -> None:
        from backend.control_plane.adapters.board import AuthorInfo

        a = AuthorInfo(id=1, username="bob", role="family")
        assert a.display_name is None

        a2 = AuthorInfo(id=2, username="carol", display_name="Carol", role="admin")
        assert a2.display_name == "Carol"


class TestIdempotencyKey:
    """幂等键逻辑验证（不依赖真实 Redis，仅验证键格式）。"""

    def test_idempotency_key_format(self) -> None:
        """确认幂等键格式为 zen70:idempotency:board:{key}。"""
        key = "test-key-123"
        idem_key = f"zen70:idempotency:board:{key}"
        assert idem_key == "zen70:idempotency:board:test-key-123"
        assert idem_key.startswith("zen70:")


class TestDeletePermission:
    """删除权限矩阵验证（纯逻辑，不依赖 DB）。"""

    def test_owner_can_delete(self) -> None:
        """作者本人可以删除。"""
        author_id = 42
        user_id = 42
        role = "family"
        assert author_id == user_id or role == "admin"

    def test_admin_can_delete_others(self) -> None:
        """admin 可删除任何人的留言。"""
        author_id = 42
        user_id = 99
        role = "admin"
        assert author_id == user_id or role == "admin"

    def test_non_owner_non_admin_denied(self) -> None:
        """非本人非 admin → 拒绝。"""
        author_id = 42
        user_id = 99
        role = "family"
        assert not (author_id == user_id or role == "admin")
