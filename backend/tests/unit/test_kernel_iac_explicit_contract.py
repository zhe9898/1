from __future__ import annotations

from backend.platform.events.channels import export_event_channel_contract
from backend.platform.redis.runtime_state import export_runtime_state_contract
from backend.tests.unit._repo_paths import repo_path
from scripts.iac_core.lint import config_lint
from scripts.iac_core.policy import evaluate_policy, load_default_policy


def test_default_kernel_iac_declares_runtime_guards_explicitly() -> None:
    lint_result = config_lint(repo_path("system.yaml"))

    assert lint_result.warnings == []

    policy = load_default_policy()
    violations = evaluate_policy(lint_result.config, policy)

    assert violations == []


def test_kernel_iac_runtime_contract_matches_code_backed_event_and_runtime_state_exports() -> None:
    lint_result = config_lint(repo_path("system.yaml"))
    runtime_contracts = lint_result.config.get("runtime_contracts")
    expected_contract = {
        **export_event_channel_contract(),
        **export_runtime_state_contract(),
    }

    assert runtime_contracts == expected_contract
