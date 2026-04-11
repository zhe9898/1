from __future__ import annotations

from dataclasses import dataclass

from backend.kernel.policy.policy_store import PolicyStore, get_policy_store
from backend.kernel.profiles.public_profile import normalize_gateway_profile
from backend.runtime.topology.profile_selection import get_enabled_router_names, resolve_runtime_pack_keys


@dataclass(frozen=True, slots=True)
class RuntimePolicySnapshot:
    profile: str
    enabled_routers: tuple[str, ...]
    active_packs: tuple[str, ...]
    policy_version: int


class RuntimePolicyResolver:
    """Single runtime policy entrypoint for control-plane gating."""

    def __init__(self, policy_store: PolicyStore | None = None) -> None:
        self._policy_store = policy_store or get_policy_store()

    @property
    def policy_store(self) -> PolicyStore:
        return self._policy_store

    def snapshot(
        self,
        *,
        profile: str,
        raw_packs: str | None = None,
        enabled_router_names: tuple[str, ...] | None = None,
    ) -> RuntimePolicySnapshot:
        normalized_profile = normalize_gateway_profile(profile)
        routers = tuple(enabled_router_names or get_enabled_router_names(normalized_profile))
        active_packs = tuple(sorted(resolve_runtime_pack_keys(profile=profile, raw_packs=raw_packs or "")))
        return RuntimePolicySnapshot(
            profile=normalized_profile,
            enabled_routers=routers,
            active_packs=active_packs,
            policy_version=self._policy_store.version,
        )

    def router_enabled(
        self,
        router_name: str,
        *,
        profile: str,
        enabled_router_names: tuple[str, ...] | None = None,
    ) -> bool:
        snapshot = self.snapshot(profile=profile, enabled_router_names=enabled_router_names)
        return router_name in snapshot.enabled_routers


_resolver: RuntimePolicyResolver | None = None


def get_runtime_policy_resolver() -> RuntimePolicyResolver:
    global _resolver
    if _resolver is None:
        _resolver = RuntimePolicyResolver()
    return _resolver


def export_runtime_policy_contract() -> dict[str, object]:
    return {
        "entrypoint": "backend.kernel.policy.runtime_policy_resolver.RuntimePolicyResolver",
        "policy_store_entrypoint": "backend.kernel.policy.policy_store.get_policy_store",
        "profile_normalizer": "backend.kernel.profiles.public_profile.normalize_gateway_profile",
        "runtime_pack_resolver": "backend.runtime.topology.profile_selection.resolve_runtime_pack_keys",
        "router_gate_method": "router_enabled",
        "snapshot_method": "snapshot",
    }
