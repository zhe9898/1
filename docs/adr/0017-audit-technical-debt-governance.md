# ADR 0017: Audit-Driven Technical Debt Governance

- Status: Accepted
- Date: 2026-04-08
- Scope: Technical debt governance, audit prioritization, regression prevention

## Context

Deep audits continue to surface a mix of architecture, security, testing, and maintainability debt. The repository needs a stable rule for how these findings are ranked and carried forward.

## Decision

1. Findings are ranked by production impact first, implementation effort second.
2. P1 items block drift in architecture, security, or data correctness.
3. P2 items target maintainability and operability gaps.
4. P3 items target tooling, CI, and future-proofing improvements.
5. Audit follow-up must be reflected in tests, governance docs, or both.

## Current Governance Notes

- SSE, lease, scheduling, and control-plane protocol changes need direct regression tests.
- Large modules and duplicate runtime paths are treated as structural debt, not style issues.
- Bootstrap and compiler flows are high-risk zones and must stay on the canonical `scripts/*` path.

## Consequences

- Debt handling becomes traceable instead of conversational.
- Follow-up work is easier to sequence by risk.
- Regression prevention becomes part of the remediation requirement, not an optional extra.
