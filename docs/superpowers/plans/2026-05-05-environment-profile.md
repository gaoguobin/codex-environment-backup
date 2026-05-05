# Environment Profile Abstraction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support both Codex and Claude Code environment backup/restore from one codebase via an `EnvironmentProfile` dataclass, rename package to `agent-environment-backup`, and retain backward compatibility through a shim.

**Architecture:** Frozen dataclass `EnvironmentProfile` holds all environment-specific values (paths, config parser, commands, naming). Two pre-defined instances. All core functions accept an optional `profile` parameter defaulting to `CODEX_PROFILE`. Package rename happens last with a shim preserving old imports.

**Tech Stack:** Python 3.11+ stdlib only. unittest for tests. setuptools for packaging.

**Spec:** `docs/superpowers/specs/2026-05-05-environment-profile-design.md`

**Implementation order:** The spec mandates profile abstraction and tests first, rename last. Tasks follow this order.

---

### Task 1: Add EnvironmentProfile dataclass and profile instances

**Files:**
- Modify: `src/codex_environment_backup/core.py:1-48` (imports, constants)

- [ ] **Step 1: Write test for profile dataclass and registry**

Add to `tests/test_core.py`:

```python
def test_profile_registry_contains_both_profiles(self) -> None:
    from codex_environment_backup.core import PROFILES, CODEX_PROFILE, CLAUDE_CODE_PROFILE
    self.assertIn("codex", PROFILES)
    self.assertIn("claude-code", PROFILES)
    self.assertIs(PROFILES["codex"], CODEX_PROFILE)
    self.assertIs(PROFILES["claude-code"], CLAUDE_CODE_PROFILE)
    self.assertEqual(CODEX_PROFILE.name, "codex")
    self.assertEqual(CLAUDE_CODE_PROFILE.name, "claude-code")
    self.assertEqual(CODEX_PROFILE.default_home_dir, ".codex")
    self.assertEqual(CLAUDE_CODE_PROFILE.default_home_dir, ".claude")
    self.assertEqual(CODEX_PROFILE.env_home_var, "CODEX_HOME")
    self.assertIsNone(CLAUDE_CODE_PROFILE.env_home_var)
    self.assertEqual(CODEX_PROFILE.backup_prefix, "codex-backup")
    self.assertEqual(CLAUDE_CODE_PROFILE.backup_prefix, "claude-code-backup")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_core.CodexEnvironmentBackupTests.test_profile_registry_contains_both_profiles -v`
Expected: FAIL — `PROFILES`, `CODEX_PROFILE`, `CLAUDE_CODE_PROFILE` not defined.

- [ ] **Step 3: Implement EnvironmentProfile dataclass, instances, and registry**

Add to `src/codex_environment_backup/core.py` after the existing imports (after line 21), before `EXCLUDED_DIR_NAMES`:

```python
from dataclasses import dataclass


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
```

After the `inspect_config` function (which will be renamed to `inspect_codex_config` in Task 3), add `inspect_claude_code_config`, then define the two profile instances and registry. For now, use `None` as the `config_inspector` for both profiles — Task 3 wires up the inspectors.

Add after the `SENSITIVE_NOTE` constant:

```python
def _make_sensitive_note(display_name: str) -> str:
    return (
        f"This backup can contain {display_name} history, provider configuration, "
        "login state, local hooks, and other sensitive environment data. Keep it "
        "offline unless you have explicitly reviewed and approved another storage location."
    )
```

The profile instances will be defined after config inspectors exist (Task 3). For now, define them with `config_inspector=None` so the dataclass and registry tests pass. They will be updated in Task 3.

```python
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
    config_inspector=None,
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
    config_inspector=None,
    commands=(("claude", "--version"), ("claude", "mcp", "list")),
    integration_module=None,
    extra_excluded_dirs=("cache",),
)

PROFILES: dict[str, EnvironmentProfile] = {
    "codex": CODEX_PROFILE,
    "claude-code": CLAUDE_CODE_PROFILE,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_core.CodexEnvironmentBackupTests.test_profile_registry_contains_both_profiles -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `python -m unittest discover -s tests -v`
Expected: All existing tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codex_environment_backup/core.py tests/test_core.py
git commit -m "feat: add EnvironmentProfile dataclass with Codex and Claude Code instances"
```

---

### Task 2: Profile-aware resolve_home, default_backup_root, is_excluded

**Files:**
- Modify: `src/codex_environment_backup/core.py:63-93`

- [ ] **Step 1: Write tests for resolve_home and default_backup_root**

Add to `tests/test_core.py`:

