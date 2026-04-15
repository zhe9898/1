"""Structured host-runtime normalization for ``runtime: host`` services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

_ENTRYPOINT_TYPE_ALIASES = {
    "python-module": "python_module",
    "python_module": "python_module",
    "python-script": "python_script",
    "python_script": "python_script",
    "binary": "binary",
}

_BUILD_TYPE_ALIASES = {
    "go-binary": "go_binary",
    "go_binary": "go_binary",
}


def _normalize_entrypoint_type(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    return _ENTRYPOINT_TYPE_ALIASES.get(value, value)


def _normalize_build_type(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    return _BUILD_TYPE_ALIASES.get(value, value)


def _stringify_mapping(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _stringify_args(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        msg = f"host entrypoint args must be a list, got {type(raw).__name__}"
        raise ValueError(msg)
    return tuple(str(item) for item in raw)


def quote_systemd_arg(arg: str) -> str:
    """Quote a single ``ExecStart=`` argument using systemd-safe escaping."""
    text = str(arg)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if not text or any(ch.isspace() for ch in text) or any(ch in {'"', "\\"} for ch in text):
        return f'"{escaped}"'
    return escaped


def build_exec_start(argv: Sequence[str]) -> str:
    """Render a deterministic ``ExecStart=`` command line."""
    return " ".join(quote_systemd_arg(arg) for arg in argv)


def format_environment_line(key: str, value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{key}={escaped}"'


@dataclass(frozen=True)
class HostRuntimePaths:
    project_root: Path
    output_root: Path

    def resolve_project_path(self, raw: str | None, *, default: str = ".") -> Path:
        candidate = Path(raw or default).expanduser()
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        return candidate.resolve()

    def resolve_output_path(self, raw: str | None, *, default: str | None = None) -> Path:
        source = raw if raw not in (None, "") else default
        if not source:
            msg = "output-relative path is required"
            raise ValueError(msg)
        candidate = Path(source).expanduser()
        if not candidate.is_absolute():
            candidate = self.output_root / candidate
        return candidate.resolve()


@dataclass(frozen=True)
class HostBuildPlan:
    kind: str
    source_dir: Path
    package: str
    output_path: Path
    env: dict[str, str]
    trimpath: bool
    ldflags: str

    def to_runtime_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source_dir": str(self.source_dir),
            "package": self.package,
            "output_path": str(self.output_path),
            "env": dict(self.env),
            "trimpath": self.trimpath,
            "ldflags": self.ldflags,
        }


@dataclass(frozen=True)
class HostServiceSpec:
    name: str
    description: str
    exec_start: str
    working_dir: str
    environment_file: str
    environment_lines: tuple[str, ...]
    user: str
    group: str
    port: int | None
    caddy_path: str
    after: str
    restart: str
    restart_sec: int
    build_plan: HostBuildPlan | None

    def to_render_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "runtime": "host",
            "description": self.description,
            "exec_start": self.exec_start,
            "working_dir": self.working_dir,
            "environment_file": self.environment_file,
            "environment_lines": list(self.environment_lines),
            "user": self.user,
            "group": self.group,
            "port": self.port,
            "caddy_path": self.caddy_path,
            "after": self.after,
            "restart": self.restart,
            "restart_sec": self.restart_sec,
            "build_plan": self.build_plan.to_runtime_dict() if self.build_plan else None,
        }


def resolve_host_runtime_paths(
    config: Mapping[str, Any],
    *,
    output_root: Path | None = None,
) -> HostRuntimePaths:
    project_root = Path(
        str(
            config.get("__project_root__")
            or config.get("_project_root")
            or config.get("project_root")
            or Path.cwd()
        )
    ).expanduser()
    if not project_root.is_absolute():
        project_root = project_root.resolve()

    resolved_output_root = output_root or Path(
        str(
            config.get("__output_root__")
            or config.get("_output_root")
            or config.get("output_root")
            or project_root
        )
    ).expanduser()
    if not resolved_output_root.is_absolute():
        resolved_output_root = (project_root / resolved_output_root).resolve()

    return HostRuntimePaths(
        project_root=project_root.resolve(),
        output_root=resolved_output_root.resolve(),
    )


def validate_host_service_config(name: str, svc: Mapping[str, Any]) -> list[str]:
    """Validate declarative host-runtime service configuration."""
    errors: list[str] = []
    entrypoint = svc.get("entrypoint")
    if entrypoint is None:
        errors.append(f"services.{name}: runtime=host requires entrypoint")
        return errors
    if not isinstance(entrypoint, Mapping):
        return [f"services.{name}.entrypoint must be a mapping"]

    entrypoint_type = _normalize_entrypoint_type(entrypoint.get("type"))
    if entrypoint_type not in {"python_module", "python_script", "binary"}:
        errors.append(
            f"services.{name}.entrypoint.type must be one of "
            "python-module, python-script, binary"
        )
        return errors

    args = entrypoint.get("args")
    if args is not None and not isinstance(args, list):
        errors.append(f"services.{name}.entrypoint.args must be a list")

    if entrypoint_type == "python_module" and not str(entrypoint.get("module") or "").strip():
        errors.append(f"services.{name}.entrypoint.module is required for python-module")
    if entrypoint_type == "python_script" and not str(entrypoint.get("script") or "").strip():
        errors.append(f"services.{name}.entrypoint.script is required for python-script")
    if entrypoint_type == "binary" and not str(entrypoint.get("path") or "").strip():
        errors.append(f"services.{name}.entrypoint.path is required for binary")

    build_cfg = entrypoint.get("build")
    if build_cfg is None:
        return errors
    if not isinstance(build_cfg, Mapping):
        errors.append(f"services.{name}.entrypoint.build must be a mapping")
        return errors
    if entrypoint_type != "binary":
        errors.append(f"services.{name}.entrypoint.build is only supported for binary entrypoints")
        return errors

    build_type = _normalize_build_type(build_cfg.get("type"))
    if build_type != "go_binary":
        errors.append(f"services.{name}.entrypoint.build.type must be go-binary")
        return errors

    if not str(build_cfg.get("source_dir") or "").strip():
        errors.append(f"services.{name}.entrypoint.build.source_dir is required")
    if not str(build_cfg.get("package") or "").strip():
        errors.append(f"services.{name}.entrypoint.build.package is required")
    if not str(build_cfg.get("output") or entrypoint.get("path") or "").strip():
        errors.append(f"services.{name}.entrypoint.build.output is required")

    env_cfg = build_cfg.get("env")
    if env_cfg is not None and not isinstance(env_cfg, Mapping):
        errors.append(f"services.{name}.entrypoint.build.env must be a mapping")

    return errors


def build_host_service_spec(
    name: str,
    svc: Mapping[str, Any],
    *,
    paths: HostRuntimePaths,
) -> HostServiceSpec:
    entrypoint = svc.get("entrypoint")
    if not isinstance(entrypoint, Mapping):
        msg = f"services.{name}: runtime=host requires a structured entrypoint mapping"
        raise ValueError(msg)

    command = _build_entrypoint_command(entrypoint, paths=paths)
    working_dir = paths.resolve_project_path(str(svc.get("working_dir") or "."))
    environment_file = paths.resolve_output_path(str(svc.get("environment_file") or ".env"), default=".env")
    environment = _stringify_mapping(svc.get("environment"))
    build_plan = _build_plan(entrypoint, paths=paths)
    after = _normalize_after_units(svc.get("after"))

    return HostServiceSpec(
        name=name,
        description=str(svc.get("description") or f"{name} (ZEN70 host process)"),
        exec_start=build_exec_start(command),
        working_dir=str(working_dir),
        environment_file=str(environment_file),
        environment_lines=tuple(format_environment_line(key, value) for key, value in environment.items()),
        user=str(svc.get("user") or ""),
        group=str(svc.get("group") or ""),
        port=int(svc["port"]) if svc.get("port") is not None else None,
        caddy_path=str(svc.get("caddy_path") or ""),
        after=after,
        restart=str(svc.get("restart") or "on-failure"),
        restart_sec=int(svc.get("restart_sec", 5)),
        build_plan=build_plan,
    )


def _normalize_after_units(raw: Any) -> str:
    if isinstance(raw, list):
        return " ".join(str(item).strip() for item in raw if str(item).strip()) or "network.target"
    return str(raw or "network.target")


def _build_entrypoint_command(
    entrypoint: Mapping[str, Any],
    *,
    paths: HostRuntimePaths,
) -> tuple[str, ...]:
    entrypoint_type = _normalize_entrypoint_type(entrypoint.get("type"))
    args = _stringify_args(entrypoint.get("args"))

    if entrypoint_type == "python_module":
        python_bin = str(entrypoint.get("python") or "python3").strip() or "python3"
        module = str(entrypoint.get("module") or "").strip()
        return ("/usr/bin/env", python_bin, "-m", module, *args)

    if entrypoint_type == "python_script":
        python_bin = str(entrypoint.get("python") or "python3").strip() or "python3"
        script_path = paths.resolve_project_path(str(entrypoint.get("script") or ""))
        return ("/usr/bin/env", python_bin, str(script_path), *args)

    if entrypoint_type == "binary":
        path = paths.resolve_output_path(str(entrypoint.get("path") or ""))
        return (str(path), *args)

    msg = f"unsupported host entrypoint type: {entrypoint_type}"
    raise ValueError(msg)


def _build_plan(
    entrypoint: Mapping[str, Any],
    *,
    paths: HostRuntimePaths,
) -> HostBuildPlan | None:
    build_cfg = entrypoint.get("build")
    if not isinstance(build_cfg, Mapping):
        return None

    build_type = _normalize_build_type(build_cfg.get("type"))
    if build_type != "go_binary":
        msg = f"unsupported host build type: {build_type}"
        raise ValueError(msg)

    source_dir = paths.resolve_project_path(str(build_cfg.get("source_dir") or ""))
    output_path = paths.resolve_output_path(
        str(build_cfg.get("output") or entrypoint.get("path") or ""),
    )
    return HostBuildPlan(
        kind=build_type,
        source_dir=source_dir,
        package=str(build_cfg.get("package") or "").strip(),
        output_path=output_path,
        env=_stringify_mapping(build_cfg.get("env")),
        trimpath=bool(build_cfg.get("trimpath", True)),
        ldflags=str(build_cfg.get("ldflags") or "-s -w").strip() or "-s -w",
    )
