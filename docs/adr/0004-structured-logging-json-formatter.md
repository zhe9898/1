# ADR 0004: 结构化日志采用自实现 JsonFormatter

- Status: Accepted
- Date: 2025-03-14
- Scope: 结构化日志采用自实现 JsonFormatter

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 决策

当前采用 **自实现 JsonFormatter**（集中于 `backend/core/structured_logging.py`），暂不引入 structlog。

## 理由

1. **满足规范**：JsonFormatter 已输出单行 JSON，含 timestamp、level、caller、message、X-Request-ID，可被 Loki 解析。
2. **依赖最少**：无需新增依赖，降低复杂度。
3. **统一集中**：redis_client、sentinel、gateway 共用同一模块，避免重复。
4. **后续可演进**：若需要更丰富的上下文、采样、输出格式，再评估引入 structlog。

## 后果

- 保持 `structured_logging.py` 作为唯一日志配置入口。
- 若未来引入 structlog，需通过新 ADR 变更本决策。
