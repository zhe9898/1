# ADR Index

This directory records architecture decisions for the repository.

## Source of Truth

ADR text is not the primary runtime truth.

Repository truth is ordered as follows:

1. implementation and exported code contracts
2. tests and enforcement gates
3. ADR text and design notes

See:

- [0052](0052-code-backed-architecture-governance-registry.md) Code-Backed Architecture Governance Registry

## Current Architecture Anchors

These ADRs are the most relevant entrypoints for the current codebase shape:

- [0024](0024-gateway-kernel-default-and-backend-driven-control-plane.md) Gateway kernel default and backend-driven control plane
- [0025](0025-control-plane-node-job-contract-hardening.md) Control-plane node/job contracts
- [0027](0027-node-machine-token-authentication.md) Node machine token authentication
- [0028](0028-scheduler-attempt-history-and-operational-overview.md) Scheduler attempt history and operational overview
- [0029](0029-scheduler-capacity-drain-and-lease-governance.md) Scheduler capacity, drain, and lease governance
- [0046](0046-kernel-only-runtime-surface-and-compatibility-retirement.md) Kernel-only runtime surface and compatibility retirement
- [0049](0049-scheduling-policy-store-single-source-of-truth.md) PolicyStore as runtime policy source of truth
- [0052](0052-code-backed-architecture-governance-registry.md) Code-backed governance registry

## Historical / Deferred

- [0023](0023-v4-architecture-evolution-roadmap.md) Archived historical debt register. Do not use it as the current implementation baseline.

## Template

- [0000](0000-adr-template.md) ADR template

## File Index

