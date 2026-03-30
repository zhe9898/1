# Multi-Language Layered Architecture

## Final language map

- Python: Gateway Core, compiler, installer, optional AI/Media/IoT plugins
- Go: Runner Agent, future Device Agent, sidecar and lightweight access proxies
- TypeScript: frontend control console
- Swift: iOS health connector client via HealthKit
- Kotlin: Android health connector client via Health Connect and vendor adapters
- YAML: single source of truth for system configuration

## Gateway profiles

- `gateway-core`: default profile; exposes routes, auth, settings, nodes, jobs, connectors
- `gateway-iot`: core plus optional IoT dependencies and optional feature routers
- `gateway-full`: core plus AI, Media, IoT dependencies and optional feature routers

## Python boundary

- Python remains the gateway core and the build/install toolchain
- AI, IoT, and Media move out of the default runtime and become optional plugin layers
- Cluster overview remains in `backend/api/cluster.py`, while active node/job/connector control-plane protocols live in dedicated APIs

## Health client boundary

- iOS uses Swift with HealthKit
- Android uses Kotlin with Health Connect or vendor-specific adapters
- The Python gateway does not read on-device health libraries directly; health data must arrive through connector clients
