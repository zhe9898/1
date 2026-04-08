#!/usr/bin/env python3
"""Compile system.yaml into deterministic runtime artifacts."""







from __future__ import annotations

import argparse
import difflib
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import jinja2

logger = logging.getLogger(__name__)

# Ensure `scripts.iac_core` imports resolve when running compiler.py directly.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ---------------------------------------------------------------------------
# ADR 0011: 閺嶇绺鹃柅鏄忕帆缂佺喍绔存禒?iac_core 鐎电厧鍙?# ---------------------------------------------------------------------------
from scripts.iac_core.exceptions import (  # noqa: E402
    ConfigLoadError,
    MigrationError,
    PolicyValidationError,
    SchemaValidationError,
)
from scripts.iac_core.lint import config_lint  # noqa: E402
from scripts.iac_core.loader import (  # noqa: E402
    extract_named_volumes,
    extract_networks,
    prepare_env,
    prepare_host_services,
    prepare_services,
)
from scripts.iac_core.policy import evaluate_and_enforce, load_default_policy  # noqa: E402
from scripts.iac_core.renderer import create_jinja2_env  # noqa: E402
from scripts.iac_core.migrator import migrate_and_persist  # noqa: E402
from scripts.iac_core.manifest import build_render_manifest, resolve_product_name  # noqa: E402
from scripts.iac_core.profiles import (  # noqa: E402
    is_profile_known,
    resolve_effective_pack_keys,
    normalize_profile,
    resolve_requested_pack_keys,
    resolve_gateway_image_target,
)
from scripts.iac_core.secrets import (  # noqa: E402
    generate_secrets,
)


def _project_root() -> Path:
    """Return the repository root relative to this script."""
    return Path(__file__).resolve().parent.parent


def _load_existing_acl_password(users_acl_path: Path, username: str) -> str | None:
    """Load existing ACL password for a user so compiler output stays idempotent."""
    if not users_acl_path.exists():
        return None
    try:
        for raw_line in users_acl_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            if parts[0] != "user" or parts[1] != username:
                continue
            for token in parts:
                if token.startswith(">") and len(token) > 1:
                    return token[1:]
    except OSError:
        return None
    return None


def _resolve_acl_output_path(config: dict, output_dir: Path) -> Path:
    """Resolve the ACL host path from the redis volume binding."""
    redis_cfg = (config.get("services") or {}).get("redis") or {}
    for volume_spec in redis_cfg.get("volumes", []) or []:
        if not isinstance(volume_spec, str):
            continue
        parts = volume_spec.rsplit(":", 2)
        if len(parts) < 2 or parts[-2] != "/etc/redis/users.acl":
            continue
        host_spec = parts[0].strip()
        if host_spec.startswith("${REDIS_ACL_FILE"):
            return _default_secure_acl_output_path()
        host_path = Path(host_spec)
        return host_path if host_path.is_absolute() else output_dir / host_path
    return _default_secure_acl_output_path()


def _default_secure_acl_output_path() -> Path:
    override = os.getenv("ZEN70_SECRET_STATE_DIR", "").strip()
    base_dir = Path(override) if override else Path.home() / ".zen70" / "runtime" / "secrets"
    return base_dir / "users.acl"


def _is_repo_scoped_secret_path(path: Path, repo_root: Path) -> bool:
    try:
        path.resolve().relative_to(repo_root.resolve())
        return True
    except ValueError:
        return False


def _compose_env_path(path: Path) -> str:
    return path.resolve().as_posix()


def _validate_acl_output_path(path: Path, repo_root: Path) -> None:
    if _is_repo_scoped_secret_path(path, repo_root):
        raise RuntimeError(
            f"Refusing to write Redis ACL into repository-managed path: {path}. "
            "Use an external secure state directory instead."
        )


def _resolve_dynamic_routes_file(root: Path, raw_path: str | None) -> Path | None:
    if raw_path is None or not raw_path.strip():
        return None
    candidate = Path(raw_path)
    return candidate if candidate.is_absolute() else root / candidate


def _load_dynamic_routes(routes_file: Path | None) -> list[dict]:
    dynamic_routes: list[dict] = []
    if routes_file is None or not routes_file.exists():
        return dynamic_routes
    try:
        with routes_file.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load dynamic routes from %s: %s", routes_file, exc)
        return dynamic_routes
    if isinstance(loaded, list):
        dynamic_routes = loaded
    return dynamic_routes


