# ZEN70 审计资产索引

本目录承载 2026-04-07 启动的全链路模块化深审资产。这里的文档只记录基于当前仓库代码重新核对后的事实，不延续旧审计结论。

当前资产：

- `execution-chain-report-2026-04-07.md`: M1 执行主链首份深审报告。
- `backend-domain-decomposition-2026-04-08.md`: backend 五域目标与拆分蓝图。
- `findings-ledger.yaml`: 结构化发现台账，使用 `open / fixed / verified` 生命周期维持当前状态。
- `asset-inventory.yaml`: 审计单元对应的一方代码与关键生成面清单。
- `module-catalog.yaml`: M1-M8 模块边界、依赖关系、优先级与下一步顺序。

当前官方边界真相以代码为准：

- `backend/kernel/governance/domain_blueprint.py`
- `backend/kernel/governance/domain_import_fence.py`
- `tools/backend_domain_fence.py`
- `backend/tests/unit/test_architecture_governance_gates.py`
- `tests/test_repo_hardening.py`

排除范围：

- `frontend/node_modules/**`
- `frontend/dist/**`
- `__pycache__/**`
- `.pytest_cache/**`
- `.mypy_cache/**`
- `.gocache/**`

其中 `contracts/openapi/**`、`docs/openapi*.json`、`docs/api/openapi_locked.json`、`placement-solver/gen/**` 仍纳入同步性审查。
