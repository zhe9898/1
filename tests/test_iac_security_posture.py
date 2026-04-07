from __future__ import annotations

from tests._repo_paths import repo_path


def test_iac_security_posture_is_explicit_in_system_yaml() -> None:
    system_yaml = repo_path("system.yaml").read_text(encoding="utf-8")
    for keyword in ("read_only", "cap_drop", "oom_score_adj", "healthcheck"):
        assert keyword in system_yaml, f"system.yaml 必须显式提示 IaC 安全姿态: {keyword}"
