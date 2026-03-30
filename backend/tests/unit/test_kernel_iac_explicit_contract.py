from __future__ import annotations

from pathlib import Path

from scripts.iac_core.lint import config_lint
from scripts.iac_core.policy import evaluate_policy, load_default_policy


def test_default_kernel_iac_declares_runtime_guards_explicitly() -> None:
    lint_result = config_lint(Path("system.yaml"))

    assert lint_result.warnings == []

    policy = load_default_policy()
    violations = evaluate_policy(lint_result.config, policy)

    assert violations == []