def _write_caddyfile_only(output_dir: Path, caddy_out: str, dry_run: bool) -> None:
    config_dir = output_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    caddy_path = config_dir / "Caddyfile"
    if dry_run:
        _dry_run_caddy_diff(caddy_path, caddy_out)
        return
    caddy_path.write_text(caddy_out, encoding="utf-8")
    logger.info("[OK] 瀹歌尙鏁撻幋?%s", caddy_path)


def _render_caddyfile(env: jinja2.Environment, templates_dir: Path, dynamic_routes: list[dict]) -> str:
    try:
        if (templates_dir / "Caddyfile.j2").exists():
            caddy_tpl = env.get_template("Caddyfile.j2")
            return caddy_tpl.render(
                dynamic_routes=dynamic_routes,
                csp_connect_src_extra=_derive_csp_connect_src_extra(),
            )
    except jinja2.TemplateError as exc:
        logger.error("Caddyfile 濡剝婢樺〒鍙夌厠婢惰精瑙? %s", exc)
        sys.exit(1)
    return ""


def _derive_csp_connect_src_extra() -> str:
    explicit = os.getenv("CSP_CONNECT_SRC_EXTRA", "").strip()
    if explicit:
        return explicit
    for env_name in ("VITE_API_BASE_URL", "API_BASE_URL"):
        raw_value = os.getenv(env_name, "").strip()
        if not raw_value or raw_value.startswith("/"):
            continue
        parsed = urlparse(raw_value)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def _render_systemd_units(
    env: jinja2.Environment,
    templates_dir: Path,
    host_services: list[dict],
    output_dir: Path,
) -> list[Path]:
    """Render systemd unit files for runtime host services into output_dir/systemd."""
    if not host_services:
        return []
    tpl_path = templates_dir / "systemd.j2"
    if not tpl_path.exists():
        logger.warning("[IaC-Host] systemd.j2 濡剝婢樻稉宥呯摠閸︻煉绱濈捄瀹犵箖 host 閺堝秴濮熷〒鍙夌厠")
        return []
    try:
        tpl = env.get_template("systemd.j2")
    except jinja2.TemplateError as exc:
        logger.error("systemd.j2 濡剝婢橀崝鐘烘祰婢惰精瑙? %s", exc)
        sys.exit(1)

    systemd_dir = output_dir / "systemd"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for svc in host_services:
        try:
            content = tpl.render(svc=svc)
        except jinja2.TemplateError as exc:
            logger.error("systemd 濞撳弶鐓嬫径杈Е [%s]: %s", svc.get("name"), exc)
            sys.exit(1)
        dest = systemd_dir / f"{svc['name']}.service"
        dest.write_text(content, encoding="utf-8")
        logger.info("[OK] 瀹歌尙鏁撻幋?%s", dest)
        written.append(dest)
    return written


def _host_services_caddy_routes(host_services: list[dict]) -> list[dict]:
    """Convert runtime host services into Caddy dynamic route entries."""

    routes: list[dict] = []
    for svc in host_services:
        caddy_path = svc.get("caddy_path")
        port = svc.get("port")
        if caddy_path and port:
            routes.append({"path": str(caddy_path), "target": f"127.0.0.1:{port}"})
    return routes



