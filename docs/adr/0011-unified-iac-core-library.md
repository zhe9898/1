# ADR 0011: Unified IaC Core and Canonical Compiler Entrypoint

- Status: Accepted
- Date: 2026-04-08
- Scope: IaC compiler core, compiler entrypoint, template source of truth

## Context

The repository previously had drift between `scripts/compiler.py` and deploy-side compiler logic. That split created risk around config loading, migration, linting, secret handling, and rendered artifacts.

## Decision

ZEN70 now keeps one canonical IaC pipeline:

- `system.yaml` is the only formal configuration root
- `scripts/compiler.py` is the only supported compiler entrypoint
- `scripts/iac_core/` is the shared compiler core
- `scripts/templates/` is the only template source of truth

The `deploy/` directory may keep offline packaging and registry scripts, but it may not carry a second compiler implementation or a forwarding wrapper.

## Consequences

### Positive

- All IaC rendering flows through one typed load / merge / migrate / lint / secrets / render pipeline
- Release artifacts are easier to audit because there is one compiler path
- Offline packaging can consume canonical artifacts instead of maintaining a shadow compiler

### Tradeoffs

- Any deploy script that previously relied on wrapper paths must be updated immediately
- Compiler changes now require stronger discipline because there is no backup path
