from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, Iterator
from textwrap import dedent

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - pyproject requires 3.11+
    tomllib = None  # type: ignore[assignment]


EXCLUDED_DIR_NAMES = {
    ".sandbox",
    ".sandbox-bin",
    ".sandbox-secrets",
    ".tmp",
    "tmp",
}

LIVE_SQLITE_SUFFIXES = (
    ".sqlite-wal",
    ".sqlite-shm",
    "-wal",
    "-shm",
)

SENSITIVE_NOTE = (
    "This backup can contain Codex history, provider configuration, login state, "
    "local hooks, and other sensitive environment data. Keep it offline unless "
    "you have explicitly reviewed and approved another storage location."
)


def _make_sensitive_note(display_name: str) -> str:
    return (
        f"This backup can contain {display_name} history, provider configuration, "
        "login state, local hooks, and other sensitive environment data. Keep it "
        "offline unless you have explicitly reviewed and approved another storage location."
    )


@dataclass(frozen=True)
class EnvironmentProfile:
    name: str
    display_name: str
    default_home_dir: str
    env_home_var: str | None
    backup_prefix: str
    pre_restore_prefix: str
    default_backup_subdir: str
    important_paths: tuple[str, ...]
    config_file: str | None
    config_inspector: Callable[[Path], dict[str, Any]] | None
    commands: tuple[tuple[str, ...], ...]
    integration_module: str | None
    extra_excluded_dirs: tuple[str, ...] = ()


