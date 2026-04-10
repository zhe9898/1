from __future__ import annotations

import datetime
import logging
import os

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.deps import get_current_user, get_db, get_redis
from backend.control_plane.adapters.deps import get_settings as get_runtime_settings
from backend.control_plane.auth.access_policy import require_superadmin_role
from backend.kernel.contracts.errors import zen
from backend.kernel.packs.registry import available_pack_definitions
from backend.kernel.policy.feature_flag_service import FeatureFlagService
from backend.kernel.profiles.public_profile import DEFAULT_PRODUCT_NAME, normalize_gateway_profile, to_public_profile
from backend.models.feature_flag import DEFAULT_CONFIGS, DEFAULT_FLAGS, FeatureFlag, SystemConfig
from backend.platform.redis.client import RedisClient
from backend.runtime.topology.profile_selection import (
    normalize_gateway_pack_keys,
    resolve_runtime_pack_keys,
)

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])
logger = logging.getLogger("zen70.settings")


class SettingsSchemaField(BaseModel):
    key: str
    label: str
    value: str | bool | None = None
    description: str | None = None
    input: str = "text"
    editable: bool = False
    save_path: str | None = None
    placeholder: str | None = None


class SettingsSchemaSection(BaseModel):
    id: str
    label: str
    description: str | None = None
    fields: list[SettingsSchemaField] = Field(default_factory=list)


class SettingsSchemaResponse(BaseModel):
    product: str
    profile: str
    runtime_profile: str
    sections: list[SettingsSchemaSection] = Field(default_factory=list)


def _require_db(db: AsyncSession | None) -> AsyncSession:
    if db is None:
        raise zen(
            "ZEN-DB-5030",
            "Database unavailable",
            status_code=503,
            recovery_hint="Ensure the gateway database is configured and retry",
        )
    return db


async def _ensure_defaults(db: AsyncSession | None, *, write_defaults: bool = True) -> AsyncSession:
    session = _require_db(db)
    if not write_defaults:
        return session

    existing_flags = await session.execute(select(FeatureFlag.key))
    existing_flag_keys = {row[0] for row in existing_flags.all()}
    for flag in DEFAULT_FLAGS:
        if flag.key not in existing_flag_keys:
            session.add(
                FeatureFlag(
                    key=flag.key,
                    enabled=flag.enabled,
                    description=flag.description,
                    category=flag.category,
                )
            )

    existing_configs = await session.execute(select(SystemConfig.key))
    existing_config_keys = {row[0] for row in existing_configs.all()}
    for config in DEFAULT_CONFIGS:
        if config.key not in existing_config_keys:
            session.add(
                SystemConfig(
                    key=config.key,
                    value=config.value,
                    description=config.description,
                )
            )

    await session.flush()
    return session


def _schema_field(
    *,
    key: str,
    label: str,
    value: str | bool | None,
    description: str,
    input_type: str = "text",
    editable: bool = False,
    save_path: str | None = None,
    placeholder: str | None = None,
) -> SettingsSchemaField:
    return SettingsSchemaField(
        key=key,
        label=label,
        value=value,
        description=description,
        input=input_type,
        editable=editable,
        save_path=save_path,
        placeholder=placeholder,
    )


