# ZEN70 审查资产索引

本目录承载 2026-04-07 启动的全链路模块化深审产物，原则上只记录基于当前仓库代码重新核对后的事实，不继承旧审计结论。

当前已落地资产：

- `execution-chain-report-2026-04-07.md`：M1 执行主链首份报告，覆盖配置入口、后端 API、核心状态机、模型、runner-agent、placement-solver、前端消费链。
- `backend-domain-decomposition-2026-04-08.md`：backend 五域拆分蓝图，固定 `kernel / control_plane / runtime / extensions / platform` 的目标归属与关键拆分动作。
- `findings-ledger.yaml`：结构化发现台账，便于后续继续跟踪、修复和验证。
- `asset-inventory.yaml`：按审查单元维护的一方代码与关键生成面清单。
- `module-catalog.yaml`：M1-M8 模块边界、优先级、依赖关系与后续建议顺序。

本批次排除项：

- `frontend/node_modules/**`
- `frontend/dist/**`
- `__pycache__/**`
- `.pytest_cache/**`
- `.mypy_cache/**`
- `.gocache/**`

其中 `contracts/openapi/**`、`docs/openapi*.json`、`docs/api/openapi_locked.json`、`placement-solver/gen/**` 仍纳入“同步性审查”。
