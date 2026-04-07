# Trigger Contracts

This directory is the control-plane source of truth for the unified trigger layer.

Scope:
- Control-plane and kernel only
- No business-entity state or domain projections
- Trigger ingress, trigger target contracts, and delivery audit semantics

Ingress contracts:
- `manual`: operator/API initiated trigger, gated by `config.allow_api_fire`
- `cron`: scheduler-owned timer trigger
- `webhook`: inbound HTTP trigger via `/api/v1/triggers/webhooks/{tenant_id}/{trigger_id}`
- `event`: internal event-bus trigger

Target contracts:
- `job`: submit a published job kind through the shared job admission path
- `workflow_template`: render a published workflow template, then create a workflow instance

Hard invariants:
1. Manual API fire is only valid for `kind=manual` and `config.allow_api_fire=true`
2. Webhook ingress is only valid for `kind=webhook`
3. Trigger target contracts must resolve to published job kinds or workflow templates
4. Every fire attempt writes a `trigger_deliveries` record before downstream dispatch
5. Idempotency keys are unique per `tenant_id + trigger_id + idempotency_key`
6. SSE and Redis event payloads publish on `trigger:events`

Operational semantics:
- Inactive triggers reject fire requests with `409`
- Downstream contract/input errors return `400`
- Ingress mismatch returns `409`
- Successful dispatch stores downstream target metadata in `trigger_deliveries.target_snapshot`

Recommended flow:
1. Discover available target contracts through `/api/v1/extensions`
2. Create or update the trigger through `/api/v1/triggers`
3. Use the native ingress contract for that trigger kind
4. Inspect `/api/v1/triggers/{trigger_id}/deliveries` for audit and replay inputs
