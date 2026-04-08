from __future__ import annotations

from pydantic import BaseModel

from backend.kernel.extensions.connector_kind_registry import get_connector_kind_info, validate_connector_config
from backend.kernel.extensions.extension_sdk import (
    CompatibilityPolicy,
    ConnectorKindSpec,
    ExtensionManifest,
    JobKindSpec,
    WorkflowTemplateSpec,
    bootstrap_extension_runtime,
    get_extension_info,
    list_extensions,
    list_published_job_kinds,
    load_extension_manifests_from_dir,
    register_extension_manifest,
)
from backend.kernel.extensions.job_kind_registry import get_job_kind_info, validate_job_payload, validate_job_result
from backend.kernel.extensions.workflow_template_registry import get_workflow_template_info, render_workflow_template


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


def test_builtin_extension_surface_includes_connector_invoke() -> None:
    kinds = {item["kind"]: item for item in list_published_job_kinds()}
    assert "connector.invoke" in kinds
    metadata = kinds["connector.invoke"]["metadata"]
    assert metadata["extension_id"] == "zen70.core"
    assert metadata["sdk_version"] == "1.0.0"


def test_register_extension_manifest_publishes_metadata_and_schemas() -> None:
    register_extension_manifest(
        ExtensionManifest(
            extension_id="acme.photo",
            version="1.2.3",
            name="Acme Photo Pack",
            publisher="Acme",
            description="Photo ingestion kinds and templates.",
            compatibility=CompatibilityPolicy(
                min_kernel_version="1.58.0",
                supported_api_versions=("v1",),
                compatibility_mode="same-major",
                notes="Requires the v1 control-plane API surface.",
            ),
            job_kinds=(
                JobKindSpec(
                    kind="photo.index",
                    payload_schema=PhotoIndexPayload,
                    result_schema=PhotoIndexResult,
                    schema_version="2.0.0",
                    stability="stable",
                    description="Index media assets for the photo library.",
                ),
            ),
            connector_kinds=(
                ConnectorKindSpec(
                    kind="photo.library",
                    config_schema=PhotoConnectorConfig,
                    schema_version="1.1.0",
                    stability="beta",
                    description="Connector for a photo library root.",
                ),
            ),
            workflow_templates=(
                WorkflowTemplateSpec(
                    template_id="photo.ingest",
                    version="1.0.0",
                    schema_version="1.0.0",
                    display_name="Photo Ingest",
                    description="Run the photo indexing pipeline.",
                    parameters_schema=PhotoIngestParams,
                    labels=("photo", "ingest"),
                    steps=(
                        {
                            "id": "index",
                            "kind": "photo.index",
                            "payload": {
                                "album_id": "${album_id}",
                                "max_items": "${max_items}",
                            },
                        },
                    ),
                ),
            ),
            source_manifest_path="contracts/extensions/acme.photo.yaml",
        ),
        replace_existing=True,
    )

    extension_info = get_extension_info("acme.photo")
    assert extension_info["version"] == "1.2.3"
    assert extension_info["job_kinds"] == ["photo.index"]
    assert extension_info["workflow_templates"] == ["photo.ingest"]

    validated_payload = validate_job_payload("photo.index", {"album_id": "album-1"})
    assert validated_payload["max_items"] == 100

    validated_result = validate_job_result("photo.index", {"indexed_items": 42})
    assert validated_result["status"] == "ok"

    job_info = get_job_kind_info("photo.index")
    assert job_info["metadata"]["extension_id"] == "acme.photo"
    assert job_info["metadata"]["schema_version"] == "2.0.0"
    assert job_info["payload_schema"] is not None

    validated_config = validate_connector_config("photo.library", {"root_uri": "file:///albums"})
    assert validated_config["readonly"] is True

    connector_info = get_connector_kind_info("photo.library")
    assert connector_info["metadata"]["extension_id"] == "acme.photo"
    assert connector_info["metadata"]["stability"] == "beta"

    template_info = get_workflow_template_info("photo.ingest")
    assert template_info["metadata"]["extension_id"] == "acme.photo"
    assert template_info["parameters_schema"] is not None

    rendered = render_workflow_template("photo.ingest", {"album_id": "album-1", "max_items": 25})
    assert rendered["steps"][0]["payload"]["album_id"] == "album-1"
    assert rendered["steps"][0]["payload"]["max_items"] == 25

    extension_ids = {item["extension_id"] for item in list_extensions()}
    assert "zen70.core" in extension_ids
    assert "acme.photo" in extension_ids


def test_load_extension_manifest_from_directory(tmp_path) -> None:
    manifest_dir = tmp_path / "extensions"
    manifest_dir.mkdir()
    manifest_path = manifest_dir / "demo.yaml"
    manifest_path.write_text(
        """
extension_id: acme.fs
version: 1.0.0
name: Acme Filesystem Pack
publisher: Acme
description: Filesystem-backed extension manifest
compatibility:
  min_kernel_version: 1.58.0
job_kinds:
  - kind: photo.fs.index
    payload_schema_ref: backend.tests.unit.test_extension_sdk:PhotoIndexPayload
    result_schema_ref: backend.tests.unit.test_extension_sdk:PhotoIndexResult
connector_kinds:
  - kind: photo.fs.connector
    config_schema_ref: backend.tests.unit.test_extension_sdk:PhotoConnectorConfig
workflow_templates:
  - template_id: photo.fs.ingest
    parameters_schema_ref: backend.tests.unit.test_extension_sdk:PhotoIngestParams
    steps:
      - id: index
        kind: photo.fs.index
        payload:
          album_id: ${album_id}
          max_items: ${max_items}
""".strip(),
        encoding="utf-8",
    )

    loaded = load_extension_manifests_from_dir(manifest_dir)
    assert loaded[0].extension_id == "acme.fs"
    assert loaded[0].source_manifest_path == str(manifest_path.resolve())

    bootstrap_extension_runtime(manifest_dir, force_reload_external=True)
    info = get_extension_info("acme.fs")
    assert info["source_manifest_path"] == str(manifest_path.resolve())

    rendered = render_workflow_template("photo.fs.ingest", {"album_id": "album-7"})
    assert rendered["steps"][0]["payload"]["album_id"] == "album-7"


def test_bootstrap_extension_runtime_reconciles_removed_manifests(tmp_path) -> None:
    manifest_dir = tmp_path / "extensions"
    manifest_dir.mkdir()
    manifest_path = manifest_dir / "demo.yaml"
    manifest_path.write_text(
        """
extension_id: acme.reload
version: 1.0.0
name: Reloadable Pack
publisher: Acme
description: Reload test
compatibility:
  min_kernel_version: 1.58.0
job_kinds:
  - kind: reload.job
    payload_schema_ref: backend.tests.unit.test_extension_sdk:PhotoIndexPayload
""".strip(),
        encoding="utf-8",
    )

    bootstrap_extension_runtime(manifest_dir, force_reload_external=True)
    assert get_job_kind_info("reload.job")["metadata"]["extension_id"] == "acme.reload"

    manifest_path.unlink()
    bootstrap_extension_runtime(manifest_dir, force_reload_external=True)
    assert all(item["kind"] != "reload.job" for item in list_published_job_kinds())
