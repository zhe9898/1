# ADR 0048: Health Pack MVP Skeleton and Pack Maturity Contract

- Status: Accepted
- Date: 2026-04-08
- Scope: Health Pack maturity staging and pack delivery metadata

## Context

Pack boundaries are now explicit, but `health-pack` needed a maturity level that matched what the repository actually ships.

## Decision

1. Every pack exposes `delivery_stage`.
2. `health-pack` is classified as `mvp-skeleton`.
3. `vector-pack` remains `contract-only`.
4. Removed packs do not remain in maturity tables as strike-through compatibility artifacts.

## Consequences

### Positive

- Pack maturity is carried by API contracts, control-plane views, and docs together.
- `health-pack` is no longer described as a placeholder when native skeleton artifacts already exist.
- Operators can tell the difference between runtime-present, mvp-skeleton, and contract-only packs.

### Tradeoffs

- Pack maturity changes now require synchronized updates across code, tests, and docs.
- Native client skeletons become part of the maintained contract surface.
