# ADR 0027: Node Machine Token Authentication

- Status: Accepted
- Date: 2026-03-27
- Scope: Gateway public exposure, runner enrollment, node/job machine channel

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 1. Context

Gateway Kernel now exposes a real multi-node control plane over a publicly reachable service boundary. User JWTs already protect the human console, but the execution path remained machine-anonymous:

- `POST /api/v1/nodes/register` and `POST /api/v1/nodes/heartbeat` trusted `node_id` from the body.
- `POST /api/v1/jobs/pull`, `POST /api/v1/jobs/{id}/result`, and `POST /api/v1/jobs/{id}/fail` relied on lease ownership but did not authenticate the machine channel itself.
- A caller that guessed or replayed a valid `node_id` could still reach sensitive execution endpoints.

This is unacceptable once the gateway is reachable from public networks. Human auth and machine auth must be separate contracts.

## 2. Decision

### 2.1 Per-node token, stored as hash on `nodes`

The control plane uses a single-table credential model on `backend.models.node.Node`:

- `auth_token_hash`
- `auth_token_version`
- `enrollment_status`

Plaintext node tokens are generated once by the control plane, shown once to the operator, and stored only as bcrypt hashes in the database.

### 2.2 One machine auth header

Machine traffic uses exactly one header form:

- `Authorization: Bearer <node_token>`

`X-Node-Token` is intentionally not supported, to avoid split implementations and drift across gateway, runner, and documentation.

### 2.3 Control-plane issued enrollment lifecycle

The lifecycle is:

1. Admin provisions a node record and receives a one-time token
2. Runner stores `RUNNER_NODE_ID` plus `NODE_TOKEN`
3. First successful `register` or `heartbeat` moves `enrollment_status` from `pending` to `active`
4. Active nodes may pull jobs and report results
5. Token rotation invalidates the previous credential immediately and returns the node to `pending`
6. Revoke blocks the node entirely until a new credential is provisioned

### 2.4 Machine auth gates job ownership

Job lease safety is now two-layered:

- machine channel must authenticate as the bound node
- lease callback must still match `node_id + attempt + lease_token`

Either condition failing rejects the request.

## 3. Consequences

### Positive

- Publicly exposed gateways no longer trust `node_id` as an identity primitive.
- Revocation and rotation are explicit control-plane operations instead of out-of-band database edits.
- Runner deployment becomes auditable because every node must be provisioned before it can execute work.

### Tradeoffs

- Existing runners must be re-provisioned with per-node tokens before they can reconnect.
- Local bring-up is stricter because anonymous auto-registration is no longer the default path.
- Credential lifecycle now belongs to the Node API contract and must stay synchronized with OpenAPI, runner config, and operator docs.

## 4. Follow-up constraints

Any future change that does one of the following must update this ADR or supersede it:

- reintroduces anonymous node/job machine traffic
- supports multiple machine-auth headers for the same path
- stores plaintext node tokens in the database or repository
- allows revoked or non-active nodes to lease or complete jobs
