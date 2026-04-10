# Host-First Multi-Node Migration Matrix

## Purpose

ZEN70's adopted default deployment model is `host-first`.

`render-manifest.json` is the migration source of truth and must explicitly expose:

- `deployment_model`
- `container_services_rendered`
- `infrastructure_containers_rendered`
- `optional_pack_containers_rendered`
- `host_processes_rendered`
- `migration_copy_plan`

This document defines how those fields map to real multi-node migration work.

## Copy Classes

### 宿主机进程复制

Copy host runtime artifacts and service definitions for:

- `gateway`
- `topology-sentinel`
- `control-worker`
- `routing-operator`
- `runner-agent`

This means copying host binaries, Python entrypoints, systemd units, and runtime config. It does not mean copying Docker service definitions.

### 基础设施容器复制

Copy infrastructure containers only when the target node must carry the kernel data plane and ingress support:

- `caddy`
- `postgres`
- `redis`
- `nats`

These are the default container services in the host-first kernel.

### 可选包容器复制

Copy only the selected optional pack containers. Examples:

- `docker-proxy`
- `watchdog`
- `victoriametrics`
- `grafana`
- `categraf`
- `loki`
- `promtail`
- `alertmanager`
- `vmalert`
- `mosquitto`

These are not part of the default kernel runtime and must not be treated as unconditional migration payload.

## Default Kernel Shape

- Default host processes: `gateway`, `topology-sentinel`, `control-worker`, `routing-operator`, `runner-agent`
- Default infrastructure containers: `caddy`, `postgres`, `redis`, `nats`
- Default optional pack containers: none

There is no default `sentinel` sidecar container in the adopted runtime model.

## Migration Scenarios

### 1. Add a compute or edge worker node

Copy:

- `runner-agent` as a host process

Do not copy by default:

- `postgres`
- `redis`
- `nats`
- `caddy`
- optional observability containers

### 2. Duplicate a full control-plane manager node

Copy:

- all default host processes
- all infrastructure containers

This is the only scenario where the whole default kernel footprint moves together.

### 3. Place observability or optional packs on a dedicated node

Copy:

- only the selected optional pack containers

Keep the control plane and infrastructure containers where they are unless you are intentionally building a full control-plane replica.

`docker-proxy` belongs to this class. It is an optional pack dependency, not part of the default kernel runtime story.

## Offline Bundle and Blueprint Rule

- Offline bundle validation must use the explicit host-first manifest fields.
- Release and migration docs must describe host processes and container classes separately.
- Old sidecar-oriented wording is historical only and must not be reused as current deployment guidance.