def _replace_text_artifact(tmp_path: Path, target_path: Path) -> None:
    """Replace a rendered text artifact atomically, with fsync for durability."""
    try:
        tmp_path.replace(target_path)
        return
    except PermissionError as exc:
        logger.warning(
            "[IaC-Write] Atomic replace denied for %s -> %s: %s; falling back to overwrite",
            tmp_path,
            target_path,
            exc,
        )
    except OSError as exc:
        if os.name != "nt":
            raise
        logger.warning(
            "[IaC-Write] Atomic replace failed for %s -> %s: %s; falling back to overwrite",
            tmp_path,
            target_path,
            exc,
        )

    # Fallback: non-atomic overwrite with fsync to ensure data reaches disk.
    # If write_text fails mid-way, target_path may be truncated 閳?this is
    # acceptable as the caller holds a .tmp copy and can retry.
    content = tmp_path.read_text(encoding="utf-8")
    with open(target_path, "w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass  # fsync may not be available on all filesystems (e.g. Windows FAT)
    tmp_path.unlink(missing_ok=True)


def main() -> None:
    """CLI entrypoint for compile, validate, and dry-run flows."""
    parser = argparse.ArgumentParser(
        description="Compile ZEN70 runtime inputs from system.yaml.",
    )
    parser.add_argument(
        "config",
        nargs="?",
        default="system.yaml",
        help="system.yaml 鐠侯垰绶為敍鍫㈡祲鐎靛綊銆嶉惄顔界壌閿涘绱濇妯款吇 system.yaml",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=".",
        help="鏉堟挸鍤惄顔肩秿閿涘牏娴夌€靛綊銆嶉惄顔界壌閿涘绱濇妯款吇 .",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="妫板嫯顫嶉崣妯绘纯瀹割喖绱撴担鍡曠瑝閸愭瑥鍙嗛弬鍥︽閿涘牆鐦戦惍浣藉殰閸斻劏鍔氶弫蹇ョ礆",
    )
    parser.add_argument(
        "--rotate-jwt",
        action="store_true",
        help="閹笛嗩攽 JWT 鐎靛棝鎸滄潪顔挎祮閿涙URRENT閳墾REVIOUS閿涘瞼鏁撻幋鎰煀 CURRENT",
    )
    parser.add_argument(
        "--render-target",
        choices=("all", "caddy"),
        default="all",
        help="Restrict compiler output to a specific artifact set.",
    )
    parser.add_argument(
        "--dynamic-routes-file",
        default=None,
        help="Optional dynamic route source file used by caddy-only rendering.",
    )
    args = parser.parse_args()

    # 濞夋洖鍚€ 鎼?.5閿涙氨鈥樻穱婵堝缁斿绻嶇悰灞炬閺冦儱绻旈崣顖濐潌
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    root = _project_root()
    config_path = root / args.config
    output_dir = root / args.output_dir
    templates_dir = Path(__file__).resolve().parent / "templates"
    migrate_fn = migrate_and_persist
    if args.render_target != "all":
        migrate_fn = lambda path, raw_config, dry_run=False: (raw_config, [f"skipped for render-target={args.render_target}"])

    if not config_path.exists():
        logger.error("闁板秶鐤嗘稉宥呯摠閸? %s", config_path)
        sys.exit(1)
    if not templates_dir.exists():
        logger.error("濡剝婢橀惄顔肩秿娑撳秴鐡ㄩ崷? %s", templates_dir)
        sys.exit(1)

    # 0.5 version migration (backup -> migrate -> write back system.yaml)
    try:
        import yaml as _yaml

        with open(config_path, encoding="utf-8") as _f:
            raw_config = _yaml.safe_load(_f)
        migrated_config, migration_log = migrate_fn(config_path, raw_config, dry_run=args.dry_run)
        for log_line in migration_log:
            logger.info("[migrator] %s", log_line)
    except MigrationError as e:
        logger.error("閻楀牊婀版潻浣盒╂径杈Е: %s", e)
        sys.exit(1)
    except OSError as e:
        logger.error("鏉╀胶些閺傚洣娆?I/O 婢惰精瑙? %s", e)
        sys.exit(1)

    # 1. config-lint (娑撳鐪伴弽锟犵崣) + policy engine
    try:
        lint_result = config_lint(config_path)
    except ConfigLoadError as e:
        logger.error("闁板秶鐤嗛崝鐘烘祰婢惰精瑙? %s", e)
        sys.exit(1)
    except SchemaValidationError as e:
        logger.error("Tier 1 Schema 閺嶏繝鐛欐径杈Е:")
        for err in e.errors:
            logger.error("  閴?%s", err)
        sys.exit(1)

    config = lint_result.config
    for w in lint_result.warnings:
        logger.warning("[config-lint] %s", w)
    logger.info("[config-lint] 閺嶏繝鐛欓柅姘崇箖")

    deployment_cfg = config.get("deployment") or {}
    raw_profile = deployment_cfg.get("profile", "gateway-kernel")
    if not is_profile_known(str(raw_profile)):
        logger.error(
            "Unsupported deployment.profile=%r. Supported runtime profile: gateway-kernel",
            raw_profile,
        )
        sys.exit(1)

    normalized_profile = normalize_profile(raw_profile)
    requested_pack_keys = resolve_requested_pack_keys(raw_profile, deployment_cfg.get("packs"))
    resolved_pack_keys = resolve_effective_pack_keys(raw_profile, deployment_cfg.get("packs"))
    product_name = resolve_product_name(deployment_cfg)
    deployment_cfg["profile"] = normalized_profile
    deployment_cfg["packs"] = list(requested_pack_keys)
    deployment_cfg["product"] = product_name
    config["deployment"] = deployment_cfg
    gateway_image_target = resolve_gateway_image_target(normalized_profile, selected_packs=requested_pack_keys)
    logger.info(
        "[profile] resolved=%s packs=%s gateway_target=%s product=%s",
        normalized_profile,
        ",".join(resolved_pack_keys) or "(none)",
        gateway_image_target,
        product_name,
    )

    # 1.5 缁涙牜鏆愬鏇熸惛 (Tier 2)
    policy_data = load_default_policy()
    policy_version = policy_data.get("policy_version", 0)
    policy_source = "iac/policy/core.yaml"
    # Prefer the checked-in policy file when it exists.
    _ext_policy_path = root / "iac" / "policy" / "core.yaml"
    if not _ext_policy_path.exists():
        policy_source = "(builtin-fallback)"
    try:
        policy_violations = evaluate_and_enforce(config, policy_data)
    except PolicyValidationError as e:
        logger.error("Tier 2 缁涙牜鏆愰弽锟犵崣婢惰精瑙?")
        for v in e.violations:
            logger.error("  閴?[%s] %s: %s", v.rule_id, v.service, v.message)
        sys.exit(1)

    # 2. 闁槒绶懕姘値 (iac_core.loader)
    services_list = prepare_services(config)
    host_services_list = prepare_host_services(config)
    env_vars = prepare_env(config)
    env_vars["csp_connect_src_extra"] = _derive_csp_connect_src_extra()
    # Use config mtime as the render timestamp so recompiles stay deterministic.
    try:
        config_mtime = config_path.stat().st_mtime
    except OSError:
        config_mtime = 0.0
    env_vars["now"] = datetime.fromtimestamp(
        config_mtime,
        tz=timezone.utc,
    ).strftime("%Y-%m-%d %H:%M:%S")

    # 2.5 闂冭尪鍘崙顓＄槈娑擃厼绺?(iac_core.secrets)
    dynamic_routes_file = _resolve_dynamic_routes_file(root, args.dynamic_routes_file)
    dynamic_routes = _load_dynamic_routes(dynamic_routes_file) if args.render_target == "caddy" else []
    # Merge host-service routes (127.0.0.1:port) into caddy dynamic_routes
    dynamic_routes = list(dynamic_routes) + _host_services_caddy_routes(host_services_list)
    env = create_jinja2_env(templates_dir)
    env.globals["now"] = env_vars["now"]

    caddy_out = _render_caddyfile(env, templates_dir, dynamic_routes)

    if args.render_target == "caddy":
        _write_caddyfile_only(output_dir, caddy_out, dry_run=getattr(args, "dry_run", False))
        return

    if getattr(args, "rotate_jwt", False):
        env_vars["_rotate_jwt"] = True
    env_vars = generate_secrets(output_dir, env_vars)
    users_acl_path = _resolve_acl_output_path(config, output_dir)
    _validate_acl_output_path(users_acl_path, root)
    env_vars["redis_acl_file"] = _compose_env_path(users_acl_path)

    # 3. 濞撳弶鐓?(iac_core.renderer)
    volumes_list = extract_named_volumes(config)
    networks_list = extract_networks(config)

# Build extra Caddy routes for runtime host services and caddy-only surfaces.
    dynamic_routes = _host_services_caddy_routes(host_services_list)
    env = create_jinja2_env(templates_dir)
    env.globals["now"] = env_vars["now"]

    try:
        dc_tpl = env.get_template("docker-compose.yml.j2")
        dc_out = dc_tpl.render(
            services_list=services_list,
            volumes_list=volumes_list,
            networks_list=networks_list,
            now=env_vars["now"],
        )
    except jinja2.TemplateError as e:
        logger.error("docker-compose 濡剝婢樺〒鍙夌厠婢惰精瑙? %s", e)
        sys.exit(1)

    try:
        env_tpl = env.get_template(".env.j2")
        env_out = env_tpl.render(**env_vars)
    except jinja2.TemplateError as e:
        logger.error(".env 濡剝婢樺〒鍙夌厠婢惰精瑙? %s", e)
        sys.exit(1)

    try:
        if (templates_dir / "Caddyfile.j2").exists():
            caddy_tpl = env.get_template("Caddyfile.j2")
            caddy_out = caddy_tpl.render(
                dynamic_routes=dynamic_routes,
                csp_connect_src_extra=env_vars["csp_connect_src_extra"],
            )
        else:
            caddy_out = ""
    except jinja2.TemplateError as e:
        logger.error("Caddyfile 濡剝婢樺〒鍙夌厠婢惰精瑙? %s", e)
        sys.exit(1)

    # 3.6 host 閺堝秴濮?systemd unit 閺傚洣娆㈠〒鍙夌厠
    _render_systemd_units(env, templates_dir, host_services_list, output_dir)

    # 3.9 Redis 闂嗘湹淇婃禒?ACL 缂佹挾鏅?(Phase 9)
    readonly_password = _load_existing_acl_password(users_acl_path, "readonly")
    readonly_password = readonly_password or secrets.token_urlsafe(16)
    gateway_password = _load_existing_acl_password(users_acl_path, "zen70_gateway") or env_vars["redis_password"]
    acl_lines = [
        "user default off nopass nocommands",
        f"user readonly on >{readonly_password} ~zen70:* &zen70:* +@read -@write +ping +info",
        f"user zen70_gateway on >{gateway_password} ~* &* +@all",
    ]
    acl_content = "\n".join(acl_lines) + "\n"
    config_dir = output_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    users_acl_path.parent.mkdir(parents=True, exist_ok=True)
    users_acl_path.write_text(acl_content, encoding="utf-8")
    try:
        os.chmod(users_acl_path, 0o600)
    except OSError:
        pass

    # 4. Validate-Before-Commit 妫板嫭顥呴悢鏃€鏌?(Phase 9)
    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_dc = output_dir / "docker-compose.yml.tmp"
    tmp_env = output_dir / ".env.tmp"

    tmp_dc.write_text(dc_out, encoding="utf-8")
    tmp_env.write_text(env_out, encoding="utf-8")

    logger.info("[IaC-Validate] Running docker compose config validation")
    try:
        # Prefer docker-compose when available, otherwise fall back to docker compose.
        compose_cmd: list[str] | None = None
        if shutil.which("docker-compose"):
            compose_cmd = ["docker-compose"]
            compose_cmd = ["docker", "compose"]
        else:
            logger.warning("[IaC-Validate] docker-compose/docker 閺堫亝澹橀崚甯礉鐠哄疇绻冩０鍕梾 " "(娴犲懎婀柈銊ц閼哄倻鍋ｉ崣顖滄暏閺冭埖澧界悰?")
            compose_cmd = None

        if compose_cmd is not None:
            cmd = compose_cmd + [
                "--project-name",
                "zen70",
                "-f",
                str(tmp_dc),
                "--env-file",
                str(tmp_env),
                "config",
                "-q",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if res.returncode != 0:
                logger.error(
                    "Validate-Before-Commit: 妫板嫭顥呮径杈Е閿涘本瀚嗙紒婵婎洬閻╂牜骞囬張澶婁淮鎼撮娈?IaC 闁板秶鐤?\n" "鐠囷妇绮忔穱鈩冧紖:\n%s",
                    res.stderr,
                )
                tmp_dc.unlink(missing_ok=True)
                tmp_env.unlink(missing_ok=True)
                sys.exit(1)

        # 妫板嫭顥呴柅姘崇箖閿涘牊鍨ㄧ捄瀹犵箖閿涘绱濆Λ鈧弻?dry-run 濡€崇础
        if getattr(args, "dry_run", False):
            _dry_run_diff(output_dir, dc_out, env_out, tmp_dc, tmp_env)
            sys.exit(0)

        # Persist validated compose/env artifacts only after validation succeeds.
        _replace_text_artifact(tmp_dc, output_dir / "docker-compose.yml")
        _replace_text_artifact(tmp_env, output_dir / ".env")
        env_path = output_dir / ".env"

    except (OSError, subprocess.SubprocessError) as e:
        logger.error("閺冪姵纭堕幍褑顢戞０鍕梾瀵洘鎼哥拫鍐崳: %s", e)
        tmp_dc.unlink(missing_ok=True)
        tmp_env.unlink(missing_ok=True)
        sys.exit(1)

    # 閺嬭埖鐎憰浣圭湴: Caddyfile 閸愭瑥鍙?config/
    if caddy_out:
        (config_dir / "Caddyfile").write_text(caddy_out, encoding="utf-8")

    try:
        os.chmod(env_path, 0o600)
    except OSError as chmod_exc:
        logger.warning(
            "[IaC-Security] Failed to set 0o600 on %s: %s 閳?"
            ".env may be world-readable; verify file permissions manually",
            env_path, chmod_exc,
        )

    logger.info("[OK] IaC 妫板嫭顥呴柅姘崇箖閿涘苯鍑￠崢鐔剁秴閸楀洨娣獮鍓佹晸閹?%s", output_dir / "docker-compose.yml")
    logger.info("[OK] 瀹歌尙鏁撻幋?%s (閺夊啴妾?600)", env_path)
    logger.info("[OK] 瀹歌尙鏁撻幋鎰敶闁劌濮炵€靛棛鐓╅梼?%s", users_acl_path)
    if caddy_out:
        logger.info("[OK] 瀹歌尙鏁撻幋?%s", config_dir / "Caddyfile")

    # 5. render-manifest.json 閳?濠у瓨绨拋鏉跨秿
    manifest = build_render_manifest(
        rendered_at=str(env_vars.get("now", "")),
        source=str(args.config),
        product=product_name,
        profile=normalized_profile,
        requested_packs=list(requested_pack_keys),
        resolved_packs=list(resolved_pack_keys),
        gateway_image_target=gateway_image_target,
        policy_version=int(policy_version),
        policy_file=policy_source,
        services_list=services_list + host_services_list,
        policy_violations=policy_violations,
        tier3_warnings=lint_result.warnings,
    )
    manifest_path = output_dir / "render-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("[OK] 瀹歌尙鏁撻幋鎰崓濠ф劘顔囪ぐ?%s", manifest_path)


def _dry_run_diff(
    output_dir: Path,
    dc_out: str,
    env_out: str,
    tmp_dc: Path,
    tmp_env: Path,
) -> None:
    """Emit a redacted dry-run diff without writing files."""
    _sensitive = ("PASSWORD", "SECRET", "TOKEN", "DSN")
    logger.info("=" * 60)
    logger.info("[DRY-RUN] Previewing changes without writing files")
    logger.info("=" * 60)

    # docker-compose diff
    old_dc_path = output_dir / "docker-compose.yml"
    if old_dc_path.exists():
        old_dc = old_dc_path.read_text(encoding="utf-8")
        for line in difflib.unified_diff(
            old_dc.splitlines(keepends=True),
            dc_out.splitlines(keepends=True),
            fromfile="docker-compose.yml (current)",
            tofile="docker-compose.yml (new)",
        ):
            logger.info("%s", line.rstrip())

    # .env diff (鐎靛棛鐖滈懘杈ㄦ櫛)
    old_env_path = output_dir / ".env"
    if old_env_path.exists():
        old_env = old_env_path.read_text(encoding="utf-8")
        logger.info("--- .env diff (閺佸繑鍔呯€涙顔屽鑼跺姎閺? ---")
        for line in difflib.unified_diff(
            old_env.splitlines(keepends=True),
            env_out.splitlines(keepends=True),
            fromfile=".env (current)",
            tofile=".env (new)",
        ):
            if line.startswith(("+", "-")) and any(s in line for s in _sensitive):
                key_part = line.split("=", 1)[0]
                logger.info("%s=***REDACTED***", key_part)
            else:
                logger.info("%s", line.rstrip())

    tmp_dc.unlink(missing_ok=True)
    tmp_env.unlink(missing_ok=True)
    logger.info("[DRY-RUN] Complete. No files were written")


def _dry_run_caddy_diff(caddy_path: Path, caddy_out: str) -> None:
    logger.info("=" * 60)
    logger.info("[DRY-RUN] 娴犮儰绗呮稉?Caddyfile 閸欐ɑ娲挎０鍕潔閿涘本婀崘娆忓弳娴犺缍嶉弬鍥︽")
    old_lines = caddy_path.read_text(encoding="utf-8").splitlines() if caddy_path.exists() else []
    new_lines = caddy_out.splitlines()
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=str(caddy_path),
        tofile="Caddyfile.new",
        lineterm="",
    )
    for line in diff:
        logger.info("%s", line)
    logger.info("[DRY-RUN] Caddyfile preview complete")


if __name__ == "__main__":
    main()