@router.get("/flags")
async def list_flags(
    db: AsyncSession | None = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict[str, object]:
    require_superadmin_role(current_user)
    session = await _ensure_defaults(db, write_defaults=False)
    result = await session.execute(select(FeatureFlag).order_by(FeatureFlag.category, FeatureFlag.key))
    flags = result.scalars().all()

    data: list[dict[str, object]] = []
    seen_keys: set[str] = set()
    for flag in flags:
        seen_keys.add(flag.key)
        data.append(
            {
                "key": flag.key,
                "enabled": flag.enabled,
                "description": flag.description,
                "category": flag.category,
                "updated_at": flag.updated_at.isoformat() if flag.updated_at else None,
            }
        )
    for default in DEFAULT_FLAGS:
        if default.key in seen_keys:
            continue
        data.append(
            {
                "key": default.key,
                "enabled": default.enabled,
                "description": default.description,
                "category": default.category,
                "updated_at": None,
            }
        )
    data.sort(key=lambda item: (str(item.get("category") or ""), str(item.get("key") or "")))
    return {"status": "ok", "count": len(data), "data": data}


@router.put("/flags/{key}")
async def toggle_flag(
    key: str,
    db: AsyncSession | None = Depends(get_db),
    redis: RedisClient | None = Depends(get_redis),
    current_user: dict = Depends(get_current_user),
) -> dict[str, object]:
    require_superadmin_role(current_user)
    session = await _ensure_defaults(db)

    result = await session.execute(select(FeatureFlag).where(FeatureFlag.key == key))
    flag = result.scalars().first()
    if flag is None:
        raise zen(
            "ZEN-CFG-4041",
            "Unknown feature flag",
            status_code=404,
            recovery_hint="Refresh the settings page and retry",
            details={"key": key},
        )

    updated_by = str(current_user.get("username") or current_user.get("sub") or "superadmin")
    updated_flag = await FeatureFlagService.toggle_flag(session, key=key, updated_by=updated_by)
    new_state = bool(updated_flag.enabled)

    if redis is not None:
        try:
            await redis.kv.setex(f"zen70:ff:{key}", 300, "1" if new_state else "0")
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            logger.debug("feature flag cache write failed: %s", exc)

    return {
        "status": "ok",
        "key": key,
        "enabled": new_state,
        "message": f"{key} set to {'enabled' if new_state else 'disabled'}",
    }


@router.get("/config")
async def list_config(
    db: AsyncSession | None = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict[str, object]:
    require_superadmin_role(current_user)
    session = await _ensure_defaults(db, write_defaults=False)
    result = await session.execute(select(SystemConfig))
    configs = result.scalars().all()
    merged: dict[str, dict[str, str | None]] = {config.key: {"value": config.value, "description": config.description} for config in configs}
    for default in DEFAULT_CONFIGS:
        merged.setdefault(default.key, {"value": default.value, "description": default.description})
    return {
        "status": "ok",
        "data": merged,
    }


@router.put("/config/{key}")
async def update_config(
    key: str,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict[str, object]:
    require_superadmin_role(current_user)
    body = await request.json()
    if "value" not in body:
        raise zen(
            "ZEN-VAL-4000",
            "Missing value field",
            status_code=400,
            recovery_hint='Send {"value": "..."}',
            details={"key": key},
        )

    session = await _ensure_defaults(db)
    result = await session.execute(select(SystemConfig).where(SystemConfig.key == key))
    config = result.scalars().first()
    if config is None:
        raise zen(
            "ZEN-CFG-4040",
            "Unknown config key",
            status_code=404,
            recovery_hint="Refresh the settings page and retry",
            details={"key": key},
        )

    await session.execute(update(SystemConfig).where(SystemConfig.key == key).values(value=str(body["value"]), updated_at=datetime.datetime.now(datetime.UTC)))
    await session.flush()
    return {"status": "ok", "key": key, "value": str(body["value"]), "message": f"{key} updated"}


@router.get("/schema", response_model=SettingsSchemaResponse)
async def get_settings_schema(
    db: AsyncSession | None = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> SettingsSchemaResponse:
    require_superadmin_role(current_user)
    session = await _ensure_defaults(db, write_defaults=False)
    result = await session.execute(select(SystemConfig))
    configs = {item.key: item.value for item in result.scalars().all()}
    for default in DEFAULT_CONFIGS:
        configs.setdefault(default.key, default.value)
    raw_profile = os.getenv("GATEWAY_PROFILE", "gateway-kernel")
    raw_packs = os.getenv("GATEWAY_PACKS", "")
    runtime_profile = normalize_gateway_profile(raw_profile)
    requested_pack_keys = normalize_gateway_pack_keys(raw_packs, profile=raw_profile)
    resolved_pack_keys = resolve_runtime_pack_keys(profile=raw_profile, raw_packs=raw_packs)
    runtime_settings = get_runtime_settings()
    runtime_cors_origins = runtime_settings.get("cors_origins")
    cors_origins = runtime_cors_origins if isinstance(runtime_cors_origins, list) else []
    available_pack_labels = ", ".join(definition.label for definition in available_pack_definitions())

    def config_field(key: str, label: str, description: str, placeholder: str | None = None) -> SettingsSchemaField:
        return _schema_field(
            key=key,
            label=label,
            value=configs.get(key, ""),
            description=description,
            editable=True,
            save_path=f"/v1/settings/config/{key}",
            placeholder=placeholder,
        )

    sections = [
        SettingsSchemaSection(
            id="profile",
            label="Profile",
            description="Kernel runtime identity resolved from IaC and backend profile.",
            fields=[
                _schema_field(
                    key="product",
                    label="Product",
                    value=DEFAULT_PRODUCT_NAME,
                    description="Frozen default product line.",
                    input_type="readonly",
                ),
                _schema_field(
                    key="profile",
                    label="Public Profile",
                    value=to_public_profile(runtime_profile),
                    description="Profile exposed to the console.",
                    input_type="readonly",
                ),
                _schema_field(
                    key="runtime_profile",
                    label="Runtime Profile",
                    value=runtime_profile,
                    description="Backend runtime profile used for router and pack gating.",
                    input_type="readonly",
                ),
                _schema_field(
                    key="requested_packs",
                    label="Selected Packs",
                    value=", ".join(requested_pack_keys) or "none",
                    description="Pack requests resolved from deployment.packs.",
                    input_type="readonly",
                ),
                _schema_field(
                    key="resolved_packs",
                    label="Effective Packs",
                    value=", ".join(resolved_pack_keys) or "none",
                    description="Expanded pack set used by routing, IaC service gating, and pack-specific selectors.",
                    input_type="readonly",
                ),
                _schema_field(
                    key="available_packs",
                    label="Available Packs",
                    value=available_pack_labels,
                    description="Optional packs kept outside the default kernel ingress boundary.",
                    input_type="readonly",
                ),
            ],
        ),
        SettingsSchemaSection(
            id="network",
            label="Network",
            description="Editable non-secret network and path settings stored in SystemConfig.",
            fields=[
                config_field("backend_port", "Backend API Port", "FastAPI service port.", "8000"),
                config_field("frontend_port", "Frontend Port", "Frontend dev or preview port.", "5173"),
                config_field("caddy_http_port", "Caddy HTTP Port", "Public HTTP ingress port.", "80"),
                config_field("caddy_https_port", "Caddy HTTPS Port", "Public HTTPS ingress port.", "443"),
                config_field("caddy_domain", "Caddy Domain", "Reverse proxy domain name.", "home.example.com"),
                config_field("cf_tunnel_domain", "Cloudflare Tunnel Domain", "Optional tunnel DNS name.", "zen70.example.com"),
                config_field("headscale_domain", "Headscale Domain", "Optional private network domain.", "hc.internal"),
                config_field("media_path", "Media Path", "Shared storage root path.", "/mnt/media"),
            ],
        ),
        SettingsSchemaSection(
            id="connectors",
            label="Connectors",
            description="Runner and connector dispatch defaults exposed from backend runtime.",
            fields=[
                _schema_field(
                    key="runner_profile",
                    label="Runner Profile",
                    value=os.getenv("RUNNER_PROFILE", "go-runner"),
                    description="Default Go Runner profile.",
                    input_type="readonly",
                ),
                _schema_field(
                    key="runner_capabilities",
                    label="Runner Capabilities",
                    value=os.getenv("RUNNER_CAPABILITIES", "connector.invoke"),
                    description="Kinds accepted by the default Go Runner.",
                    input_type="readonly",
                ),
                _schema_field(
                    key="runner_pull_seconds",
                    label="Runner Pull Interval",
                    value=os.getenv("RUNNER_PULL_SECONDS", "5"),
                    description="Current runtime poll interval in seconds.",
                    input_type="readonly",
                ),
                _schema_field(
                    key="runner_heartbeat_seconds",
                    label="Runner Heartbeat Interval",
                    value=os.getenv("RUNNER_HEARTBEAT_SECONDS", "15"),
                    description="Current runtime heartbeat interval in seconds.",
                    input_type="readonly",
                ),
                _schema_field(
                    key="connector_default_lease_seconds",
                    label="Default Connector Lease",
                    value=os.getenv("RUNNER_LEASE_SECONDS", "30"),
                    description="Current runtime lease for connector jobs.",
                    input_type="readonly",
                ),
            ],
        ),
        SettingsSchemaSection(
            id="security",
            label="Auth / Security",
            description="Backend authority for access control and auth posture.",
            fields=[
                _schema_field(
                    key="settings_access",
                    label="Settings Access",
                    value="superadmin-only",
                    description="Settings mutations require superadmin role.",
                    input_type="readonly",
                ),
                _schema_field(
                    key="cors_origins",
                    label="CORS Origins",
                    value=", ".join(str(item) for item in cors_origins),
                    description="Allowed frontend origins.",
                    input_type="readonly",
                ),
                _schema_field(
                    key="jwt_rotation",
                    label="JWT Rotation",
                    value="dual-track" if os.getenv("JWT_SECRET_PREVIOUS") else "single-key",
                    description="JWT secret rotation mode is env-managed.",
                    input_type="readonly",
                ),
                _schema_field(
                    key="auth_stack",
                    label="Auth Stack",
                    value="password + webauthn",
                    description="Default kernel auth posture.",
                    input_type="readonly",
                ),
            ],
        ),
    ]

    return SettingsSchemaResponse(
        product=DEFAULT_PRODUCT_NAME,
        profile=to_public_profile(runtime_profile),
        runtime_profile=runtime_profile,
        sections=sections,
    )


def get_runtime_version() -> str:
    """Read VERSION from pyproject.toml or fallback."""
    try:
        import tomllib

        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        return str(data.get("project", {}).get("version", "unknown"))
    except Exception:
        return "unknown"


def get_model_registry() -> object:
    """Lazy import to avoid circular dependency."""
    from backend.ai_router import model_registry  # type: ignore[attr-defined]

    return model_registry


@router.get("/system-info")
async def system_info(
    db: AsyncSession | None = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict[str, object]:
    require_superadmin_role(current_user)
    version = get_runtime_version()
    return {"status": "ok", "version": version}
