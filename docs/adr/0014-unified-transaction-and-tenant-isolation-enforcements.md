# ADR 0014: Unified Transaction Management and Enhanced Tenant Isolation

- Status: Accepted
- Date: Unknown
- Scope: Unified Transaction Management and Enhanced Tenant Isolation

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## Context
During the V3.3 codebase hardening phase, multiple critical deviations from the intended architectural design were identified across the codebase:
1. **IoT IDOR Vulnerability**: The `/api/v1/iot/submit` and device control paths in `iot_bridge` bypassed mandatory tenant assertions. It blindly pulled `device_id` from payloads, creating an Insecure Direct Object Reference (IDOR) vector where any authenticated user could control external devices belonging to other tenants.
2. **"Double-Commit" Race Conditions**: Routes (e.g., `assets.py`, `push.py`, `board.py`, `settings.py`) were manually invoking `await db.commit()`. Since the central session dependency (`get_db_session` / `get_db`) automatically executes a `commit` upon successful HTTP exit, the duplicate, inner commit led to erratic connection states and potential transaction boundary regressions under high load.
3. **Type-Mismatch in Auth Contexts**: SQLAlchemy 2.0 heavily enforced strict types (e.g., `user_id: Mapped[int]`), but arbitrary claims parsing (`current_user.get("sub")` returning `str`) led to silent coercion bugs in DB inserts (e.g., in `push.py`). 

## Decision
### 1. Mandatory Pre-Flight RLS Lookup for Asynchronous/Stream Jobs
Before any API endpoint enqueues a background job or pushes to a pub-sub stream (such as Redis `XADD`), the API **must** validate ownership and retrieve the `tenant_id` linked to the target entity (e.g., `Device`) through PostgreSQL Row-Level Security (RLS). 
- The payload injected into Redis streams must contain an explicit `tenant_id` context. 
- Fire-and-forget logic that lacks context injection is strictly forbidden.

### 2. Banning Explicit `db.commit()` in Routes
- Controllers and API routes are explicitly banned from calling `await db.commit()` or `await session.commit()`.
- Routes may only request an intermediate flush using `await db.flush()` to retrieve auto-incrementing ID sequences.
- Ultimate commitment or rollback of the database transaction remains the strict, undivided responsibility of the FastAPI `Depends(get_db_session)` lifecycle context manager.

### 3. Explicit Type Casting for JWT Sub Claims
Endpoints parsing `"sub"` from the `Depends(get_current_user)` payload **must** execute explicit type conversion (e.g., `int(sub)`) and provide a safe fallback or error catch handling for non-numeric/invalid tokens *before* querying or allocating data in the database. 

## Consequences
- **Positive**: Zero possibility of "Double-Commit" or "dangling transaction" deadlocks. Radically tighter security around IoT command injection. Prevents silent SQL runtime crashes resulting from data type coercion faults.
- **Negative**: Developers must remember *not* to use `commit` and explicitly cast `str` to `int` when dealing with `user_id`, or use utility wrappers.
