"""Software Evaluation Pydantic models, schema, and helper functions.

Extracted from evaluations.py for maintainability.
Route handlers remain in evaluations.py.
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

from backend.control_plane.adapters.action_contracts import ControlAction
from backend.control_plane.adapters.ui_contracts import (
    FormFieldOption,
    FormFieldSchema,
    FormSectionSchema,
    ResourceSchemaResponse,
    StatusView,
)
from backend.kernel.profiles.public_profile import DEFAULT_PRODUCT_NAME, normalize_gateway_profile, to_public_profile
from backend.models.software_evaluation import SoftwareEvaluation

# 鈹€鈹€ Pydantic request/response models 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

VALID_RATINGS = {1, 2, 3, 4, 5}
VALID_CATEGORIES = {"general", "performance", "reliability", "usability", "security"}
VALID_STATUSES = {"submitted", "approved", "rejected"}


class EvaluationCreateRequest(BaseModel):
    evaluation_id: str = Field(..., min_length=1, max_length=128)
    software_id: str = Field(..., min_length=1, max_length=128)
    branch: str = Field(default="main", min_length=1, max_length=128)
    rating: int = Field(..., ge=1, le=5)
    category: str = Field(default="general", min_length=1, max_length=64)
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("category")
    @classmethod
    def _validate_category(cls, value: str) -> str:
        if value not in VALID_CATEGORIES:
            allowed = ", ".join(sorted(VALID_CATEGORIES))
            raise PydanticCustomError(
                "category_invalid",
                "category must be one of: {allowed}",
                {"allowed": allowed},
            )
        return value


class EvaluationResponse(BaseModel):
    evaluation_id: str
    software_id: str
    branch: str
    rating: int
    category: str
    comment: str | None
    evaluator: str
    status: str
    status_view: StatusView
    actions: list[ControlAction] = Field(default_factory=list)
    created_at: datetime.datetime
    updated_at: datetime.datetime


# 鈹€鈹€ Helper functions 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def _evaluation_status_view(status: str) -> StatusView:
    mapping: dict[str, tuple[str, str]] = {
        "submitted": ("Submitted", "neutral"),
        "approved": ("Approved", "success"),
        "rejected": ("Rejected", "error"),
    }
    label, tone = mapping.get(status, (status.title(), "neutral"))
    return StatusView(key=status, label=label, tone=tone)


def _build_evaluation_actions(evaluation: SoftwareEvaluation) -> list[ControlAction]:
    can_delete = evaluation.status in {"submitted", "rejected"}
    return [
        ControlAction(
            key="delete",
            label="Delete",
            endpoint=f"/v1/evaluations/{evaluation.evaluation_id}",
            method="DELETE",
            enabled=can_delete,
            requires_admin=False,
            reason=None if can_delete else f"Evaluation status {evaluation.status} does not allow deletion",
            confirmation="Are you sure you want to delete this evaluation?",
            fields=[],
        ),
    ]


def _to_response(evaluation: SoftwareEvaluation) -> EvaluationResponse:
    return EvaluationResponse(
        evaluation_id=evaluation.evaluation_id,
        software_id=evaluation.software_id,
        branch=evaluation.branch,
        rating=evaluation.rating,
        category=evaluation.category,
        comment=evaluation.comment,
        evaluator=evaluation.evaluator,
        status=evaluation.status,
        status_view=_evaluation_status_view(evaluation.status),
        actions=_build_evaluation_actions(evaluation),
        created_at=evaluation.created_at,
        updated_at=evaluation.updated_at,
    )


def _resource_schema() -> ResourceSchemaResponse:
    import os

    profile = normalize_gateway_profile(os.getenv("GATEWAY_PROFILE"))
    product = DEFAULT_PRODUCT_NAME
    return ResourceSchemaResponse(
        product=product,
        profile=to_public_profile(profile),
        runtime_profile=profile,
        resource="evaluation",
        title="Software Evaluations",
        description="Submit and review evaluations for all software branches and components.",
        empty_state="No evaluations have been submitted yet.",
        policies={},
        submit_action=ControlAction(
            key="submit",
            label="Submit Evaluation",
            endpoint="/v1/evaluations",
            method="POST",
            enabled=True,
            requires_admin=False,
            reason=None,
            confirmation=None,
            fields=[],
        ),
        sections=[
            FormSectionSchema(
                id="identity",
                label="Software Identity",
                description="Identify the software and branch being evaluated.",
                fields=[
                    FormFieldSchema(
                        key="evaluation_id",
                        label="Evaluation ID",
                        input_type="text",
                        required=True,
                        placeholder="eval-001",
                        description="Unique identifier for this evaluation.",
                    ),
                    FormFieldSchema(
                        key="software_id",
                        label="Software ID",
                        input_type="text",
                        required=True,
                        placeholder="my-service-v2",
                        description="Identifier for the software component.",
                    ),
                    FormFieldSchema(
                        key="branch",
                        label="Branch",
                        input_type="text",
                        required=False,
                        placeholder="main",
                        value="main",
                        description="Branch or version of the software.",
                    ),
                ],
            ),
            FormSectionSchema(
                id="rating",
                label="Rating",
                description="Rate the software component.",
                fields=[
                    FormFieldSchema(
                        key="rating",
                        label="Rating (1鈥?)",
                        input_type="number",
                        required=True,
                        placeholder="5",
                        description="Overall rating from 1 (lowest) to 5 (highest).",
                    ),
                    FormFieldSchema(
                        key="category",
                        label="Category",
                        input_type="select",
                        required=False,
                        value="general",
                        options=[
                            FormFieldOption(value="general", label="General"),
                            FormFieldOption(value="performance", label="Performance"),
                            FormFieldOption(value="reliability", label="Reliability"),
                            FormFieldOption(value="usability", label="Usability"),
                            FormFieldOption(value="security", label="Security"),
                        ],
                    ),
                    FormFieldSchema(
                        key="comment",
                        label="Comment",
                        input_type="textarea",
                        required=False,
                        placeholder="Optional detailed feedback",
                        description="Additional comments about the evaluation.",
                    ),
                ],
            ),
        ],
    )