```python
def test_resolve_home_uses_profile_default(self) -> None:
    from codex_environment_backup.core import resolve_home, CODEX_PROFILE, CLAUDE_CODE_PROFILE
    with mock.patch.dict(os.environ, {}, clear=True):
        codex_home = resolve_home(CODEX_PROFILE)
        claude_home = resolve_home(CLAUDE_CODE_PROFILE)
    self.assertEqual(codex_home, (Path.home() / ".codex").resolve())
    self.assertEqual(claude_home, (Path.home() / ".claude").resolve())

def test_resolve_home_respects_env_var(self) -> None:
    from codex_environment_backup.core import resolve_home, CODEX_PROFILE, CLAUDE_CODE_PROFILE
    with mock.patch.dict(os.environ, {"CODEX_HOME": "/tmp/custom-codex"}, clear=True):
        result = resolve_home(CODEX_PROFILE)
    self.assertEqual(result, Path("/tmp/custom-codex").resolve())
    with mock.patch.dict(os.environ, {}, clear=True):
        result = resolve_home(CLAUDE_CODE_PROFILE)
    self.assertEqual(result, (Path.home() / ".claude").resolve())

def test_resolve_home_override_takes_precedence(self) -> None:
    from codex_environment_backup.core import resolve_home, CODEX_PROFILE
    with mock.patch.dict(os.environ, {"CODEX_HOME": "/tmp/env"}, clear=True):
        result = resolve_home(CODEX_PROFILE, "/tmp/explicit")
    self.assertEqual(result, Path("/tmp/explicit").resolve())

def test_default_backup_root_uses_profile(self) -> None:
    from codex_environment_backup.core import default_backup_root, CODEX_PROFILE, CLAUDE_CODE_PROFILE
    codex_root = default_backup_root(CODEX_PROFILE)
    claude_root = default_backup_root(CLAUDE_CODE_PROFILE)
    self.assertTrue(str(codex_root).endswith("CodexBackups"))
    self.assertTrue(str(claude_root).endswith("ClaudeCodeBackups"))
```

Add `import os` to the test file imports if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_core.CodexEnvironmentBackupTests.test_resolve_home_uses_profile_default tests.test_core.CodexEnvironmentBackupTests.test_default_backup_root_uses_profile -v`
Expected: FAIL — `resolve_home` not defined.

- [ ] **Step 3: Implement resolve_home, update default_backup_root, update is_excluded**

In `core.py`, add `resolve_home` and update the existing functions:

```python
def resolve_home(
    profile: EnvironmentProfile = CODEX_PROFILE,
    home_override: str | os.PathLike[str] | None = None,
) -> Path:
    if home_override:
        return Path(home_override).expanduser().resolve()
    if profile.env_home_var is not None:
        env_home = os.environ.get(profile.env_home_var)
        if env_home:
            return Path(env_home).expanduser().resolve()
    return (Path.home() / profile.default_home_dir).resolve()


def resolve_codex_home(codex_home: str | os.PathLike[str] | None = None) -> Path:
    return resolve_home(CODEX_PROFILE, codex_home)


def default_backup_root(profile: EnvironmentProfile = CODEX_PROFILE) -> Path:
    return (Path.home() / "Documents" / profile.default_backup_subdir).resolve()
```

Update `is_excluded` to accept extra exclusions:

```python
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
```

- [ ] **Step 4: Run all tests**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS (new tests + existing tests unchanged because `default_backup_root()` with no args still returns CodexBackups, `is_excluded()` with no extra args still uses only `EXCLUDED_DIR_NAMES`).

- [ ] **Step 5: Commit**

```bash
git add src/codex_environment_backup/core.py tests/test_core.py
git commit -m "feat: add profile-aware resolve_home, default_backup_root, is_excluded"
```

---

### Task 3: Config inspectors and wire profile instances

**Files:**
- Modify: `src/codex_environment_backup/core.py:286-330` (inspect_config)

- [ ] **Step 1: Write test for inspect_claude_code_config**

Add to `tests/test_core.py`:

```python
def test_inspect_claude_code_config(self) -> None:
    from codex_environment_backup.core import inspect_claude_code_config
    with self.temp_root() as temp_dir:
        home = Path(temp_dir) / "claude-home"
        home.mkdir()
        settings = {
            "permissions": {"allow": ["Bash(git *)"]},
            "env": {"DEBUG": "1"},
            "hooks": {"afterToolCall": [{"command": "echo done"}]},
            "model": "claude-sonnet-4-6",
            "allowedTools": ["Bash", "Read"],
        }
        (home / "settings.json").write_text(
            json.dumps(settings), encoding="utf-8"
        )
        result = inspect_claude_code_config(home)
        self.assertEqual(result["parse_status"], "ok")
        self.assertTrue(result["permissions_present"])
        self.assertEqual(result["permissions_count"], 1)
        self.assertTrue(result["env_present"])
        self.assertTrue(result["hooks_present"])
        self.assertEqual(result["hooks_count"], 1)
        self.assertEqual(result["model"], "claude-sonnet-4-6")
        self.assertTrue(result["allowed_tools_present"])

