from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_health_pack_placeholders_removed_and_delivery_files_present() -> None:
    ios_dir = ROOT / "clients" / "health-ios"
    android_dir = ROOT / "clients" / "health-android"

    assert not (ios_dir / "placeholder.yaml").exists()
    assert not (android_dir / "placeholder.yaml").exists()

    assert (ios_dir / "client.yaml").exists()
    assert (ios_dir / "Package.swift").exists()
    assert (ios_dir / "Sources" / "HealthGatewayClient" / "GatewayBootstrapConfig.swift").exists()
    assert (ios_dir / "Sources" / "HealthGatewayClient" / "GatewayIdentityContext.swift").exists()
    assert (ios_dir / "Sources" / "HealthGatewayClient" / "HealthIngestEnvelope.swift").exists()

    assert (android_dir / "client.yaml").exists()
    assert (android_dir / "settings.gradle.kts").exists()
    assert (android_dir / "build.gradle.kts").exists()
    assert (android_dir / "src" / "main" / "kotlin" / "io" / "zen70" / "healthgateway" / "GatewayBootstrapConfig.kt").exists()
    assert (android_dir / "src" / "main" / "kotlin" / "io" / "zen70" / "healthgateway" / "GatewayIdentityContext.kt").exists()
    assert (android_dir / "src" / "main" / "kotlin" / "io" / "zen70" / "healthgateway" / "HealthIngestEnvelope.kt").exists()


def test_health_pack_clients_consume_gateway_identity_contract() -> None:
    ios_client = _read(ROOT / "clients" / "health-ios" / "client.yaml")
    android_client = _read(ROOT / "clients" / "health-android" / "client.yaml")
    ios_readme = _read(ROOT / "clients" / "health-ios" / "README.md")
    android_readme = _read(ROOT / "clients" / "health-android" / "README.md")

    for content in (ios_client, android_client):
        assert "delivery_stage: mvp-skeleton" in content
        assert "auth_provider: gateway-identity" in content
        assert "tenant_context: required" in content
        assert "RUNNER_TENANT_ID" in content
        assert "GATEWAY_BASE_URL" in content
        assert "GATEWAY_CA_FILE" in content
        assert "health.ingest" in content

    assert "Gateway Identity" in ios_readme
    assert "Gateway Identity" in android_readme
    assert "tenant_id" in ios_readme
    assert "tenant_id" in android_readme
    assert "HealthKit" in ios_readme
    assert "Health Connect" in android_readme
