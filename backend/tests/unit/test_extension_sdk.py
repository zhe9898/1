from __future__ import annotations

from backend.extensions.connector_kind_registry import get_connector_kind_info, validate_connector_config
from backend.extensions.extension_sdk import (
    bootstrap_extension_runtime,
    get_extension_info,
    list_extensions,
    list_published_job_kinds,
    load_extension_manifests_from_dir,
    register_extension_manifest,
)
from backend.extensions.job_kind_registry import get_job_kind_info, validate_job_payload, validate_job_result
from backend.extensions.workflow_template_registry import get_workflow_template_info, render_workflow_template
from backend.tests.unit.extensions_test_support import build_extension_manifest_yaml, build_photo_extension_manifest


def test_builtin_extension_surface_includes_connector_invoke() -> None:
    kinds = {item["kind"]: item for item in list_published_job_kinds()}
    assert "connector.invoke" in kinds
    metadata = kinds["connector.invoke"]["metadata"]
    assert metadata["extension_id"] == "zen70.core"
    assert metadata["sdk_version"] == "1.0.0"


def test_register_extension_manifest_publishes_metadata_and_schemas() -> None:
    register_extension_manifest(build_photo_extension_manifest(), replace_existing=True)

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
        build_extension_manifest_yaml(
            extension_id="acme.fs",
            name="Acme Filesystem Pack",
            description="Filesystem-backed extension manifest",
            job_kind="photo.fs.index",
            connector_kind="photo.fs.connector",
            template_id="photo.fs.ingest",
        ),
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
        build_extension_manifest_yaml(
            extension_id="acme.reload",
            name="Reloadable Pack",
            description="Reload test",
            job_kind="reload.job",
            include_result_schema=False,
        ),
        encoding="utf-8",
    )

    bootstrap_extension_runtime(manifest_dir, force_reload_external=True)
    assert get_job_kind_info("reload.job")["metadata"]["extension_id"] == "acme.reload"

    manifest_path.unlink()
    bootstrap_extension_runtime(manifest_dir, force_reload_external=True)
    assert all(item["kind"] != "reload.job" for item in list_published_job_kinds())