def test_inspect_claude_code_config_missing(self) -> None:
    from codex_environment_backup.core import inspect_claude_code_config
    with self.temp_root() as temp_dir:
        home = Path(temp_dir) / "claude-home"
        home.mkdir()
        result = inspect_claude_code_config(home)
        self.assertFalse(result["present"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_core.CodexEnvironmentBackupTests.test_inspect_claude_code_config -v`
Expected: FAIL — `inspect_claude_code_config` not defined.

- [ ] **Step 3: Implement inspect_claude_code_config and rename inspect_config**

In `core.py`, rename `inspect_config` to `inspect_codex_config`. Add `inspect_config` as an alias:

```python
def inspect_codex_config(home: Path) -> dict[str, Any]:
    # ... existing inspect_config body unchanged ...

inspect_config = inspect_codex_config
```

Add `inspect_claude_code_config`:

```python
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
```

- [ ] **Step 4: Wire config_inspector into profile instances**

Update `CODEX_PROFILE` and `CLAUDE_CODE_PROFILE` definitions to use the real inspectors. Since the profile instances are defined after the functions, this is just changing `config_inspector=None` to the actual function references:

```python
CODEX_PROFILE = EnvironmentProfile(
    ...
    config_inspector=inspect_codex_config,
    ...
)

CLAUDE_CODE_PROFILE = EnvironmentProfile(
    ...
    config_inspector=inspect_claude_code_config,
    ...
)
```

Note: The profile instances must be defined AFTER the inspector functions. Move the profile/registry definitions to after `inspect_claude_code_config` in the file.

- [ ] **Step 5: Run all tests**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codex_environment_backup/core.py tests/test_core.py
git commit -m "feat: add inspect_claude_code_config and wire config inspectors into profiles"
```

---

### Task 4: Profile-aware doctor_environment

**Files:**
- Modify: `src/codex_environment_backup/core.py:353-444`

- [ ] **Step 1: Write test for doctor with Claude Code profile**

Add to `tests/test_core.py`:

```python
def make_claude_code_home(self, root: Path) -> Path:
    home = root / "claude-home"
    home.mkdir(parents=True)
    (home / "projects").mkdir()
    (home / "memory").mkdir()
    (home / "todos").mkdir()
    (home / "plugins").mkdir()
    (home / "statsig").mkdir()
    (home / "settings.json").write_text(
        json.dumps({"model": "claude-sonnet-4-6", "permissions": {"allow": []}}),
        encoding="utf-8",
    )
    (home / "settings.local.json").write_text("{}", encoding="utf-8")
    (home / "credentials.json").write_text(
        '{"access_token":"FAKE-CLAUDE-TOKEN"}', encoding="utf-8"
    )
    (home / "keybindings.json").write_text("[]", encoding="utf-8")
    self.make_sqlite(home / "data.sqlite", "data")
    return home

def test_doctor_claude_code_profile_structural(self) -> None:
    from codex_environment_backup.core import doctor_environment, CLAUDE_CODE_PROFILE
    with self.temp_root() as temp_dir:
        home = self.make_claude_code_home(Path(temp_dir))
        report = doctor_environment(home, profile=CLAUDE_CODE_PROFILE, run_commands=False)
        self.assertTrue(report["ok"], report)
        self.assertIn("settings.json", report["paths"])
        self.assertIn("projects", report["paths"])
        self.assertIn("memory", report["paths"])
        self.assertNotIn("auth.json", report["paths"])
        self.assertNotIn("sessions", report["paths"])
        self.assertIn("home", report)
        self.assertNotIn("codex_home", report)
        report_json = json.dumps(report)
        self.assertNotIn("FAKE-CLAUDE-TOKEN", report_json)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_core.CodexEnvironmentBackupTests.test_doctor_claude_code_profile_structural -v`
Expected: FAIL — `doctor_environment` not defined.

- [ ] **Step 3: Implement doctor_environment**

Refactor `doctor_codex_environment` into `doctor_environment` that accepts a profile. The existing function becomes an alias.

```python
def doctor_environment(
    home_override: str | os.PathLike[str] | None = None,
    *,
    profile: EnvironmentProfile = CODEX_PROFILE,
    run_commands: bool = True,
) -> dict[str, Any]:
    home = resolve_home(profile, home_override)
    core_ok = home.exists() and home.is_dir()
    sensitive_note = _make_sensitive_note(profile.display_name)
    report: dict[str, Any] = {
        "ok": core_ok,
        "core_ok": core_ok,
        "created_at": utc_now_iso(),
        "profile": profile.name,
        "home": str(home),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "sensitive_note": sensitive_note,
        "paths": {},
        "config": {},
        "commands": {},
    }

    if profile.config_inspector is not None:
        report["config"] = profile.config_inspector(home)

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
        for cmd_tuple in profile.commands:
            cmd_name = "_".join(cmd_tuple).replace("-", "_")
            report["commands"][cmd_name] = run_command(list(cmd_tuple), env=command_env)
        if profile.integration_module is not None:
            if importlib.util.find_spec(profile.integration_module) is not None:
                report["commands"][f"{profile.integration_module}_status"] = run_command(
                    [sys.executable, "-m", profile.integration_module, "status"],
                    env=command_env,
                    include_output=False,
                    json_summary=True,
                )
                report["commands"][f"{profile.integration_module}_doctor"] = run_command(
                    [sys.executable, "-m", profile.integration_module, "doctor"],
                    env=command_env,
                    include_output=False,
                    json_summary=True,
                )
            else:
                report["commands"][profile.integration_module] = {
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
```

- [ ] **Step 4: Update existing tests that read `report["codex_home"]`**

The existing tests access `report["codex_home"]` — update to `report["home"]`. Search `tests/test_core.py` for `codex_home` in assertions and update. The `doctor_codex_environment` alias still works, but the output key is now `"home"`.

- [ ] **Step 5: Run all tests**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codex_environment_backup/core.py tests/test_core.py
git commit -m "feat: add profile-aware doctor_environment with Codex alias"
```

---

### Task 5: Profile-aware create_backup and manifest profile field

**Files:**
- Modify: `src/codex_environment_backup/core.py:1127-1255`

- [ ] **Step 1: Write test for backup with Claude Code profile**

Add to `tests/test_core.py`:

```python
def test_backup_claude_code_profile(self) -> None:
    from codex_environment_backup.core import create_backup, CLAUDE_CODE_PROFILE
    with self.temp_root() as temp_dir:
        root = Path(temp_dir)
        home = self.make_claude_code_home(root)
        backup_root = root / "backups"

        result = create_backup(
            home,
            backup_root=backup_root,
            profile=CLAUDE_CODE_PROFILE,
            timestamp="claude-code-backup-test",
            run_doctor_commands=False,
        )

        self.assertTrue(result["ok"], result)
        self.assertIn("claude-code-backup-test", result["backup_dir"])
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["profile"], "claude-code")
        self.assertIn("home", manifest)
        self.assertNotIn("codex_home", manifest)
        paths = {entry["relative_path"] for entry in manifest["entries"]}
        self.assertIn("settings.json", paths)
        self.assertIn("credentials.json", paths)
        self.assertIn("data.sqlite", paths)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_core.CodexEnvironmentBackupTests.test_backup_claude_code_profile -v`
Expected: FAIL — `create_backup` does not accept `profile` parameter.

- [ ] **Step 3: Add profile parameter to create_backup**

Update `create_backup` signature to accept `profile: EnvironmentProfile = CODEX_PROFILE`. Key changes inside the function:

- Replace `resolve_codex_home(codex_home)` with `resolve_home(profile, codex_home)`
- Replace `default_backup_root()` with `default_backup_root(profile)`
- Replace `local_timestamp()` with `local_timestamp(profile.backup_prefix)`
- Replace `doctor_codex_environment(home, ...)` with `doctor_environment(home, profile=profile, ...)`
- Replace hardcoded `SENSITIVE_NOTE` with `_make_sensitive_note(profile.display_name)`
- Replace `"Codex environment backup"` / `"Codex home"` in summary with profile.display_name
- Add `"profile": profile.name` to manifest dict
- Replace `"codex_home": str(home)` with `"home": str(home)` in manifest
- Replace `"Restore kit: RESTORE.md, ... restore-codex-environment.*"` with `"restore-environment.*"` references in summary
- Pass `extra_excluded_dirs=frozenset(profile.extra_excluded_dirs)` to `is_excluded` calls in `iter_source_files`

Update `iter_source_files` to accept and pass through `extra_excluded_dirs`:

```python
def iter_source_files(
    home: Path,
    errors: list[dict[str, str]] | None = None,
    extra_excluded_dirs: frozenset[str] = frozenset(),
) -> Iterator[tuple[Path, Path]]:
```

And pass `extra_excluded_dirs` to `is_excluded` inside the function.

- [ ] **Step 4: Run all tests**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS. Existing tests call `create_backup` without `profile` and get `CODEX_PROFILE` default.

- [ ] **Step 5: Commit**

```bash
git add src/codex_environment_backup/core.py tests/test_core.py
git commit -m "feat: add profile parameter to create_backup with manifest profile field"
```

---

### Task 6: Profile-aware restore_backup and list_backups

**Files:**
- Modify: `src/codex_environment_backup/core.py` (restore_backup, list_backups, backup_list_item)

- [ ] **Step 1: Write test for restore with Claude Code profile**

Add to `tests/test_core.py`:

```python
def test_restore_claude_code_profile_dry_run_and_apply(self) -> None:
    from codex_environment_backup.core import (
        create_backup, restore_backup, list_backups, CLAUDE_CODE_PROFILE,
    )
    with self.temp_root() as temp_dir:
        root = Path(temp_dir)
        source_home = self.make_claude_code_home(root)
        backup_result = create_backup(
            source_home,
            backup_root=root / "backups",
            profile=CLAUDE_CODE_PROFILE,
            timestamp="claude-code-backup-test",
            run_doctor_commands=False,
        )
        archive = Path(backup_result["archive"])

        dry_run = restore_backup(archive, root / "dry-target", profile=CLAUDE_CODE_PROFILE)
        self.assertTrue(dry_run["dry_run"])
        self.assertFalse((root / "dry-target").exists())

        target = root / "restored-claude"
        target.mkdir()
        (target / "settings.json").write_text('{"old":true}', encoding="utf-8")
        result = restore_backup(
            archive,
            target,
            backup_root=root / "prebacks",
            profile=CLAUDE_CODE_PROFILE,
            apply=True,
            confirm=True,
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["pre_restore_backup"])
        restored_settings = json.loads(
            (target / "settings.json").read_text(encoding="utf-8")
        )
        self.assertIn("model", restored_settings)

        listing = list_backups(root / "backups", profile=CLAUDE_CODE_PROFILE)
        self.assertTrue(any(
            item.get("status") == "ok" for item in listing["backups"]
        ))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_core.CodexEnvironmentBackupTests.test_restore_claude_code_profile_dry_run_and_apply -v`
Expected: FAIL — `restore_backup` does not accept `profile`.

- [ ] **Step 3: Add profile parameter to restore_backup and list_backups**

For `restore_backup`, add `profile: EnvironmentProfile = CODEX_PROFILE` parameter. Inside:
- Replace `resolve_codex_home` with `resolve_home(profile, ...)`
- Replace `default_backup_root()` with `default_backup_root(profile)`
- Replace `local_timestamp("pre-restore-codex-backup")` with `local_timestamp(profile.pre_restore_prefix)`
- Pass `profile=profile` to `create_backup` and `doctor_environment` calls
- Replace `"Codex home"` / `SENSITIVE_NOTE` with profile-derived values
- Rename `--i-understand-this-restores-sensitive-codex-state` error message to generic form

For `list_backups`, add `profile: EnvironmentProfile = CODEX_PROFILE` parameter:
- Replace `default_backup_root()` with `default_backup_root(profile)`

For `backup_list_item`, add profile annotation from manifest data when `profile` key is present.

- [ ] **Step 4: Run all tests**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_environment_backup/core.py tests/test_core.py
git commit -m "feat: add profile parameter to restore_backup and list_backups"
```

---

### Task 7: Generalize restore kit text and filenames

**Files:**
- Modify: `src/codex_environment_backup/core.py:550-1100` (restore_kit_instructions, restore_kit_markdown, RESTORE_STANDALONE_PY, write_restore_kit)

- [ ] **Step 1: Write test for restore kit with Claude Code profile**

Add to `tests/test_core.py`:

```python
def test_restore_kit_uses_profile_display_name(self) -> None:
    from codex_environment_backup.core import create_backup, CLAUDE_CODE_PROFILE
    with self.temp_root() as temp_dir:
        root = Path(temp_dir)
        home = self.make_claude_code_home(root)
        result = create_backup(
            home,
            backup_root=root / "backups",
            profile=CLAUDE_CODE_PROFILE,
            timestamp="claude-code-kit-test",
            run_doctor_commands=False,
        )
        backup_dir = Path(result["backup_dir"])
        restore_md = (backup_dir / "RESTORE.md").read_text(encoding="utf-8")
        instructions = (backup_dir / "RESTORE_INSTRUCTIONS.txt").read_text(encoding="utf-8")
        self.assertIn("Claude Code", restore_md)
        self.assertIn("Claude Code", instructions)
        self.assertNotIn("Codex", restore_md)
        self.assertNotIn("Codex", instructions)
        self.assertTrue((backup_dir / "restore-environment.cmd").exists())
        self.assertTrue((backup_dir / "restore-environment.ps1").exists())
        self.assertTrue((backup_dir / "restore-environment.command").exists())
        self.assertTrue((backup_dir / "restore-environment.sh").exists())
        self.assertFalse((backup_dir / "restore-codex-environment.cmd").exists())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_core.CodexEnvironmentBackupTests.test_restore_kit_uses_profile_display_name -v`
Expected: FAIL — restore kit still uses "Codex" text and old filenames.

- [ ] **Step 3: Update restore kit functions to accept display_name**

Update `restore_kit_instructions(display_name: str)` and `restore_kit_markdown(display_name: str)` to template `{display_name}` into all user-facing text. Replace all `"Codex"` with `display_name`, all `"CODEX_HOME"` with `"the environment home directory"` in user-facing text.

Update `write_restore_kit(backup_dir: Path, display_name: str = "Codex")` to:
- Use `display_name` in text generation
- Write files as `restore-environment.*` instead of `restore-codex-environment.*`
- Update `RESTORE_STANDALONE_PY` embedded script: rename `--codex-home` to `--target-home`, replace "Codex" user-facing strings with a generic form (the standalone script cannot receive display_name at runtime, so use "agent environment" as the generic label)

Update `create_backup` to pass `profile.display_name` to `write_restore_kit`.

Update existing test assertions that check for `restore-codex-environment.*` filenames — change to `restore-environment.*`.

- [ ] **Step 4: Run all tests**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS after updating existing assertions.

- [ ] **Step 5: Commit**

```bash
git add src/codex_environment_backup/core.py tests/test_core.py
git commit -m "feat: generalize restore kit text and filenames for multi-profile support"
```

---

### Task 8: Profile-aware CLI with --profile and --home

**Files:**
- Modify: `src/codex_environment_backup/cli.py`

- [ ] **Step 1: Write test for CLI --profile argument**

Add to `tests/test_core.py`:

```python
def test_cli_profile_argument_default(self) -> None:
    from codex_environment_backup.cli import build_parser
    args = build_parser().parse_args(["doctor"])
    self.assertEqual(args.profile, "codex")

def test_cli_profile_argument_claude_code(self) -> None:
    from codex_environment_backup.cli import build_parser
    args = build_parser().parse_args(["--profile", "claude-code", "doctor"])
    self.assertEqual(args.profile, "claude-code")

def test_cli_home_argument_and_codex_home_alias(self) -> None:
    from codex_environment_backup.cli import build_parser
    args = build_parser().parse_args(["doctor", "--home", "/tmp/test"])
    self.assertEqual(args.home, "/tmp/test")
    args2 = build_parser().parse_args(["doctor", "--codex-home", "/tmp/test2"])
    self.assertEqual(args2.home, "/tmp/test2")

def test_cli_restore_confirm_flag_and_alias(self) -> None:
    from codex_environment_backup.cli import build_parser
    args = build_parser().parse_args([
        "restore", "--archive", "/tmp/a.tar.gz",
        "--apply", "--i-understand-this-restores-sensitive-state",
    ])
    self.assertTrue(args.confirm)
    args2 = build_parser().parse_args([
        "restore", "--archive", "/tmp/a.tar.gz",
        "--apply", "--i-understand-this-restores-sensitive-codex-state",
    ])
    self.assertTrue(args2.confirm)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_core.CodexEnvironmentBackupTests.test_cli_profile_argument_default -v`
Expected: FAIL — `args.profile` not defined.

- [ ] **Step 3: Update cli.py**

In `build_parser()`:

1. Add `--profile` to the top-level parser (before subparsers):

```python
parser.add_argument(
    "--profile",
    choices=["codex", "claude-code"],
    default="codex",
    help="Environment profile. Default: codex.",
)
```

2. In each subparser that has `--codex-home`, replace with `--home` and add `--codex-home` as an alias. Use `dest="home"`:

```python
backup.add_argument("--home", "--codex-home", dest="home", help="Environment home path.")
```

3. In the restore subparser, add the new confirm flag name alongside the old:

```python
restore.add_argument(
    "--i-understand-this-restores-sensitive-state",
    "--i-understand-this-restores-sensitive-codex-state",
    action="store_true",
    dest="confirm",
    help="Required with --apply.",
)
```

4. In `main()`, resolve the profile from `args.profile`:

```python
from .core import PROFILES
profile = PROFILES[args.profile]
```

5. Pass `profile=profile` and `args.home` (instead of `args.codex_home`) to all core function calls.

- [ ] **Step 4: Run all tests**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_environment_backup/cli.py tests/test_core.py
git commit -m "feat: add --profile and --home CLI arguments with backward-compatible aliases"
```

---

### Task 9: Update __init__.py exports

**Files:**
- Modify: `src/codex_environment_backup/__init__.py`

- [ ] **Step 1: Update exports**

```python
"""Agent environment backup and restore helpers."""

from .core import (
    BackupError,
    CLAUDE_CODE_PROFILE,
    CODEX_PROFILE,
    EnvironmentProfile,
    PROFILES,
    create_backup,
    default_backup_root,
    doctor_codex_environment,
    doctor_environment,
    list_backups,
    resolve_codex_home,
    resolve_home,
    restore_backup,
)

__all__ = [
    "BackupError",
    "CLAUDE_CODE_PROFILE",
    "CODEX_PROFILE",
    "EnvironmentProfile",
    "PROFILES",
    "create_backup",
    "default_backup_root",
    "doctor_codex_environment",
    "doctor_environment",
    "list_backups",
    "resolve_codex_home",
    "resolve_home",
    "restore_backup",
]

__version__ = "0.2.0"
```

- [ ] **Step 2: Run all tests**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS.

- [ ] **Step 3: Commit**

```bash
git add src/codex_environment_backup/__init__.py
git commit -m "feat: export new profile-aware API from __init__.py"
```

---

### Task 10: Rename package to agent_environment_backup with shim

**Files:**
- Create: `src/agent_environment_backup/` (move from `src/codex_environment_backup/`)
- Create: `src/codex_environment_backup/__init__.py` (shim)
- Create: `src/codex_environment_backup/__main__.py` (shim)
- Modify: `pyproject.toml`
- Modify: `scripts/*.py`
- Modify: `tests/test_core.py`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Write test for shim package**

Add to `tests/test_core.py`:

```python
def test_shim_package_reexports(self) -> None:
    import codex_environment_backup as shim
    import agent_environment_backup as main_pkg
    self.assertIs(shim.BackupError, main_pkg.BackupError)
    self.assertIs(shim.create_backup, main_pkg.create_backup)
    self.assertIs(shim.CODEX_PROFILE, main_pkg.CODEX_PROFILE)
    self.assertIs(shim.CLAUDE_CODE_PROFILE, main_pkg.CLAUDE_CODE_PROFILE)
    self.assertIs(shim.resolve_home, main_pkg.resolve_home)
```

- [ ] **Step 2: Rename src/codex_environment_backup to src/agent_environment_backup**

```bash
git mv src/codex_environment_backup src/agent_environment_backup
```

- [ ] **Step 3: Create shim package src/codex_environment_backup/**

Create `src/codex_environment_backup/__init__.py`:

```python
"""Backward-compatible shim — imports from agent_environment_backup."""
from agent_environment_backup import *  # noqa: F401,F403
from agent_environment_backup import __all__, __version__  # noqa: F811
```

Create `src/codex_environment_backup/__main__.py`:

```python
from agent_environment_backup.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Update pyproject.toml**

```toml
[project]
name = "agent-environment-backup"
version = "0.2.0"
description = "Offline backup, restore, and health checks for local AI agent environments."

[project.scripts]
agent-environment-backup = "agent_environment_backup.cli:main"

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 5: Update test imports**

In `tests/test_core.py`, update the import block:

```python
import agent_environment_backup.core as core_module  # noqa: E402
from agent_environment_backup.core import (  # noqa: E402
    BackupError,
    create_backup,
    doctor_codex_environment,
    doctor_environment,
    list_backups,
    restore_backup,
)
```

In `tests/test_docs.py`, no import changes needed (it reads files, not Python modules).

- [ ] **Step 6: Update scripts/*.py**

In each script under `scripts/`, change the import path:

```python
from agent_environment_backup.cli import main
```

- [ ] **Step 7: Run all tests**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: rename package to agent_environment_backup with codex shim"
```

---

### Task 11: Claude Code skill

**Files:**
- Create: `skills/claude-code-environment-backup/SKILL.md`
- Modify: `skills/codex-environment-backup/SKILL.md`

- [ ] **Step 1: Create Claude Code skill file**

Create `skills/claude-code-environment-backup/SKILL.md` based on the existing Codex skill. Key differences:

- Name: `claude-code-environment-backup`
- Description triggers: "back up Claude Code environment", "restore Claude Code backup", "check Claude Code backup", "list Claude Code backups", "备份 Claude Code 环境", "恢复 Claude Code 备份", "检查 Claude Code 备份", "列出 Claude Code 备份"
- All CLI invocations use `--profile claude-code`
- Module name is `agent_environment_backup`
- Replace "Codex" with "Claude Code" in all user-facing text
- Replace "CODEX_HOME" with "the Claude Code home directory" and note that default is `~/.claude`
- Remove `codex-fast-proxy` references
- Replace "Close the Codex App" with "Close Claude Code"
- No `agents/` subdirectory

- [ ] **Step 2: Update Codex skill CLI references**

In `skills/codex-environment-backup/SKILL.md`, update:
- `codex_environment_backup` -> `agent_environment_backup`
- Add `--profile codex` to CLI invocations

- [ ] **Step 3: Add test_docs assertions for Claude Code skill**

Add to `tests/test_docs.py`:

```python
def test_claude_code_skill_exists_and_has_profile(self) -> None:
    skill = self.read("skills/claude-code-environment-backup/SKILL.md")
    self.assertIn("--profile claude-code", skill)
    self.assertIn("agent_environment_backup", skill)
    self.assertIn("Claude Code", skill)
    self.assertNotIn("codex_environment_backup", skill)

def test_codex_skill_uses_new_module_name(self) -> None:
    skill = self.read("skills/codex-environment-backup/SKILL.md")
    self.assertIn("agent_environment_backup", skill)
    self.assertIn("--profile codex", skill)
```

- [ ] **Step 4: Run all tests**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/ tests/test_docs.py
git commit -m "feat: add Claude Code skill and update Codex skill for new module name"
```

---

### Task 12: Lifecycle docs

**Files:**
- Create: `.claude/INSTALL.md`, `.claude/UPDATE.md`, `.claude/UNINSTALL.md`
- Modify: `.codex/INSTALL.md`, `.codex/UPDATE.md`, `.codex/UNINSTALL.md`

- [ ] **Step 1: Create .claude/ lifecycle docs**

Create `.claude/INSTALL.md`, `.claude/UPDATE.md`, `.claude/UNINSTALL.md` following the same structure as the `.codex/` versions. Key differences:

- Clone target: `~/.claude/agent-environment-backup`
- Module: `agent_environment_backup`
- All CLI commands include `--profile claude-code`
- Replace "Codex" with "Claude Code" in all user-facing text
- Skill link path: note that Claude Code skill discovery may require manual `settings.json` configuration or plugin registration
- Doctor command: `python -m agent_environment_backup --profile claude-code doctor`
- After-install prompt: "Please restart Claude Code and return to this conversation" / "请重启 Claude Code 并回到这个对话"

- [ ] **Step 2: Update .codex/ lifecycle docs**

In `.codex/INSTALL.md`, `.codex/UPDATE.md`, `.codex/UNINSTALL.md`:
- Replace `codex_environment_backup` with `agent_environment_backup`
- Add `--profile codex` to CLI invocations where appropriate

- [ ] **Step 3: Add test_docs assertions**

Add to `tests/test_docs.py`:

```python
def test_claude_code_lifecycle_docs_exist(self) -> None:
    for name in ("INSTALL.md", "UPDATE.md", "UNINSTALL.md"):
        content = self.read(f".claude/{name}")
        self.assertIn("agent_environment_backup", content)
        self.assertIn("--profile claude-code", content)
        self.assertIn("Claude Code", content)

def test_codex_lifecycle_docs_use_new_module(self) -> None:
    for name in ("INSTALL.md", "UPDATE.md", "UNINSTALL.md"):
        content = self.read(f".codex/{name}")
        self.assertIn("agent_environment_backup", content)
```

- [ ] **Step 4: Run all tests**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add .claude/ .codex/ tests/test_docs.py
git commit -m "docs: add Claude Code lifecycle docs and update Codex docs for new module"
```

---

### Task 13: Update README.md and CLAUDE.md

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update README.md**

Key changes:
- Title: mention both Codex and Claude Code support
- Add `--profile codex|claude-code` to CLI examples
- Replace `codex_environment_backup` with `agent_environment_backup` in command examples
- Add Claude Code section or note alongside existing Codex instructions
- Update install/update/uninstall one-paste prompts to reference both `.codex/` and `.claude/` docs
- Keep existing bilingual structure

- [ ] **Step 2: Update CLAUDE.md**

Update command examples, module name, and architecture description to reflect the rename and profile system.

- [ ] **Step 3: Verify test_docs assertions still pass**

Run: `python -m unittest tests.test_docs -v`
Expected: All PASS (the bilingual structure tests should still hold).

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: update README and CLAUDE.md for multi-profile support"
```

---

### Task 14: Final integration test and .gitignore update

**Files:**
- Modify: `tests/test_core.py`
- Modify: `.gitignore`

- [ ] **Step 1: Add cross-profile warning test**

Add to `tests/test_core.py`:

```python
def test_list_backups_annotates_profile(self) -> None:
    from agent_environment_backup.core import (
        create_backup, list_backups, CODEX_PROFILE, CLAUDE_CODE_PROFILE,
    )
    with self.temp_root() as temp_dir:
        root = Path(temp_dir)
        backup_root = root / "shared-backups"
        codex_home = self.make_home(root)
        claude_home = self.make_claude_code_home(root)
        create_backup(
            codex_home,
            backup_root=backup_root,
            profile=CODEX_PROFILE,
            timestamp="codex-test",
            run_doctor_commands=False,
        )
        create_backup(
            claude_home,
            backup_root=backup_root,
            profile=CLAUDE_CODE_PROFILE,
            timestamp="claude-test",
            run_doctor_commands=False,
        )
        listing = list_backups(backup_root)
        profiles_found = {
            item.get("profile", "codex") for item in listing["backups"]
        }
        self.assertIn("codex", profiles_found)
        self.assertIn("claude-code", profiles_found)
```

- [ ] **Step 2: Update .gitignore**

Add Claude Code backup patterns:

```
claude-code-backup-*/
pre-restore-claude-code-backup-*/
```

- [ ] **Step 3: Run full test suite**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_core.py .gitignore
git commit -m "test: add cross-profile listing test and update .gitignore"
```