class BackupError(RuntimeError):
    """Raised when a backup or restore cannot complete safely."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def local_timestamp(prefix: str = "codex-backup") -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def resolve_home(
    profile: EnvironmentProfile | None = None,
    home_override: str | os.PathLike[str] | None = None,
) -> Path:
    if profile is None:
        profile = CODEX_PROFILE
    if home_override:
        return Path(home_override).expanduser().resolve()
    if profile.env_home_var is not None:
        env_home = os.environ.get(profile.env_home_var)
        if env_home:
            return Path(env_home).expanduser().resolve()
    return (Path.home() / profile.default_home_dir).resolve()


def resolve_codex_home(codex_home: str | os.PathLike[str] | None = None) -> Path:
    return resolve_home(CODEX_PROFILE, codex_home)


def default_backup_root(profile: EnvironmentProfile | None = None) -> Path:
    if profile is None:
        profile = CODEX_PROFILE
    return (Path.home() / "Documents" / profile.default_backup_subdir).resolve()


def is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def normalize_relative(path: Path) -> str:
    return path.as_posix()


def is_excluded(
    relative_path: Path,
    extra_excluded_dirs: frozenset[str] = frozenset(),
) -> bool:
    excluded = EXCLUDED_DIR_NAMES | extra_excluded_dirs
    parts = [part.lower() for part in relative_path.parts if part not in ("", ".")]
    if any(part in excluded for part in parts):
        return True
    name = relative_path.name.lower()
    return name.endswith(LIVE_SQLITE_SUFFIXES)


def is_sqlite_database(path: Path) -> bool:
    return path.suffix.lower() == ".sqlite"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def redact_text(text: str) -> str:
    patterns = [
        (r"sk-[A-Za-z0-9_\-]{12,}", "sk-<redacted>"),
        (r"(api[_-]?key\s*[:=]\s*)[^\s,;}]+", r"\1<redacted>"),
        (r"(authorization\s*[:=]\s*bearer\s+)[^\s,;}]+", r"\1<redacted>"),
        (r"(\"access_token\"\s*:\s*\")[^\"]+(\")", r"\1<redacted>\2"),
        (r"(\"refresh_token\"\s*:\s*\")[^\"]+(\")", r"\1<redacted>\2"),
    ]
    redacted = text
    for pattern, replacement in patterns:
        redacted = re.sub(pattern, replacement, redacted, flags=re.IGNORECASE)
    return redacted


def run_command(
    command: list[str],
    timeout: int = 20,
    env: dict[str, str] | None = None,
    *,
    include_output: bool = True,
    json_summary: bool = False,
) -> dict[str, Any]:
    path = env.get("PATH") if env is not None else None
    if shutil.which(command[0], path=path) is None and Path(command[0]).name == command[0]:
        return {
            "command": command,
            "status": "skipped",
            "reason": "command_not_found",
        }
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "status": "timeout",
            "timeout_seconds": timeout,
            "stdout": redact_text(exc.stdout or ""),
            "stderr": redact_text(exc.stderr or ""),
        }
    except OSError as exc:
        return {
            "command": command,
            "status": "error",
            "error": str(exc),
        }
    result: dict[str, Any] = {
        "command": command,
        "status": "ok" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
    }
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if json_summary:
        result["stdout_summary"] = summarize_json_output(stdout)
        result["output_redacted"] = not include_output
    if include_output:
        result["stdout"] = redact_text(stdout)
        result["stderr"] = redact_text(stderr)
    else:
        result["stdout_bytes"] = len(stdout.encode("utf-8"))
        result["stderr_bytes"] = len(stderr.encode("utf-8"))
    return result


def summarize_json_output(text: str) -> dict[str, Any]:
    if not text:
        return {"parse_status": "empty"}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"parse_status": "failed", "stdout_present": True}
    if not isinstance(data, dict):
        return {"parse_status": "ok", "json_type": type(data).__name__}

    summary: dict[str, Any] = {"parse_status": "ok"}
    for key in (
        "ok",
        "status",
        "installed",
        "running",
        "healthy",
        "runtime_matches",
        "needs_restart",
        "pending_restart",
        "config_matches",
        "startup_hook",
    ):
        if key in data and isinstance(data[key], (bool, str, int, float, type(None))):
            summary[key] = data[key]

    for key in (
        "provider",
        "base_url",
        "upstream_base",
        "config_base_url",
        "log",
        "stdout",
        "stderr",
    ):
        if key in data:
            summary[f"{key}_present"] = data[key] is not None

    health = data.get("health")
    if isinstance(health, dict):
        summary["health"] = {
            "ok": health.get("ok"),
            "service_tier_present": health.get("service_tier") is not None,
            "upstream_base_present": health.get("upstream_base") is not None,
            "runtime_id_present": health.get("runtime_id") is not None,
        }

    checks = data.get("checks")
    if isinstance(checks, list):
        safe_checks = []
        for check in checks:
            if isinstance(check, dict):
                safe_checks.append(
                    {
                        "name": str(check.get("name", "")),
                        "ok": check.get("ok"),
                    }
                )
        summary["checks"] = safe_checks
    return summary


def summarize_command_results(commands: dict[str, dict[str, Any]], *, run: bool) -> dict[str, Any]:
    failed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    if not run:
        return {
            "run": False,
            "ok": True,
            "total": 0,
            "failed": failed,
            "skipped": [{"name": "external_commands", "reason": "disabled"}],
        }

    for name, result in sorted(commands.items()):
        status = result.get("status", "unknown")
        if status == "skipped":
            skipped.append(
                {
                    "name": name,
                    "reason": result.get("reason", "skipped"),
                }
            )
            continue
        if status != "ok":
            failed.append(
                {
                    "name": name,
                    "status": status,
                    "returncode": result.get("returncode"),
                    "reason": result.get("reason") or result.get("error"),
                }
            )
    return {
        "run": True,
        "ok": not failed,
        "total": len(commands),
        "failed": failed,
        "skipped": skipped,
    }


def inspect_codex_config(codex_home: Path) -> dict[str, Any]:
    config_path = codex_home / "config.toml"
    result: dict[str, Any] = {
        "path": "config.toml",
        "present": config_path.exists(),
    }
    if not config_path.exists():
        return result
    result["bytes"] = config_path.stat().st_size
    if tomllib is None:
        result["parse_status"] = "skipped"
        result["reason"] = "tomllib_unavailable"
        return result
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:  # tomllib exposes several parse errors.
        result["parse_status"] = "failed"
        result["error"] = str(exc)
        return result

    providers = data.get("model_providers")
    active_provider = data.get("model_provider")
    provider_summary: dict[str, Any] = {}
    if isinstance(providers, dict):
        for name, provider in providers.items():
            if isinstance(provider, dict):
                provider_summary[str(name)] = {
                    "base_url_present": "base_url" in provider,
                    "env_key_present": "env_key" in provider,
                    "service_tier_present": "service_tier" in provider,
                    "wire_api_present": "wire_api" in provider,
                }
    result.update(
        {
            "parse_status": "ok",
            "model_provider_present": active_provider is not None,
            "model_provider": str(active_provider) if active_provider is not None else None,
            "model_providers_present": isinstance(providers, dict),
            "model_provider_count": len(providers) if isinstance(providers, dict) else 0,
            "provider_fields": provider_summary,
            "hooks_enabled_present": isinstance(data.get("features"), dict)
            and "codex_hooks" in data.get("features", {}),
        }
    )
    return result


inspect_config = inspect_codex_config


def inspect_claude_code_config(home: Path) -> dict[str, Any]:
    config_path = home / "settings.json"
    result: dict[str, Any] = {
        "path": "settings.json",
        "present": config_path.exists(),
    }
    if not config_path.exists():
        return result
    result["bytes"] = config_path.stat().st_size
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        result["parse_status"] = "failed"
        result["error"] = str(exc)
        return result
    if not isinstance(data, dict):
        result["parse_status"] = "failed"
        result["error"] = "root is not an object"
        return result
    permissions = data.get("permissions")
    hooks = data.get("hooks")
    result.update({
        "parse_status": "ok",
        "permissions_present": isinstance(permissions, dict),
        "permissions_count": len(permissions.get("allow", [])) if isinstance(permissions, dict) else 0,
        "env_present": isinstance(data.get("env"), dict),
        "hooks_present": isinstance(hooks, dict),
        "hooks_count": sum(len(v) for v in hooks.values() if isinstance(v, list)) if isinstance(hooks, dict) else 0,
        "model": data.get("model"),
        "theme": data.get("theme"),
        "allowed_tools_present": isinstance(data.get("allowedTools"), list),
    })
    return result


CODEX_PROFILE = EnvironmentProfile(
    name="codex",
    display_name="Codex",
    default_home_dir=".codex",
    env_home_var="CODEX_HOME",
    backup_prefix="codex-backup",
    pre_restore_prefix="pre-restore-codex-backup",
    default_backup_subdir="CodexBackups",
    important_paths=(
        "auth.json", "hooks.json", "history.jsonl", "sessions",
        "archived_sessions", "memories", "skills", "plugins",
        "rules", "automations", "codex-fast-proxy-state",
    ),
    config_file="config.toml",
    config_inspector=inspect_codex_config,
    commands=(("codex", "--version"), ("codex", "mcp", "list")),
    integration_module="codex_fast_proxy",
)

CLAUDE_CODE_PROFILE = EnvironmentProfile(
    name="claude-code",
    display_name="Claude Code",
    default_home_dir=".claude",
    env_home_var=None,
    backup_prefix="claude-code-backup",
    pre_restore_prefix="pre-restore-claude-code-backup",
    default_backup_subdir="ClaudeCodeBackups",
    important_paths=(
        "settings.json", "settings.local.json", "credentials.json",
        "statsig", "projects", "memory", "todos", "plugins",
        "keybindings.json",
    ),
    config_file="settings.json",
    config_inspector=inspect_claude_code_config,
    commands=(("claude", "--version"), ("claude", "mcp", "list")),
    integration_module=None,
    extra_excluded_dirs=("cache",),
)

PROFILES: dict[str, EnvironmentProfile] = {
    "codex": CODEX_PROFILE,
    "claude-code": CLAUDE_CODE_PROFILE,
}


def count_tree(
    path: Path,
    extra_excluded_dirs: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if not path.exists():
        return {"present": False, "files": 0, "dirs": 0}
    files = 0
    dirs = 0
    errors: list[dict[str, str]] = []

    def onerror(exc: OSError) -> None:
        errors.append(walk_error_entry(path, exc, method="walk"))

    for root, dir_names, file_names in os.walk(path, onerror=onerror):
        rel_root = Path(root).relative_to(path)
        dir_names[:] = [
            name for name in dir_names
            if not is_excluded(rel_root / name, extra_excluded_dirs)
        ]
        dirs += len(dir_names)
        files += sum(
            1 for name in file_names
            if not is_excluded(rel_root / name, extra_excluded_dirs)
        )
    return {"present": True, "files": files, "dirs": dirs, "errors": errors}


def _command_key(cmd: tuple[str, ...]) -> str:
    """Derive a dict key from a command tuple, e.g. ("codex", "--version") -> "codex_version"."""
    return "_".join(part.lstrip("-") for part in cmd).replace("-", "_")


def doctor_environment(
    home_override: str | os.PathLike[str] | None = None,
    *,
    profile: EnvironmentProfile | None = None,
    run_commands: bool = True,
) -> dict[str, Any]:
    if profile is None:
        profile = CODEX_PROFILE
    home = resolve_home(profile, home_override)
    core_ok = home.exists() and home.is_dir()
    config = profile.config_inspector(home) if profile.config_inspector is not None else {}
    report: dict[str, Any] = {
        "ok": core_ok,
        "core_ok": core_ok,
        "created_at": utc_now_iso(),
        "home": str(home),
        "profile": profile.name,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "sensitive_note": _make_sensitive_note(profile.display_name),
        "paths": {},
        "config": config,
        "commands": {},
    }

    for rel in profile.important_paths:
        target = home / rel
        if target.is_dir():
            report["paths"][rel] = count_tree(target)
        else:
            report["paths"][rel] = {
                "present": target.exists(),
                "bytes": target.stat().st_size if target.exists() else 0,
            }

    path_scan_errors = [
        {"path": name, **error}
        for name, info in report["paths"].items()
        for error in info.get("errors", [])
    ]
    report["path_scan_ok"] = not path_scan_errors
    report["path_scan_errors"] = path_scan_errors

    if run_commands:
        command_env = os.environ.copy()
        if profile.env_home_var is not None:
            command_env[profile.env_home_var] = str(home)
        for cmd in profile.commands:
            report["commands"][_command_key(cmd)] = run_command(
                list(cmd), env=command_env
            )
        if profile.integration_module is not None:
            module_name = profile.integration_module
            if importlib.util.find_spec(module_name) is not None:
                module_key = module_name.replace("-", "_")
                report["commands"][f"{module_key}_status"] = run_command(
                    [sys.executable, "-m", module_name, "status"],
                    env=command_env,
                    include_output=False,
                    json_summary=True,
                )
                report["commands"][f"{module_key}_doctor"] = run_command(
                    [sys.executable, "-m", module_name, "doctor"],
                    env=command_env,
                    include_output=False,
                    json_summary=True,
                )
            else:
                report["commands"][module_name.replace("-", "_")] = {
                    "status": "skipped",
                    "reason": "module_not_available",
                }
    command_summary = summarize_command_results(report["commands"], run=run_commands)
    report["command_summary"] = command_summary
    report["command_ok"] = command_summary["ok"]
    report["checks"] = {
        "core": report["core_ok"],
        "paths": report["path_scan_ok"],
        "commands": report["command_ok"],
    }
    report["ok"] = all(report["checks"].values())
    return report


def doctor_codex_environment(
    codex_home: str | os.PathLike[str] | None = None,
    *,
    run_commands: bool = True,
) -> dict[str, Any]:
    return doctor_environment(codex_home, profile=CODEX_PROFILE, run_commands=run_commands)


def error_relative_path(base: Path, raw_path: object) -> str:
    if not raw_path:
        return "."
    path = Path(str(raw_path))
    try:
        return normalize_relative(path.resolve(strict=False).relative_to(base.resolve(strict=False)))
    except (OSError, ValueError):
        return str(path)


def walk_error_entry(base: Path, exc: OSError, *, method: str) -> dict[str, str]:
    return {
        "relative_path": error_relative_path(base, getattr(exc, "filename", None)),
        "method": method,
        "error": str(exc),
    }


def iter_source_files(
    home: Path,
    errors: list[dict[str, str]] | None = None,
    extra_excluded_dirs: frozenset[str] = frozenset(),
) -> Iterator[tuple[Path, Path]]:
    def onerror(exc: OSError) -> None:
        entry = walk_error_entry(home, exc, method="walk")
        if errors is not None:
            errors.append(entry)
            return
        raise BackupError(entry["error"])

    for root, dir_names, file_names in os.walk(home, onerror=onerror):
        root_path = Path(root)
        rel_root = root_path.relative_to(home)
        dir_names[:] = [
            name for name in dir_names
            if not is_excluded(rel_root / name, extra_excluded_dirs)
        ]
        for file_name in file_names:
            source = root_path / file_name
            relative = source.relative_to(home)
            if is_excluded(relative, extra_excluded_dirs):
                continue
            yield source, relative


def backup_sqlite_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    uri = source.resolve().as_uri() + "?mode=ro"
    source_conn = sqlite3.connect(uri, uri=True, timeout=30.0)
    try:
        dest_conn = sqlite3.connect(str(destination), timeout=30.0)
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()


def sqlite_integrity_check(database: Path) -> dict[str, Any]:
    try:
        conn = sqlite3.connect(str(database), timeout=30.0)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
    except Exception as exc:
        return {"path": str(database), "ok": False, "error": str(exc)}
    value = row[0] if row else None
    return {"path": str(database), "ok": value == "ok", "result": value}


def write_environment_snapshot(
    path: Path,
    doctor_report: dict[str, Any],
    display_name: str = "Codex",
) -> None:
    lines = [
        f"{display_name} environment snapshot",
        f"Created: {doctor_report['created_at']}",
        f"{display_name} home: {doctor_report['home']}",
        f"Platform: {doctor_report['platform']['system']} {doctor_report['platform']['release']} {doctor_report['platform']['machine']}",
        f"Python: {doctor_report['platform']['python']}",
        f"Core ok: {doctor_report.get('core_ok')}",
        f"Path scan ok: {doctor_report.get('path_scan_ok')}",
        f"Command ok: {doctor_report.get('command_ok')}",
        "",
        _make_sensitive_note(display_name),
        "",
        "Important paths:",
    ]
    for name, info in doctor_report["paths"].items():
        lines.append(f"- {name}: present={info.get('present')}")
    config = doctor_report.get("config", {})
    lines.extend(
        [
            "",
            "Config:",
            f"- present={config.get('present')}",
            f"- parse_status={config.get('parse_status')}",
            f"- model_provider_present={config.get('model_provider_present')}",
            f"- model_provider_count={config.get('model_provider_count')}",
            "",
            "Secrets, auth payloads, and conversation contents are intentionally not printed here.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def restore_kit_instructions(display_name: str = "Codex") -> str:
    return dedent(
        f"""
        {display_name} environment restore kit

        This folder contains a restore helper for people who do not want to type CLI commands.

        Windows:
          1. Close the {display_name} app that uses this environment.
          2. Double-click restore-environment.cmd.
          3. Confirm the prompt.

        macOS:
          1. Close the {display_name} app that uses this environment.
          2. Double-click restore-environment.command.
          3. Confirm the prompt.

        Linux:
          1. Close the {display_name} app that uses this environment.
          2. Run restore-environment.sh from a terminal.
          3. Confirm the prompt.

        The restore helper creates a pre-restore backup before it overwrites the environment home directory.
        """
    ).strip() + "\n"


def restore_kit_markdown(display_name: str = "Codex") -> str:
    return dedent(
        f"""
        # {display_name} Environment Restore

        This backup includes a restore kit for users who do not want to type CLI commands.

        ## Before Restoring

        - Close the {display_name} app that uses this environment.
        - Keep this backup local unless you have reviewed its contents.
        - The restore helper creates a pre-restore backup before overwriting the environment home directory.

        ## Windows

        1. Extract the backup archive if needed.
        2. Double-click `restore-environment.cmd`.
        3. Type `RESTORE` when prompted.

        ## macOS

        1. Extract the backup archive if needed.
        2. Open `restore-environment.command`.
        3. Type `RESTORE` when prompted.

        ## Linux

        1. Extract the backup archive if needed.
        2. Run `restore-environment.sh`.
        3. Type `RESTORE` when prompted.

        ## Files

        - `RESTORE.md`: Markdown restore guide.
        - `RESTORE_INSTRUCTIONS.txt`: Plain-text restore guide.
        - `restore-environment.cmd`: Windows double-click restore entry.
        - `restore-environment.ps1`: Windows PowerShell restore script.
        - `restore-environment.command`: macOS restore entry.
        - `restore-environment.sh`: Linux restore entry.
        - `restore-standalone.py`: Self-contained restore implementation.
        """
    ).strip() + "\n"


RESTORE_STANDALONE_PY = dedent(
    r"""
    #!/usr/bin/env python3
    from __future__ import annotations

    import argparse
    import hashlib
    import json
    import os
    import shutil
    import sqlite3
    import sys
    import tarfile
    import zipfile
    from datetime import datetime
    from pathlib import Path
    from textwrap import dedent

    EXCLUDED_DIR_NAMES = {".sandbox", ".sandbox-bin", ".sandbox-secrets", ".tmp", "tmp"}
    LIVE_SQLITE_SUFFIXES = (".sqlite-wal", ".sqlite-shm", "-wal", "-shm")
    PROFILE_HOME_DEFAULTS = {
        "codex": ".codex",
        "claude-code": ".claude",
    }

    def resolve_target_home(target_home: str | None = None, profile: str | None = None) -> Path:
        if target_home:
            return Path(target_home).expanduser().resolve()
        if profile == "codex":
            env_home = os.environ.get("CODEX_HOME")
            if env_home:
                return Path(env_home).expanduser().resolve()
        default_dir = PROFILE_HOME_DEFAULTS.get(profile or "codex", ".codex")
        return (Path.home() / default_dir).resolve()

    def default_backup_root(profile: str | None = None) -> Path:
        subdir = "ClaudeCodeBackups" if profile == "claude-code" else "CodexBackups"
        return (Path.home() / "Documents" / subdir).resolve()

    def is_excluded(relative_path: Path) -> bool:
        parts = [part.lower() for part in relative_path.parts if part not in ("", ".")]
        if any(part in EXCLUDED_DIR_NAMES for part in parts):
            return True
        return relative_path.name.lower().endswith(LIVE_SQLITE_SUFFIXES)

    def sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest().upper()

    def write_json(path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def error_relative_path(base: Path, raw_path) -> str:
        if not raw_path:
            return "."
        path = Path(str(raw_path))
        try:
            return path.resolve(strict=False).relative_to(base.resolve(strict=False)).as_posix()
        except (OSError, ValueError):
            return str(path)

    def walk_error_entry(base: Path, exc: OSError, method: str) -> dict:
        return {
            "relative_path": error_relative_path(base, getattr(exc, "filename", None)),
            "method": method,
            "error": str(exc),
        }

    def iter_source_files(source_root: Path, errors: list):
        def onerror(exc: OSError) -> None:
            errors.append(walk_error_entry(source_root, exc, "walk"))

        for root, dir_names, file_names in os.walk(source_root, onerror=onerror):
            root_path = Path(root)
            rel_root = root_path.relative_to(source_root)
            dir_names[:] = [name for name in dir_names if not is_excluded(rel_root / name)]
            for file_name in file_names:
                source = root_path / file_name
                relative = source.relative_to(source_root)
                if is_excluded(relative):
                    continue
                yield source, relative

    def backup_sqlite_database(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        uri = source.resolve().as_uri() + "?mode=ro"
        source_conn = sqlite3.connect(uri, uri=True, timeout=30.0)
        try:
            dest_conn = sqlite3.connect(str(destination), timeout=30.0)
            try:
                source_conn.backup(dest_conn)
            finally:
                dest_conn.close()
        finally:
            source_conn.close()

    def create_backup(source_home: Path, backup_root: Path, prefix: str) -> dict:
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_dir = backup_root / f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        suffix = 1
        while backup_dir.exists():
            backup_dir = backup_root / f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{suffix}"
            suffix += 1
        files_dir = backup_dir / "files"
        files_dir.mkdir(parents=True)
        entries = []
        sqlite_checks = []
        errors = []
        for source, relative in iter_source_files(source_home, errors):
            destination = files_dir / relative
            if source.suffix.lower() == ".sqlite":
                backup_sqlite_database(source, destination)
                conn = sqlite3.connect(str(destination), timeout=30.0)
                try:
                    row = conn.execute("PRAGMA integrity_check").fetchone()
                finally:
                    conn.close()
                sqlite_checks.append({"path": str(destination), "ok": (row[0] if row else None) == "ok"})
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination, follow_symlinks=False)
            entries.append({"relative_path": relative.as_posix(), "sha256": sha256_file(destination)})
        write_json(backup_dir / "manifest.json", {
            "schema_version": 1,
            "created_at": datetime.now().isoformat(),
            "codex_home": str(source_home),
            "backup_name": backup_dir.name,
            "counts": {
                "files": len(entries),
                "sqlite_databases": sum(1 for item in entries if item["relative_path"].lower().endswith(".sqlite")),
                "errors": len(errors),
            },
            "errors": errors,
            "entries": entries,
        })
        write_json(backup_dir / "sqlite-integrity-check.json", sqlite_checks)
        (backup_dir / "backup-summary.txt").write_text(
            "Pre-restore backup created by restore helper.\n", encoding="utf-8"
        )
        write_restore_kit(backup_dir)
        archive_path = backup_dir.with_name(f"{backup_dir.name}.tar.gz")
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(backup_dir, arcname=backup_dir.name)
        sha = sha256_file(archive_path)
        (archive_path.with_name(f"{archive_path.name}.sha256")).write_text(
            f"{sha}  {archive_path.name}\n", encoding="utf-8"
        )
        return {
            "ok": not errors and all(check.get("ok") for check in sqlite_checks),
            "backup_dir": str(backup_dir),
            "archive": str(archive_path),
            "archive_sha256": sha,
            "errors": errors,
        }

    def restore_kit_markdown() -> str:
        return dedent('''
        # Environment Restore

        This backup includes a restore kit for users who do not want to type CLI commands.

        ## Before Restoring

        - Close the app that uses this environment.
        - Keep this backup local unless you have reviewed its contents.
        - The restore helper creates a pre-restore backup before overwriting the environment home directory.

        ## Windows

        1. Extract the backup archive if needed.
        2. Double-click `restore-environment.cmd`.
        3. Type `RESTORE` when prompted.

        ## macOS

        1. Extract the backup archive if needed.
        2. Open `restore-environment.command`.
        3. Type `RESTORE` when prompted.

        ## Linux

        1. Extract the backup archive if needed.
        2. Run `restore-environment.sh`.
        3. Type `RESTORE` when prompted.
        ''').strip() + "\n"

    def restore_kit_instructions() -> str:
        return dedent('''
        Environment restore kit

        This folder contains a restore helper for people who do not want to type CLI commands.

        Windows:
          1. Close the app that uses this environment.
          2. Double-click restore-environment.cmd.
          3. Confirm the prompt.

        macOS:
          1. Close the app that uses this environment.
          2. Double-click restore-environment.command.
          3. Confirm the prompt.

        Linux:
          1. Close the app that uses this environment.
          2. Run restore-environment.sh from a terminal.
          3. Confirm the prompt.
        ''').strip() + "\n"

    def write_restore_kit(backup_dir: Path) -> None:
        script_source = Path(__file__).read_text(encoding="utf-8")
        (backup_dir / "RESTORE.md").write_text(restore_kit_markdown(), encoding="utf-8")
        (backup_dir / "RESTORE_INSTRUCTIONS.txt").write_text(restore_kit_instructions(), encoding="utf-8")
        (backup_dir / "restore-standalone.py").write_text(script_source, encoding="utf-8")
        (backup_dir / "restore-environment.ps1").write_text(dedent('''
        $ErrorActionPreference = 'Stop'
        $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
        Write-Host 'Close the app that uses this environment before restoring.'
        $answer = Read-Host 'Type RESTORE to apply this backup'
        if ($answer -ne 'RESTORE') { exit 1 }
        $pythonCmd = $null
        foreach ($candidate in @('python3', 'python')) {
            if (-not (Get-Command $candidate -ErrorAction SilentlyContinue)) {
                continue
            }
            try {
                $versionText = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
                if ($LASTEXITCODE -ne 0) {
                    continue
                }
                $version = [version]($versionText | Select-Object -First 1)
                if ($version -ge [version]'3.11') {
                    $pythonCmd = $candidate
                    break
                }
            } catch {
                continue
            }
        }
        if (-not $pythonCmd) { throw 'Python 3.11+ is required to restore this backup.' }
        & $pythonCmd "$scriptRoot\\restore-standalone.py" --backup-dir $scriptRoot --apply --confirm
        ''').strip() + "\n", encoding="utf-8")
        (backup_dir / "restore-environment.cmd").write_text(dedent('''
        @echo off
        setlocal
        set "SCRIPT_DIR=%~dp0"
        powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%restore-environment.ps1"
        ''').strip() + "\n", encoding="utf-8")
        shell_script = dedent('''
        #!/bin/sh
        set -eu
        script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
        printf 'Close the app that uses this environment before restoring.\n'
        printf 'Type RESTORE to apply this backup: '
        read answer
        if [ "$answer" != "RESTORE" ]; then
          exit 1
        fi
        python_cmd=${PYTHON:-}
        if [ -n "$python_cmd" ]; then
          "$python_cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1 || {
            echo 'Python 3.11+ is required to restore this backup.' >&2
            exit 1
          }
        else
          for candidate in python3 python; do
            if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
              python_cmd="$candidate"
              break
            fi
          done
          if [ -z "$python_cmd" ]; then
            echo 'Python 3.11+ is required to restore this backup.' >&2
            exit 1
          fi
        fi
        "$python_cmd" "$script_dir/restore-standalone.py" --backup-dir "$script_dir" --apply --confirm
        ''').strip() + "\n"
        command_path = backup_dir / "restore-environment.command"
        sh_path = backup_dir / "restore-environment.sh"
        command_path.write_text(shell_script, encoding="utf-8")
        sh_path.write_text(shell_script, encoding="utf-8")
        for path in (command_path, sh_path):
            try:
                path.chmod(0o755)
            except OSError:
                pass

    def restore_overlay(backup_dir: Path, target_home: Path) -> dict:
        files_dir = backup_dir / "files"
        if not files_dir.is_dir():
            raise SystemExit(f"Backup directory does not contain files/: {backup_dir}")
        restored = 0
        skipped = []
        for source in files_dir.rglob("*"):
            if not source.is_file():
                continue
            relative = source.relative_to(files_dir)
            if is_excluded(relative):
                skipped.append(relative.as_posix())
                continue
            destination = target_home / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination, follow_symlinks=False)
            restored += 1
        return {"restored_files": restored, "skipped": skipped}

    def main(argv: list[str] | None = None) -> int:
        parser = argparse.ArgumentParser(description="Restore an environment backup.")
        parser.add_argument("--backup-dir", required=True)
        parser.add_argument("--target-home")
        parser.add_argument("--codex-home", dest="target_home", help=argparse.SUPPRESS)
        parser.add_argument("--backup-root")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm", action="store_true")
        args = parser.parse_args(argv)

        backup_dir = Path(args.backup_dir).expanduser().resolve()

        manifest_path = backup_dir / "manifest.json"
        if not manifest_path.exists():
            raise SystemExit(f"Missing manifest.json in {backup_dir}")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        profile = manifest.get("profile", "codex")
        target_home = resolve_target_home(args.target_home, profile)
        backup_root = Path(args.backup_root).expanduser().resolve() if args.backup_root else default_backup_root(profile)

        print(json.dumps({
            "ok": True,
            "dry_run": not args.apply,
            "backup_dir": str(backup_dir),
            "target_home": str(target_home),
            "source_manifest": {
                "created_at": manifest.get("created_at"),
                "backup_name": manifest.get("backup_name"),
                "file_count": manifest.get("counts", {}).get("files"),
                "sqlite_databases": manifest.get("counts", {}).get("sqlite_databases"),
            },
        }, indent=2))

        if not args.apply:
            return 0
        if not args.confirm:
            raise SystemExit("Missing confirmation for apply restore.")

        pre_prefix = "pre-restore-claude-code-backup" if profile == "claude-code" else "pre-restore-codex-backup"
        pre_restore = None
        if target_home.exists():
            pre_restore = create_backup(target_home, backup_root, pre_prefix)
            if not pre_restore.get("ok"):
                print(json.dumps({
                    "ok": False,
                    "pre_restore_backup": pre_restore,
                    "restore": {
                        "restored_files": 0,
                        "skipped": [],
                        "errors": [{
                            "error": "pre_restore_backup_failed",
                            "message": "Restore aborted because the current environment home could not be backed up completely.",
                        }],
                    },
                }, indent=2))
                return 2
        else:
            target_home.mkdir(parents=True, exist_ok=True)
        result = restore_overlay(backup_dir, target_home)
        post = {
            "pre_restore_backup": pre_restore,
            "restore": result,
            "ok": True,
        }
        print(json.dumps(post, indent=2))
        return 0

    if __name__ == "__main__":
        raise SystemExit(main())
    """
).lstrip()


def write_restore_kit(backup_dir: Path, display_name: str = "Codex") -> dict[str, str]:
    restore_md = backup_dir / "RESTORE.md"
    instructions = backup_dir / "RESTORE_INSTRUCTIONS.txt"
    restore_py = backup_dir / "restore-standalone.py"
    restore_ps1 = backup_dir / "restore-environment.ps1"
    restore_cmd = backup_dir / "restore-environment.cmd"
    restore_command = backup_dir / "restore-environment.command"
    restore_sh = backup_dir / "restore-environment.sh"

    restore_md.write_text(restore_kit_markdown(display_name), encoding="utf-8")
    instructions.write_text(restore_kit_instructions(display_name), encoding="utf-8")
    restore_py.write_text(RESTORE_STANDALONE_PY, encoding="utf-8")
    close_msg = f"Close the {display_name} app that uses this environment before restoring."
    restore_ps1.write_text(
        dedent(
            """
            $ErrorActionPreference = 'Stop'
            $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
            Write-Host '__CLOSE_MSG__'
            $answer = Read-Host 'Type RESTORE to apply this backup'
            if ($answer -ne 'RESTORE') { exit 1 }
            $pythonCmd = $null
            foreach ($candidate in @('python3', 'python')) {
                if (-not (Get-Command $candidate -ErrorAction SilentlyContinue)) {
                    continue
                }
                try {
                    $versionText = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
                    if ($LASTEXITCODE -ne 0) {
                        continue
                    }
                    $version = [version]($versionText | Select-Object -First 1)
                    if ($version -ge [version]'3.11') {
                        $pythonCmd = $candidate
                        break
                    }
                } catch {
                    continue
                }
            }
            if (-not $pythonCmd) { throw 'Python 3.11+ is required to restore this backup.' }
            & $pythonCmd "$scriptRoot\\restore-standalone.py" --backup-dir $scriptRoot --apply --confirm
            """
        ).strip().replace("__CLOSE_MSG__", close_msg)
        + "\n",
        encoding="utf-8",
    )
    restore_cmd.write_text(
        dedent(
            """
            @echo off
            setlocal
            set "SCRIPT_DIR=%~dp0"
            powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%restore-environment.ps1"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    restore_command.write_text(
        dedent(
            """
            #!/bin/sh
            set -eu
            script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
            printf '__CLOSE_MSG__\n'
            printf 'Type RESTORE to apply this backup: '
            read answer
            if [ "$answer" != "RESTORE" ]; then
              exit 1
            fi
            python_cmd=${PYTHON:-}
            if [ -n "$python_cmd" ]; then
              "$python_cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1 || {
                echo 'Python 3.11+ is required to restore this backup.' >&2
                exit 1
              }
            else
              for candidate in python3 python; do
                if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
                  python_cmd="$candidate"
                  break
                fi
              done
              if [ -z "$python_cmd" ]; then
                echo 'Python 3.11+ is required to restore this backup.' >&2
                exit 1
              fi
            fi
            "$python_cmd" "$script_dir/restore-standalone.py" --backup-dir "$script_dir" --apply --confirm
            """
        ).strip().replace("__CLOSE_MSG__", close_msg)
        + "\n",
        encoding="utf-8",
    )
    restore_sh.write_text(
        restore_command.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    try:
        restore_command.chmod(0o755)
    except OSError:
        pass
    try:
        restore_sh.chmod(0o755)
    except OSError:
        pass

    return {
        "restore_md": str(restore_md),
        "instructions": str(instructions),
        "restore_py": str(restore_py),
        "restore_ps1": str(restore_ps1),
        "restore_cmd": str(restore_cmd),
        "restore_command": str(restore_command),
        "restore_sh": str(restore_sh),
    }


def create_archive(backup_dir: Path, archive_format: str) -> Path:
    if archive_format == "tar.gz":
        archive_path = backup_dir.with_name(f"{backup_dir.name}.tar.gz")
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(backup_dir, arcname=backup_dir.name)
        return archive_path
    if archive_format == "zip":
        archive_path = backup_dir.with_name(f"{backup_dir.name}.zip")
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in backup_dir.rglob("*"):
                archive.write(path, path.relative_to(backup_dir.parent))
        return archive_path
    raise BackupError(f"Unsupported archive format: {archive_format}")


def create_backup(
    codex_home: str | os.PathLike[str] | None = None,
    *,
    backup_root: str | os.PathLike[str] | None = None,
    profile: EnvironmentProfile | None = None,
    archive_format: str = "tar.gz",
    make_archive: bool = True,
    timestamp: str | None = None,
    run_doctor_commands: bool = True,
) -> dict[str, Any]:
    if profile is None:
        profile = CODEX_PROFILE
    home = resolve_home(profile, codex_home)
    if not home.exists() or not home.is_dir():
        raise BackupError(
            f"{profile.display_name} home does not exist or is not a directory: {home}"
        )

    root = Path(backup_root).expanduser().resolve() if backup_root else default_backup_root(profile)
    if is_relative_to(root, home):
        raise BackupError(
            f"backup_root must not be inside {profile.display_name} home"
        )
    root.mkdir(parents=True, exist_ok=True)

    backup_name = timestamp or local_timestamp(profile.backup_prefix)
    backup_dir = root / backup_name
    suffix = 1
    while backup_dir.exists():
        backup_dir = root / f"{backup_name}-{suffix}"
        suffix += 1
    backup_name = backup_dir.name
    files_dir = backup_dir / "files"
    files_dir.mkdir(parents=True)

    entries: list[dict[str, Any]] = []
    sqlite_checks: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    extra_excluded = frozenset(profile.extra_excluded_dirs)

    for source, relative in iter_source_files(home, errors, extra_excluded):
        destination = files_dir / relative
        method = "copy2"
        try:
            if is_sqlite_database(source):
                method = "sqlite_backup"
                backup_sqlite_database(source, destination)
                sqlite_checks.append(sqlite_integrity_check(destination))
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination, follow_symlinks=False)
            stat = destination.stat()
            entries.append(
                {
                    "relative_path": normalize_relative(relative),
                    "kind": "file",
                    "bytes": stat.st_size,
                    "sha256": sha256_file(destination),
                    "method": method,
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "relative_path": normalize_relative(relative),
                    "method": method,
                    "error": str(exc),
                }
            )

    sensitive_note = _make_sensitive_note(profile.display_name)
    doctor_report = doctor_environment(home, profile=profile, run_commands=run_doctor_commands)
    manifest = {
        "schema_version": 1,
        "created_at": utc_now_iso(),
        "profile": profile.name,
        "home": str(home),
        "backup_name": backup_name,
        "archive_format": archive_format if make_archive else None,
        "sensitive_note": sensitive_note,
        "platform": doctor_report["platform"],
        "exclusions": {
            "directory_names": sorted(EXCLUDED_DIR_NAMES | set(profile.extra_excluded_dirs)),
            "live_sqlite_suffixes": list(LIVE_SQLITE_SUFFIXES),
        },
        "entries": entries,
        "errors": errors,
        "counts": {
            "files": len(entries),
            "sqlite_databases": sum(1 for entry in entries if entry["method"] == "sqlite_backup"),
            "errors": len(errors),
        },
    }
    write_json(backup_dir / "manifest.json", manifest)
    write_json(backup_dir / "sqlite-integrity-check.json", sqlite_checks)
    write_json(backup_dir / "doctor-report.json", doctor_report)
    write_environment_snapshot(backup_dir / "environment-snapshot.txt", doctor_report, profile.display_name)

    ok = not errors and all(check.get("ok") for check in sqlite_checks)
    summary_lines = [
        f"{profile.display_name} environment backup",
        f"Backup: {backup_dir}",
        f"{profile.display_name} home: {home}",
        f"Files: {len(entries)}",
        f"SQLite databases: {manifest['counts']['sqlite_databases']}",
        f"Errors: {len(errors)}",
        f"Integrity: {'ok' if all(check.get('ok') for check in sqlite_checks) else 'failed'}",
        "Restore kit: RESTORE.md, RESTORE_INSTRUCTIONS.txt, restore-environment.cmd, restore-environment.ps1, restore-environment.command, restore-environment.sh",
        "",
        sensitive_note,
    ]
    (backup_dir / "backup-summary.txt").write_text(
        "\n".join(summary_lines) + "\n", encoding="utf-8"
    )

    restore_kit = write_restore_kit(backup_dir, profile.display_name)

    archive_path = None
    sha256_path = None
    archive_sha256 = None
    if make_archive:
        archive_path = create_archive(backup_dir, archive_format)
        archive_sha256 = sha256_file(archive_path)
        sha256_path = archive_path.with_name(f"{archive_path.name}.sha256")
        sha256_path.write_text(f"{archive_sha256}  {archive_path.name}\n", encoding="utf-8")

    return {
        "ok": ok,
        "backup_dir": str(backup_dir),
        "archive": str(archive_path) if archive_path else None,
        "archive_sha256": archive_sha256,
        "sha256_file": str(sha256_path) if sha256_path else None,
        "manifest": str(backup_dir / "manifest.json"),
        "sqlite_integrity": str(backup_dir / "sqlite-integrity-check.json"),
        "doctor_report": str(backup_dir / "doctor-report.json"),
        "restore_kit": restore_kit,
        "counts": manifest["counts"],
        "errors": errors,
        "sensitive_note": sensitive_note,
    }


