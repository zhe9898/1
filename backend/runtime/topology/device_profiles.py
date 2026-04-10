"""Device Profile Registry — built-in hardware classifications for heterogeneous nodes.

Covers the full spectrum of edge/IoT device types:
  - Single-board computers  (Raspberry Pi 4/5)
  - Industrial controllers  (industrial_x86)
  - Network appliances      (soft_router, network_switch)
  - Security/surveillance   (nvr, ip_camera)
  - Storage appliances      (nas)
  - General compute         (mini_pc, cloud_vm, gateway_appliance, generic_edge)

Usage — node side
-----------------
At registration / heartbeat the backend calls ``infer_device_profile()`` when
the caller has not supplied an explicit ``device_profile`` key in their
registration metadata.  The resolved name is stored in
``metadata_json["device_profile"]`` so the frontend and scheduler can read it.

Usage — job side
----------------
A job may set ``preferred_device_profile`` to express a *soft* scheduling
preference.  The scheduler adds a bonus score when the job's preferred profile
matches the candidate node's ``metadata_json["device_profile"]``.  It is never
a hard filter — jobs still run on non-matching nodes if no match is available.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

__all__ = [
    "DeviceProfile",
    "DEVICE_PROFILE_REGISTRY",
    "get_device_profile",
    "infer_device_profile",
    "apply_profile_defaults",
]


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """Immutable descriptor for a class of hardware."""

    name: str
    display_name: str
    description: str
    # Typical OS/arch values for this device class; empty tuple = any
    typical_os: tuple[str, ...] = field(default_factory=tuple)
    typical_arch: tuple[str, ...] = field(default_factory=tuple)
    # Recommended executor when the node does not declare one explicitly
    default_executor: str = "edge-native"
    # Capabilities this device class typically exposes
    default_capabilities: tuple[str, ...] = field(default_factory=tuple)
    # Resource footprint hints (0 = no constraint / unbounded)
    min_memory_mb: int = 0
    max_memory_mb: int = 0
    min_cpu_cores: int = 1
    # Suggested scheduling zone when the node does not declare one
    suggested_zone: str | None = None
    # Recommended max_concurrency when the node reports ≤ 1
    default_max_concurrency: int = 2


# ---------------------------------------------------------------------------
# Built-in profile definitions  (more specific profiles must come *before*
# more generic ones so the inference loop can break on the first high-score
# match without needing extra tie-breaking logic)
# ---------------------------------------------------------------------------

_PROFILES: list[DeviceProfile] = [
    # ── Single-board computers ────────────────────────────────────────────
    DeviceProfile(
        name="raspberry_pi_5",
        display_name="Raspberry Pi 5",
        description="Raspberry Pi 5 (4–8 GB RAM, ARM64, higher throughput than Pi 4)",
        typical_os=("linux",),
        typical_arch=("arm64",),
        default_executor="edge-native",
        default_capabilities=("iot.adapter", "connector.invoke"),
        min_memory_mb=4096,
        max_memory_mb=8192,
        suggested_zone="home",
        default_max_concurrency=8,
    ),
    DeviceProfile(
        name="raspberry_pi_4",
        display_name="Raspberry Pi 4",
        description="Raspberry Pi 4 Model B (512 MB – 8 GB RAM, ARM/ARM64, edge-native)",
        typical_os=("linux",),
        typical_arch=("arm64", "arm"),
        default_executor="edge-native",
        default_capabilities=("iot.adapter", "connector.invoke"),
        min_memory_mb=512,
        max_memory_mb=8192,
        suggested_zone="home",
        default_max_concurrency=4,
    ),
    # ── Industrial / factory ──────────────────────────────────────────────
    DeviceProfile(
        name="industrial_x86",
        display_name="Industrial x86 Controller",
        description="Fanless industrial PC / PLC gateway (x86_64, factory floor)",
        typical_os=("linux", "windows"),
        typical_arch=("amd64", "386"),
        default_executor="docker",
        default_capabilities=("plc.read", "modbus", "connector.invoke"),
        min_memory_mb=2048,
        max_memory_mb=32768,
        suggested_zone="factory",
        default_max_concurrency=4,
    ),
    # ── Network appliances ────────────────────────────────────────────────
    DeviceProfile(
        name="soft_router",
        display_name="Soft Router",
        description="Software-defined router / firewall (OpenWrt, pfSense, RouterOS, x86/ARM)",
        typical_os=("linux",),
        typical_arch=("amd64", "arm64", "arm", "mipsle", "mips64le"),
        default_executor="process",
        default_capabilities=("net.route", "net.firewall", "net.monitor"),
        min_memory_mb=256,
        max_memory_mb=8192,
        suggested_zone="network",
        default_max_concurrency=2,
    ),
    DeviceProfile(
        name="network_switch",
        display_name="Network Switch",
        description="Managed switch with embedded agent (MIPS/x86, VLAN/SNMP capable)",
        typical_os=("linux",),
        typical_arch=("mipsle", "mips64le", "amd64"),
        default_executor="process",
        default_capabilities=("net.switch", "net.vlan", "net.monitor"),
        min_memory_mb=128,
        max_memory_mb=4096,
        suggested_zone="network",
        default_max_concurrency=2,
    ),
    # ── Security / surveillance ───────────────────────────────────────────
    DeviceProfile(
        name="nvr",
        display_name="Network Video Recorder (NVR)",
        description="Dedicated NVR appliance or DVR server for surveillance camera streams",
        typical_os=("linux",),
        typical_arch=("amd64", "arm64"),
        default_executor="docker",
        default_capabilities=("video.record", "video.stream"),
        min_memory_mb=4096,
        max_memory_mb=32768,
        suggested_zone="security",
        default_max_concurrency=4,
    ),
    DeviceProfile(
        name="ip_camera",
        display_name="IP Camera",
        description="Embedded IP camera with ONVIF/RTSP support (constrained ARM device, ≤512 MB)",
        typical_os=("linux",),
        typical_arch=("arm", "arm64"),
        default_executor="edge-native",
        default_capabilities=("video.capture", "video.stream"),
        min_memory_mb=64,
        max_memory_mb=512,
        suggested_zone="security",
        default_max_concurrency=1,
    ),
    # ── Storage appliances ────────────────────────────────────────────────
    DeviceProfile(
        name="nas",
        display_name="Network-Attached Storage (NAS)",
        description="NAS appliance with data sync and serving capabilities",
        typical_os=("linux",),
        typical_arch=("amd64", "arm64"),
        default_executor="docker",
        default_capabilities=("storage.serve", "data.sync"),
        min_memory_mb=2048,
        max_memory_mb=131072,
        suggested_zone="storage",
        default_max_concurrency=4,
    ),
    # ── General compute ───────────────────────────────────────────────────
    DeviceProfile(
        name="mini_pc",
        display_name="Mini PC",
        description="Compact x86 desktop (Intel NUC, BMAX, Beelink, etc.) 4–32 GB RAM",
        typical_os=("linux", "windows"),
        typical_arch=("amd64",),
        default_executor="docker",
        default_capabilities=("connector.invoke", "shell.exec"),
        min_memory_mb=4096,
        max_memory_mb=32768,
        suggested_zone="home",
        default_max_concurrency=8,
    ),
    DeviceProfile(
        name="cloud_vm",
        display_name="Cloud VM",
        description="Cloud virtual machine (AWS EC2, GCP Compute, Azure VM) with high bandwidth",
        typical_os=("linux",),
        typical_arch=("amd64", "arm64"),
        default_executor="docker",
        default_capabilities=("connector.invoke", "shell.exec"),
        min_memory_mb=1024,
        max_memory_mb=524288,
        suggested_zone="cloud",
        default_max_concurrency=16,
    ),
    DeviceProfile(
        name="gateway_appliance",
        display_name="Gateway Appliance",
        description="IoT / home-automation gateway hub (ZEN70 or third-party, ARM64, 1–2 GB)",
        typical_os=("linux",),
        typical_arch=("arm64",),
        default_executor="docker",
        default_capabilities=("connector.invoke", "iot.adapter"),
        min_memory_mb=1024,
        max_memory_mb=2048,
        suggested_zone="home",
        default_max_concurrency=4,
    ),
    # ── Catch-all ─────────────────────────────────────────────────────────
    DeviceProfile(
        name="generic_edge",
        display_name="Generic Edge Node",
        description="Catch-all for heterogeneous edge devices that do not match a specific profile",
        typical_os=("linux",),
        typical_arch=("amd64", "arm64", "arm"),
        default_executor="edge-native",
        default_capabilities=("connector.invoke",),
        min_memory_mb=256,
        max_memory_mb=0,
        suggested_zone=None,
        default_max_concurrency=2,
    ),
]

# Public name → profile lookup dict
DEVICE_PROFILE_REGISTRY: Final[dict[str, DeviceProfile]] = {p.name: p for p in _PROFILES}


def get_device_profile(name: str) -> DeviceProfile | None:
    """Return the named profile, or ``None`` if not found."""
    return DEVICE_PROFILE_REGISTRY.get(name)


# ---------------------------------------------------------------------------
# Inference heuristics
# ---------------------------------------------------------------------------


def infer_device_profile(
    *,
    os: str = "",
    arch: str = "",
    memory_mb: int = 0,
    executor: str = "",
    capabilities: list[str] | None = None,
) -> str:
    """Return the best-matching profile name for a node's hardware fingerprint.

    Scoring rules (higher = better match):
      +2  arch is in ``profile.typical_arch``
      +2  memory fits in a bounded range (max_memory_mb > 0) — rewards specificity
      +1  memory fits in an unbounded range (max_memory_mb == 0)
      +N  N capabilities overlap with ``profile.default_capabilities``

    Tie-breaking (lower is better, applied after score):
      memory range width (bounded profiles only) — tighter range = more specific

    Hard disqualifiers (profile is skipped entirely):
      • OS mismatch (when node OS and profile.typical_os are both non-empty)
      • memory_mb < profile.min_memory_mb (when both are > 0)

    At least one scoring dimension must be positive (score > 0) to accept a
    match; returns ``"generic_edge"`` when nothing scores.
    """
    os_norm = (os or "").lower().strip()
    arch_norm = (arch or "").lower().strip()
    caps_set = frozenset(c.lower() for c in (capabilities or []))

    best_name = "generic_edge"
    best_score = 0  # require score > 0 to replace fallback
    best_range_width = -1  # lower range width wins ties (more specific profile)

    for profile in _PROFILES:
        if profile.name == "generic_edge":
            continue  # used as explicit fallback only

        # Hard disqualifier: OS mismatch
        if os_norm and profile.typical_os and os_norm not in profile.typical_os:
            continue

        # Hard disqualifier: node is below the profile's minimum memory
        if memory_mb > 0 and profile.min_memory_mb > 0 and memory_mb < profile.min_memory_mb:
            continue

        # Hard disqualifier: node exceeds the profile's maximum memory
        if memory_mb > 0 and profile.max_memory_mb > 0 and memory_mb > profile.max_memory_mb:
            continue

        score = 0

        # Arch match
        if arch_norm and profile.typical_arch and arch_norm in profile.typical_arch:
            score += 2

        # Memory range match — bounded ranges (max > 0) score higher than unbounded ones
        # because they represent a more specific device class.
        if memory_mb > 0:
            in_range = profile.min_memory_mb <= memory_mb and (profile.max_memory_mb == 0 or memory_mb <= profile.max_memory_mb)
            if in_range:
                score += 2 if profile.max_memory_mb > 0 else 1

        # Capability overlap hint
        if profile.default_capabilities and caps_set:
            overlap = sum(1 for c in profile.default_capabilities if c in caps_set)
            score += overlap

        # Penalize specialized profiles when the node declares no capabilities at all.
        # Profiles whose entire default_capabilities set is domain-specific (no generic
        # "connector.invoke" / "shell.exec") should not win over general-purpose profiles
        # based purely on arch/memory matches when there is nothing to confirm the domain.
        if not caps_set and profile.default_capabilities:
            _generic = frozenset({"connector.invoke", "shell.exec", "noop"})
            if all(c not in _generic for c in profile.default_capabilities):
                score = max(0, score - 1)

        # Memory range width used as tiebreaker: bounded profiles with a smaller
        # range represent a more specific device class (0 = unbounded = very wide)
        range_width = (profile.max_memory_mb - profile.min_memory_mb) if profile.max_memory_mb > 0 else 999_999_999

        if score > best_score or (score == best_score and best_score > 0 and range_width < best_range_width):
            best_score = score
            best_name = profile.name
            best_range_width = range_width

    return best_name


def apply_profile_defaults(
    profile: DeviceProfile,
    *,
    executor: str,
    zone: str | None,
    max_concurrency: int,
) -> dict[str, object]:
    """Return a dict of field overrides for values that are still at generic defaults.

    Only fills in fields where the node reported an "unknown" or missing value.
    The caller decides which of the returned overrides to apply.
    """
    overrides: dict[str, object] = {}
    if executor in ("", "unknown", "go-native") and profile.default_executor:
        overrides["executor"] = profile.default_executor
    if zone is None and profile.suggested_zone:
        overrides["zone"] = profile.suggested_zone
    if max_concurrency <= 1 and profile.default_max_concurrency > 1:
        overrides["max_concurrency"] = profile.default_max_concurrency
    return overrides
