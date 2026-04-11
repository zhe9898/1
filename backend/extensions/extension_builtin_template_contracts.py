"""Builtin workflow template parameter contracts."""

from __future__ import annotations

from pydantic import BaseModel


class HttpHealthcheckTemplateParams(BaseModel):
    target: str
    expected_status: int = 200
    timeout: int = 10


class FileTransferTemplateParams(BaseModel):
    src: str
    dst: str
    overwrite: bool = False
    mkdir: bool = True
    verify_sha256: str | None = None
