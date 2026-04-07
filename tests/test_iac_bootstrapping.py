import pytest
import subprocess
import tarfile
import zipfile
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPORT_SCRIPT = PROJECT_ROOT / "scripts" / "export_seed.py"
OUTPUT_DIR = PROJECT_ROOT / "tests" / "output_seed_test"


@pytest.fixture(scope="session", autouse=True)
def setup_teardown():
    # Setup
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    yield
    # Teardown
    import shutil

    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)


def test_compiler_rejects_legacy_version():
    """Legacy config versions must fail schema validation under strict IaC policy."""
    compiler_script = PROJECT_ROOT / "scripts" / "compiler.py"

    import tempfile

    # Read the real system.yaml to ensure compiler validation passes
    original_yaml_path = PROJECT_ROOT / "system.yaml"
    yaml_content = original_yaml_path.read_text(encoding="utf-8")

    # Inject a legacy version
    import re

    yaml_content = re.sub(r'version:\s*["\']?[\d\.]+["\']?', 'version: "1.0"', yaml_content)

    temp_yaml = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8")
    temp_yaml.write(yaml_content)
    temp_yaml_path = temp_yaml.name
    temp_yaml.close()

    try:
        # Run compiler on the temporary legacy system.yaml
        env = {**__import__("os").environ, "PYTHONUTF8": "1"}
        result = subprocess.run(
            ["python", str(compiler_script), temp_yaml_path, "-o", "."],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=60,
        )

        assert result.returncode != 0, f"Expected non-zero exit for legacy version.\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        combined = f"{result.stdout}\n{result.stderr}"
        assert "低于最低要求 2.0" in combined or "Schema" in combined, (
            "Expected schema failure for legacy version 1.0.\n" f"STDOUT: {result.stdout!r}\nSTDERR: {result.stderr!r}"
        )
    finally:
        import os

        os.unlink(temp_yaml_path)


def test_export_seed_execution():
    """Test the offline seed export script (Rule 8.1.5)"""
    import sys

    sys.path.append(str(PROJECT_ROOT / "scripts"))
    import export_seed
    from unittest.mock import patch

    # We mock export_docker_images inside export_seed so it doesn't actually call `docker save`
    with patch("export_seed.export_docker_images") as mock_export:
        mock_export.return_value = True  # Pretend it succeeds

        # Run the pack_offline_seed function directly
        zip_path = export_seed.pack_offline_seed(PROJECT_ROOT, OUTPUT_DIR)

        zips = list(OUTPUT_DIR.glob("*.zip"))
        assert len(zips) >= 1, "Should generate exactly one zip file"

        # Verify contents of Zip
        with zipfile.ZipFile(zip_path, "r") as z:
            namelist = z.namelist()
            assert any(n.endswith("system.yaml") for n in namelist), "system.yaml missing from seed"
            assert any(n.endswith("docker-compose.yml") for n in namelist), "docker-compose.yml missing from seed"
            assert any(n.endswith("bootstrap.py") for n in namelist), "bootstrap.py missing from seed"


def test_adr_existence():
    """Verify ADR implementation (Rule 8.1.4)"""
    adr_dir = PROJECT_ROOT / "docs" / "adr"
    assert adr_dir.exists()
    assert (adr_dir / "0000-adr-template.md").exists()
    assert (adr_dir / "0001-implement-iac-with-python-compiler.md").exists()