- [0001](0001-implement-iac-with-python-compiler.md) implement-iac-with-python-compiler
- [0002](0002-docker-cli-via-docker-host.md) docker-cli-via-docker-host
- [0003](0003-jwt-secret-production-failfast.md) jwt-secret-production-failfast
- [0004](0004-structured-logging-json-formatter.md) structured-logging-json-formatter
- [0005](0005-topology-sentinel-redis-client.md) topology-sentinel-redis-client
- [0006](0006-decouple-gateway-from-docker-socket.md) decouple-gateway-from-docker-socket
- [0007](0007-native-family-board-via-sse.md) native-family-board-via-sse
- [0008](0008-optional-local-llm-agent.md) optional-local-llm-agent
- [0009](0009-contracts-generated-types.md) contracts-generated-types
- [0010](0010-unified-success-envelope.md) unified-success-envelope
- [0011](0011-unified-iac-core-library.md) unified-iac-core-library
- [0013](0013-redis-spof-mitigation-and-state-decoupling.md) redis-spof-mitigation-and-state-decoupling
- [0014](0014-unified-transaction-and-tenant-isolation-enforcements.md) unified-transaction-and-tenant-isolation-enforcements
- [0015](0015-unified-http-client-interceptor-enforcement.md) unified-http-client-interceptor-enforcement
- [0016](0016-sse-client-token-heartbeat-timeout.md) sse-client-token-heartbeat-timeout
- [0017](0017-audit-technical-debt-governance.md) audit-technical-debt-governance
- [0018](0018-zero-downtime-deployment.md) zero-downtime-deployment
- [0019](0019-mypy-type-ignore-comments-policy.md) mypy-type-ignore-comments-policy
- [0020](0020-auth-audit-stream.md) auth-audit-stream
- [0021](0021-frontend-api-path-constant-registry.md) frontend-api-path-constant-registry
- [0022](0022-tiered-release-smoke-gate.md) tiered-release-smoke-gate
- [0023](0023-v4-architecture-evolution-roadmap.md) v4-architecture-evolution-roadmap
- [0024](0024-gateway-kernel-default-and-backend-driven-control-plane.md) gateway-kernel-default-and-backend-driven-control-plane
- [0025](0025-control-plane-node-job-contract-hardening.md) control-plane-node-job-contract-hardening
- [0027](0027-node-machine-token-authentication.md) node-machine-token-authentication
- [0028](0028-scheduler-attempt-history-and-operational-overview.md) scheduler-attempt-history-and-operational-overview
- [0029](0029-scheduler-capacity-drain-and-lease-governance.md) scheduler-capacity-drain-and-lease-governance
- [0030](0030-backend-driven-resource-forms-and-action-dialogs.md) backend-driven-resource-forms-and-action-dialogs
- [0031](0031-dashboard-route-intents-and-filtered-operations-views.md) dashboard-route-intents-and-filtered-operations-views
- [0032](0032-backend-owned-resource-chrome-and-node-bootstrap-receipts.md) backend-owned-resource-chrome-and-node-bootstrap-receipts
- [0033](0033-backend-owned-status-semantics-and-tone-contracts.md) backend-owned-status-semantics-and-tone-contracts
- [0034](0034-server-owned-resource-list-filters.md) server-owned-resource-list-filters
- [0035](0035-pack-registry-and-kernel-pack-boundary.md) pack-registry-and-kernel-pack-boundary
- [0036](0036-kernel-iac-explicit-runtime-guards-and-windows-safe-compiler-writeback.md) kernel-iac-explicit-runtime-guards-and-windows-safe-compiler-writeback
- [0037](0037-phase5-native-client-contracts-and-resource-aware-dispatch.md) phase5-native-client-contracts-and-resource-aware-dispatch
- [0038](0038-control-plane-tenant-boundary-machine-channel-hardening-and-reproducible-offline-bundles.md) control-plane-tenant-boundary-machine-channel-hardening-and-reproducible-offline-bundles
- [0039](0039-tenant-scoped-admin-idempotency-and-reproducible-release-inputs.md) tenant-scoped-admin-idempotency-and-reproducible-release-inputs
- [0040](0040-preauth-tenant-contract-tmp-compile-secret-governance-and-immutable-workflows.md) preauth-tenant-contract-tmp-compile-secret-governance-and-immutable-workflows
- [0041](0041-rls-startup-attestation-external-acl-state-and-digest-pinned-images.md) rls-startup-attestation-external-acl-state-and-digest-pinned-images
- [0042](0042-encrypted-backup-push-contract-and-admin-role-unification.md) encrypted-backup-push-contract-and-admin-role-unification
- [0043](0043-runner-https-default-immutable-offline-release-and-locked-ci-dependencies.md) runner-https-default-immutable-offline-release-and-locked-ci-dependencies
- [0044](0044-disabled-account-auth-gates-internal-machine-tls-and-clean-release-bundles.md) disabled-account-auth-gates-internal-machine-tls-and-clean-release-bundles
- [0045](0045-http-entry-redirect-push-tenant-scope-global-settings-superadmin-and-update-branch-tracking.md) http-entry-redirect-push-tenant-scope-global-settings-superadmin-and-update-branch-tracking
- [0046](0046-kernel-only-runtime-surface-and-compatibility-retirement.md) kernel-only-runtime-surface-and-compatibility-retirement
- [0047](0047-control-plane-ui-and-runner-regression-density.md) control-plane-ui-and-runner-regression-density
- [0048](0048-health-pack-mvp-skeleton-and-pack-maturity-contract.md) health-pack-mvp-skeleton-and-pack-maturity-contract
- [0049](0049-scheduling-policy-store-single-source-of-truth.md) scheduling-policy-store-single-source-of-truth
- [0050](0050-runner-extended-job-kinds-and-accepted-kinds-dispatch.md) runner-extended-job-kinds-and-accepted-kinds-dispatch
- [0051](0051-iac-three-layer-field-resolution.md) iac-three-layer-field-resolution
- [0052](0052-code-backed-architecture-governance-registry.md) code-backed-architecture-governance-registry

## Maintenance Rules

- New ADRs must start from [0000](0000-adr-template.md).
- If code and ADR text disagree, fix the code-backed contract or downgrade the ADR status.
- If an ADR becomes historical only, mark it clearly instead of leaving it in a pseudo-active state.
