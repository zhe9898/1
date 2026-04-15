from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel

from backend.extensions.extension_sdk import (
    CompatibilityPolicy,
    ConnectorKindSpec,
    ExtensionManifest,
    JobKindSpec,
    WorkflowTemplateSpec,
)


class PhotoIndexPayload(BaseModel):
    album_id: str
    max_items: int = 100


class PhotoIndexResult(BaseModel):
    indexed_items: int
    status: str = "ok"


class PhotoConnectorConfig(BaseModel):
    root_uri: str
    readonly: bool = True


class PhotoIngestParams(BaseModel):
    album_id: str
    max_items: int = 100


def _schema_ref(model: type[BaseModel]) -> str:
    return f"{model.__module__}:{model.__name__}"


def build_photo_extension_manifest(
    *,
    extension_id: str = "acme.photo",
    version: str = "1.2.3",
    name: str = "Acme Photo Pack",
    publisher: str = "Acme",
    description: str = "Photo ingestion kinds and templates.",
    job_kind: str = "photo.index",
    connector_kind: str = "photo.library",
    template_id: str = "photo.ingest",
    source_manifest_path: str = "contracts/extensions/acme.photo.yaml",
) -> ExtensionManifest:
    return ExtensionManifest(
        extension_id=extension_id,
        version=version,
        name=name,
        publisher=publisher,
        description=description,
        compatibility=CompatibilityPolicy(
            min_kernel_version="1.58.0",
            supported_api_versions=("v1",),
            compatibility_mode="same-major",
            notes="Requires the v1 control-plane API surface.",
        ),
        job_kinds=(
            JobKindSpec(
                kind=job_kind,
                payload_schema=PhotoIndexPayload,
                result_schema=PhotoIndexResult,
                schema_version="2.0.0",
                stability="stable",
                description="Index media assets for the photo library.",
            ),
        ),
        connector_kinds=(
            ConnectorKindSpec(
                kind=connector_kind,
                config_schema=PhotoConnectorConfig,
                schema_version="1.1.0",
                stability="beta",
                description="Connector for a photo library root.",
            ),
        ),
        workflow_templates=(
            WorkflowTemplateSpec(
                template_id=template_id,
                version="1.0.0",
                schema_version="1.0.0",
                display_name="Photo Ingest",
                description="Run the photo indexing pipeline.",
                parameters_schema=PhotoIngestParams,
                labels=("photo", "ingest"),
                steps=(
                    {
                        "id": "index",
                        "kind": job_kind,
                        "payload": {
                            "album_id": "${album_id}",
                            "max_items": "${max_items}",
                        },
                    },
                ),
            ),
        ),
        source_manifest_path=source_manifest_path,
    )


def build_extension_manifest_yaml(
    *,
    extension_id: str,
    name: str,
    description: str,
    version: str = "1.0.0",
    publisher: str = "Acme",
    job_kind: str,
    connector_kind: str | None = None,
    template_id: str | None = None,
    include_result_schema: bool = True,
) -> str:
    payload: dict[str, Any] = {
        "extension_id": extension_id,
        "version": version,
        "name": name,
        "publisher": publisher,
        "description": description,
        "compatibility": {
            "min_kernel_version": "1.58.0",
        },
        "job_kinds": [
            {
                "kind": job_kind,
                "payload_schema_ref": _schema_ref(PhotoIndexPayload),
            },
        ],
    }
    if include_result_schema:
        payload["job_kinds"][0]["result_schema_ref"] = _schema_ref(PhotoIndexResult)
    if connector_kind is not None:
        payload["connector_kinds"] = [
            {
                "kind": connector_kind,
                "config_schema_ref": _schema_ref(PhotoConnectorConfig),
            },
        ]
    if template_id is not None:
        payload["workflow_templates"] = [
            {
                "template_id": template_id,
                "parameters_schema_ref": _schema_ref(PhotoIngestParams),
                "steps": [
                    {
                        "id": "index",
                        "kind": job_kind,
                        "payload": {
                            "album_id": "${album_id}",
                            "max_items": "${max_items}",
                        },
                    },
                ],
            },
        ]
    return yaml.safe_dump(payload, sort_keys=False).strip()