def safe_extract_tar(archive_path: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with tarfile.open(archive_path, "r:*") as archive:
        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            if not is_relative_to(target, destination_resolved):
                raise BackupError(f"Archive member escapes extraction root: {member.name}")
            if member.isdir():
                continue
            if not member.isfile():
                raise BackupError(f"Unsupported archive member type: {member.name}")

        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = archive.extractfile(member)
            if extracted is None:
                raise BackupError(f"Unable to extract archive member: {member.name}")
            with extracted, target.open("wb") as handle:
                shutil.copyfileobj(extracted, handle)


def safe_extract_zip(archive_path: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if not is_relative_to(target, destination_resolved):
                raise BackupError(f"Archive member escapes extraction root: {member.filename}")
            mode = member.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if member.is_dir():
                continue
            if file_type and file_type != stat.S_IFREG:
                raise BackupError(f"Unsupported archive member type: {member.filename}")

        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as source, target.open("wb") as handle:
                shutil.copyfileobj(source, handle)


def locate_backup_dir(path: Path) -> Path:
    if (path / "manifest.json").exists() and (path / "files").is_dir():
        return path
    candidates = [
        candidate.parent
        for candidate in path.rglob("manifest.json")
        if (candidate.parent / "files").is_dir()
    ]
    if not candidates:
        raise BackupError(f"No backup manifest/files directory found in: {path}")
    candidates.sort(key=lambda candidate: len(candidate.parts))
    return candidates[0]


@contextlib.contextmanager
def temporary_extract_dir(source: Path, work_root: Path | None = None) -> Iterator[Path]:
    candidates = [candidate for candidate in (work_root, source.parent, Path.cwd()) if candidate]
    roots: list[Path] = []
    for candidate in candidates:
        root = Path(candidate).expanduser().resolve()
        if root not in roots:
            roots.append(root)

    errors: list[str] = []
    temp_dir = None
    for root in roots:
        try:
            root.mkdir(parents=True, exist_ok=True)
            candidate = root / f"codex-restore-work-{uuid.uuid4().hex}"
            candidate.mkdir()
            temp_dir = candidate
            break
        except OSError as exc:
            errors.append(f"{root}: {exc}")

    if temp_dir is None:
        raise BackupError(
            "Unable to create a restore extraction directory. Tried: " + "; ".join(errors)
        )

    try:
        yield temp_dir
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@contextlib.contextmanager
def open_backup_source(source: Path, work_root: Path | None = None) -> Iterator[Path]:
    source = source.expanduser().resolve()
    if source.is_dir():
        yield locate_backup_dir(source)
        return
    if not source.exists():
        raise BackupError(f"Backup source does not exist: {source}")
    with temporary_extract_dir(source, work_root) as temp_dir:
        lower_name = source.name.lower()
        if lower_name.endswith((".tar.gz", ".tgz", ".tar")):
            safe_extract_tar(source, temp_dir)
        elif lower_name.endswith(".zip"):
            safe_extract_zip(source, temp_dir)
        else:
            raise BackupError(f"Unsupported backup archive: {source}")
        yield locate_backup_dir(temp_dir)


def restore_plan(
    backup_dir: Path,
    target_home: Path,
    extra_excluded_dirs: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    files_dir = backup_dir / "files"
    files = [
        path
        for path in files_dir.rglob("*")
        if path.is_file() and not is_excluded(path.relative_to(files_dir), extra_excluded_dirs)
    ]
    total_bytes = sum(path.stat().st_size for path in files)
    return {
        "backup_dir": str(backup_dir),
        "target_home": str(target_home),
        "files": len(files),
        "bytes": total_bytes,
        "mode": "overlay",
        "will_prune_existing_files": False,
        "requires_app_closed": True,
        "will_skip_excluded_paths": sorted(EXCLUDED_DIR_NAMES | extra_excluded_dirs),
    }


def copy_backup_files(
    backup_dir: Path,
    target_home: Path,
    extra_excluded_dirs: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    files_dir = backup_dir / "files"
    restored = 0
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    for source in files_dir.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(files_dir)
        if is_excluded(relative, extra_excluded_dirs):
            skipped.append(normalize_relative(relative))
            continue
        destination = target_home / relative
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination, follow_symlinks=False)
            restored += 1
        except Exception as exc:
            errors.append(
                {
                    "relative_path": normalize_relative(relative),
                    "error": str(exc),
                }
            )
    return {"restored_files": restored, "skipped": skipped, "errors": errors}


def restore_backup(
    source: str | os.PathLike[str],
    codex_home: str | os.PathLike[str] | None = None,
    *,
    backup_root: str | os.PathLike[str] | None = None,
    profile: EnvironmentProfile | None = None,
    apply: bool = False,
    confirm: bool = False,
    archive_format: str = "tar.gz",
    run_post_restore_commands: bool = False,
) -> dict[str, Any]:
    if profile is None:
        profile = CODEX_PROFILE
    home = resolve_home(profile, codex_home)
    root = Path(backup_root).expanduser().resolve() if backup_root else default_backup_root(profile)
    source_path = Path(source).expanduser().resolve()
    source_is_dir = source_path.is_dir()
    extra_excluded = frozenset(profile.extra_excluded_dirs)
    with open_backup_source(source_path, root) as backup_dir:
        plan = restore_plan(backup_dir, home, extra_excluded)
        manifest_path = backup_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if source_is_dir:
            restore_kit = {
                "restore_md": str(backup_dir / "RESTORE.md"),
                "instructions": str(backup_dir / "RESTORE_INSTRUCTIONS.txt"),
                "restore_cmd": str(backup_dir / "restore-environment.cmd"),
                "restore_command": str(backup_dir / "restore-environment.command"),
                "restore_sh": str(backup_dir / "restore-environment.sh"),
                "restore_ps1": str(backup_dir / "restore-environment.ps1"),
            }
        else:
            restore_kit = {
                "available_in_archive": True,
                "source_archive": str(source_path),
                "instructions": "Extract the archive and open RESTORE.md, or RESTORE_INSTRUCTIONS.txt as a plain-text fallback, to use the no-command restore helper.",
            }
        result: dict[str, Any] = {
            "ok": True,
            "dry_run": not apply,
            "plan": plan,
            "source_manifest": {
                "created_at": manifest.get("created_at"),
                "backup_name": manifest.get("backup_name"),
                "file_count": manifest.get("counts", {}).get("files"),
                "sqlite_databases": manifest.get("counts", {}).get("sqlite_databases"),
            },
            "restore_kit": restore_kit,
            "sensitive_note": _make_sensitive_note(profile.display_name),
        }
        manifest_profile = manifest.get("profile", "codex")
        if manifest_profile != profile.name:
            result["profile_mismatch"] = {
                "backup_profile": manifest_profile,
                "restore_profile": profile.name,
                "warning": (
                    f"This backup was created with profile '{manifest_profile}' "
                    f"but restore is using profile '{profile.name}'. "
                    "The backup may be restored to the wrong home directory."
                ),
            }
        if not apply:
            result["message"] = (
                f"Dry run only. Close {profile.display_name} before restore, then rerun with --apply "
                "and --i-understand-this-restores-sensitive-state."
            )
            return result
        if not confirm:
            raise BackupError(
                "Restore apply requires --i-understand-this-restores-sensitive-state"
            )

        pre_restore = None
        if home.exists():
            pre_restore = create_backup(
                home,
                backup_root=root,
                profile=profile,
                archive_format=archive_format,
                timestamp=local_timestamp(profile.pre_restore_prefix),
                run_doctor_commands=False,
            )
            if not pre_restore.get("ok"):
                result.update(
                    {
                        "pre_restore_backup": pre_restore,
                        "restore": {
                            "restored_files": 0,
                            "skipped": [],
                            "errors": [
                                {
                                    "error": "pre_restore_backup_failed",
                                    "message": f"Restore aborted because the current {profile.display_name} home could not be backed up completely.",
                                }
                            ],
                        },
                        "ok": False,
                    }
                )
                return result
        else:
            root.mkdir(parents=True, exist_ok=True)
            home.mkdir(parents=True, exist_ok=True)

        copy_result = copy_backup_files(backup_dir, home, extra_excluded)
        post_doctor = doctor_environment(home, profile=profile, run_commands=run_post_restore_commands)
        result.update(
            {
                "pre_restore_backup": pre_restore,
                "restore": copy_result,
                "post_restore_doctor": post_doctor,
                "post_restore_doctor_mode": "full"
                if run_post_restore_commands
                else "structural",
                "post_restore_next_steps": (
                    f"Reopen {profile.display_name} or start a fresh CLI session, then run a full doctor check "
                    "against the restored home."
                    if not run_post_restore_commands
                    else None
                ),
                "ok": not copy_result["errors"] and post_doctor["ok"],
            }
        )
        return result


def count_files_under(path: Path) -> int:
    return sum(1 for candidate in path.rglob("*") if candidate.is_file())


def backup_list_item(manifest: Path, data: dict[str, Any]) -> dict[str, Any]:
    counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
    entries = data.get("entries") if isinstance(data.get("entries"), list) else []
    schema_version = data.get("schema_version")
    schema_label = schema_version if schema_version is not None else "legacy"
    files_dir = manifest.parent / "files"

    files = counts.get("files")
    if files is None and entries:
        files = len(entries)
    if files is None and files_dir.is_dir():
        files = count_files_under(files_dir)
    if files is None:
        files = count_files_under(manifest.parent)

    sqlite_databases = counts.get("sqlite_databases")
    if sqlite_databases is None and entries:
        sqlite_databases = sum(1 for entry in entries if entry.get("method") == "sqlite_backup")
    if sqlite_databases is None and isinstance(data.get("sqlite_online_backup"), list):
        sqlite_databases = len(data["sqlite_online_backup"])

    errors = counts.get("errors")
    if errors is None and isinstance(data.get("errors"), list):
        errors = len(data["errors"])
    if errors is None:
        errors = 0

    status = "ok" if schema_version == 1 else "legacy_manifest"
    archive_candidates = [
        manifest.parent.with_name(f"{manifest.parent.name}.tar.gz"),
        manifest.parent.with_name(f"{manifest.parent.name}.zip"),
    ]
    item: dict[str, Any] = {
        "backup_dir": str(manifest.parent),
        "status": status,
        "schema_version": schema_label,
        "profile": data.get("profile"),
        "created_at": data.get("created_at") or data.get("generated_at"),
        "files": files,
        "sqlite_databases": sqlite_databases,
        "errors": errors,
        "archives": [str(path) for path in archive_candidates if path.exists()],
    }
    if status == "legacy_manifest":
        item["legacy_summary"] = {
            "generated_at": data.get("generated_at"),
            "root_files": len(data.get("included_root_files", []))
            if isinstance(data.get("included_root_files"), list)
            else None,
            "directories": len(data.get("included_directories", []))
            if isinstance(data.get("included_directories"), list)
            else None,
            "sqlite_online_backup": len(data.get("sqlite_online_backup", []))
            if isinstance(data.get("sqlite_online_backup"), list)
            else None,
            "counts_estimated": True,
        }
    return item


def list_backups(
    backup_root: str | os.PathLike[str] | None = None,
    *,
    profile: EnvironmentProfile | None = None,
) -> dict[str, Any]:
    if profile is None:
        profile = CODEX_PROFILE
    root = Path(backup_root).expanduser().resolve() if backup_root else default_backup_root(profile)
    items: list[dict[str, Any]] = []
    if not root.exists():
        return {"ok": True, "backup_root": str(root), "backups": items}
    for manifest in sorted(root.rglob("manifest.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception as exc:
            items.append(
                {
                    "backup_dir": str(manifest.parent),
                    "status": "unreadable",
                    "error": str(exc),
                }
            )
            continue
        items.append(backup_list_item(manifest, data))
    return {"ok": True, "backup_root": str(root), "backups": items}
