# ZEN70 Control Plane Phase Roadmap

- 最后更新：2026-04-08
- 适用范围：`gateway-kernel`、`runner-agent`、backend-driven control plane

## 阶段总表

| 阶段 | 目标 | 状态 | 收口标准 |
| --- | --- | --- | --- |
| `Phase 1` | 固定 kernel-first 产品形态 | 已完成 | `system.yaml -> manifest -> compose -> runtime profile -> capabilities -> menu` 一致 |
| `Phase 2` | 让调度器和运维控制台达到生产可用 | 已完成 | nodes/jobs/diagnostics/explain/lease 合同闭环 |
| `Phase 3` | 让控制台彻底 backend-driven | 已完成 | actions、policies、schema、status views 都由后端拥有 |
| `Phase 4` | 把 pack 与 kernel 彻底分层 | 已完成 | pack registry、topology、控制台展示与 IaC 共享同一套 pack 合同 |
| `Phase 5` | 为异构执行器和原生客户端留出稳定边界 | 已完成（控制面范围） | executor/resource-aware 合同、health native skeleton、vector/search 边界成立 |

## 当前固定结论

- `gateway-kernel` 是唯一正式 runtime surface。
- control plane 是 backend-driven 的管理/编排入口。
- Pack 只是能力合同与运行边界，不是新产品。
- topology 是 runtime 内第一等子域，不再单独长成产品层。
- 开发期不保留 `deploy/config-compiler.py`、`deploy/bootstrap.py` 之类兼容 wrapper。

## 后续原则

- 继续拆 `backend/core` 残余逻辑，按 `kernel / control_plane / runtime / extensions / platform` 收口。
- 任何新能力先落合同和治理，再落执行与 UI。
- 不允许再引入第二 runtime surface、第二控制面事实源或第二编译入口。
