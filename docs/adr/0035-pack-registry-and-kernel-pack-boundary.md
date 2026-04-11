# ADR 0035: Pack Registry and Kernel / Pack Boundary

- Status: Accepted
- Date: 2026-04-08
- Scope: Pack registry, kernel/pack boundary, runtime topology inputs

## Context

After the kernel-first control plane stabilized, business capability domains still needed a first-class boundary. The repository now treats packs as explicit contracts rather than profile variants or bundle presets.

## Decision

### 1. Pack fact source

Pack truth is owned by the kernel pack registry:

- `backend/kernel/packs/registry.py`
- `backend/kernel/packs/presets.py`
- `backend/runtime/topology/pack_selection.py`

### 2. Runtime inputs

- `deployment.profile` and `GATEWAY_PROFILE` only express `gateway-kernel`
- `deployment.packs` and `GATEWAY_PACKS` express optional capability domains
- `render-manifest.json` must record `requested_packs` and `resolved_packs`

### 3. Pack contract

Each pack must declare:

- `key`
- `category`
- `services`
- `routers`
- `capability_keys`
- `delivery_stage`
- `deployment_boundary`
- `runtime_owner`
- `selector.required_capabilities`
- `selector.target_zone`
- `selector.target_executors`

### 4. Boundary rule

- Pack may extend capability, router, service, and runtime owner
- Pack may not become a second public runtime profile
- Pack may not bypass kernel policy, service contracts, or ownership rules

## Consequences

### Positive

- Kernel identity and business expansion boundaries are cleanly separated.
- IaC, runtime topology, control plane, and documentation share the same pack contract.
- Operators can see current pack boundaries directly in profile/settings/manifest views.

### Tradeoffs

- Build targets may still differ by pack, for example `iot-pack` mapping to `gateway-iot`, but that must stay an image-target concern only.
- New pack work must land contracts, scheduling hints, UI exposure, and tests together.
