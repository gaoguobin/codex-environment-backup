# Environment Profile Abstraction

Date: 2026-05-05

## Goal

Make the existing codex-environment-backup codebase support both Codex and Claude Code environments through a lightweight profile abstraction. Existing Codex behavior remains the default and unchanged.

## Approach

A frozen dataclass `EnvironmentProfile` encapsulates all environment-specific differences. Two pre-defined instances (`CODEX_PROFILE`, `CLAUDE_CODE_PROFILE`) cover the two supported environments. All core functions accept an optional `profile` parameter; when omitted, `CODEX_PROFILE` is used for backward compatibility.

## Rename

The package and module are renamed from Codex-specific to generic:

- Package: `codex-environment-backup` -> `agent-environment-backup`
- Module: `codex_environment_backup` -> `agent_environment_backup`
- CLI entry: `agent-environment-backup`
- `python -m agent_environment_backup` replaces `python -m codex_environment_backup`

A shim package `src/codex_environment_backup/` is retained that re-exports everything from `agent_environment_backup`. This preserves `python -m codex_environment_backup`, existing imports, and installed Codex skill references until users update.

Implementation order: profile abstraction and tests first, then rename with shim, to avoid breaking existing installs mid-flight.

## EnvironmentProfile dataclass

```python
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

`env_home_var` is `str | None`. When `None`, resolve_home skips environment variable lookup and falls through to the default path.

`extra_excluded_dirs` allows profiles to add exclusions beyond the global `EXCLUDED_DIR_NAMES` set.

### CODEX_PROFILE

Exact reproduction of current hardcoded values:

- `name`: `"codex"`
- `display_name`: `"Codex"`
- `default_home_dir`: `".codex"`
- `env_home_var`: `"CODEX_HOME"`
- `backup_prefix`: `"codex-backup"`
- `pre_restore_prefix`: `"pre-restore-codex-backup"`
- `default_backup_subdir`: `"CodexBackups"`
- `important_paths`: `("auth.json", "hooks.json", "history.jsonl", "sessions", "archived_sessions", "memories", "skills", "plugins", "rules", "automations", "codex-fast-proxy-state")`
- `config_file`: `"config.toml"`
- `config_inspector`: `inspect_codex_config` (current `inspect_config` function)
- `commands`: `(("codex", "--version"), ("codex", "mcp", "list"))`
- `integration_module`: `"codex_fast_proxy"`
- `extra_excluded_dirs`: `()`

### CLAUDE_CODE_PROFILE

- `name`: `"claude-code"`
- `display_name`: `"Claude Code"`
- `default_home_dir`: `".claude"`
- `env_home_var`: `None` (Claude Code does not use a `CLAUDE_HOME` env var)
- `backup_prefix`: `"claude-code-backup"`
- `pre_restore_prefix`: `"pre-restore-claude-code-backup"`
- `default_backup_subdir`: `"ClaudeCodeBackups"`
- `important_paths`: `("settings.json", "settings.local.json", "credentials.json", "statsig", "projects", "memory", "todos", "plugins", "keybindings.json")`
- `config_file`: `"settings.json"`
- `config_inspector`: `inspect_claude_code_config`
- `commands`: `(("claude", "--version"), ("claude", "mcp", "list"))`
- `integration_module`: `None`
- `extra_excluded_dirs`: `("cache",)`

## Profile registry

A module-level dict `PROFILES` maps name to instance:

```python
PROFILES: dict[str, EnvironmentProfile] = {
    "codex": CODEX_PROFILE,
    "claude-code": CLAUDE_CODE_PROFILE,
}
```

CLI uses this to resolve `--profile` argument. Default is `"codex"` for backward compatibility. Each skill explicitly passes its profile, so the user experience is automatic profile selection based on which agent context they are in.

## Core function changes

### resolve_home

New function `resolve_home(profile, home_override)` replaces `resolve_codex_home`. Resolution order:

1. Explicit `home_override` argument
2. `os.environ.get(profile.env_home_var)` if `env_home_var` is not None
3. `Path.home() / profile.default_home_dir`

`resolve_codex_home` becomes a backward-compatible alias that calls `resolve_home(CODEX_PROFILE, ...)`.

### default_backup_root

Takes a profile and returns `Path.home() / "Documents" / profile.default_backup_subdir`.

### is_excluded

Extended to accept an optional `extra_excluded_dirs` set from the profile, merged with the global `EXCLUDED_DIR_NAMES`.

### doctor_environment

Replaces `doctor_codex_environment`. Uses `profile.important_paths` for the path scan, `profile.config_inspector` for config analysis, `profile.commands` for external command probes, and `profile.integration_module` for optional integration checks.

`doctor_codex_environment` becomes a backward-compatible alias.

### create_backup

Uses `profile.backup_prefix` for naming. Passes `profile` through to `doctor_environment` and `default_backup_root`.

### restore_backup

Uses `profile.pre_restore_prefix` for pre-restore backup naming. Passes `profile` through to `create_backup`, `doctor_environment`, and `default_backup_root`.

### list_backups

No profile-specific logic beyond `default_backup_root`. Takes `profile` for consistency. When listing, manifests that contain a `profile` field are annotated; manifests without it are treated as `"codex"`.

### Restore kit and RESTORE_STANDALONE_PY

The embedded standalone restore script does not need profile awareness. It receives `--backup-dir` and `--target-home` (renamed from `--codex-home`) and overlays files. The profile-specific logic (which directory to back up, naming) is handled at backup creation time.

Restore kit filenames are generalized to `restore-environment.*` (dropping the `codex-` prefix). Text content in restore scripts and instructions uses `profile.display_name` for user-facing messages (e.g., "Close the Codex App" becomes "Close the {display_name} app").

## Manifest changes

`manifest.json` gains a `profile` field recording the profile name used at backup time:

```json
{
  "schema_version": 1,
  "profile": "codex",
  ...
}
```

Manifests without a `profile` field are treated as `"codex"` (backward compat with existing backups).

## Hardcoded text generalization

The following hardcoded references are templated using `profile.display_name`:

- `SENSITIVE_NOTE` constant — "This backup can contain {display_name} history..."
- `write_environment_snapshot` — "{display_name} environment snapshot"
- `backup-summary.txt` — "{display_name} environment backup"
- Restore kit instructions — "Close the {display_name} app..."
- `RESTORE_STANDALONE_PY` — user-facing strings only; internal variable names stay generic

## Config inspectors

### inspect_codex_config(home: Path) -> dict

Current `inspect_config` function renamed. Parses `config.toml` with `tomllib`, reports model_provider, model_providers, base_url, env_key, service_tier, hooks_enabled.

### inspect_claude_code_config(home: Path) -> dict

New function. Parses `settings.json` with `json.loads`. Reports:

- `permissions` present and count
- `env` vars present
- `hooks` present and count
- `model` setting
- `theme` setting
- `allowedTools` present

## CLI changes

Top-level argument added before subcommands:

```
agent-environment-backup [--profile codex|claude-code] <command> [options]
```

Default profile: `codex`.

The `--codex-home` argument is renamed to `--home` across all subcommands. `--codex-home` remains accepted as an alias for backward compatibility.

The `--i-understand-this-restores-sensitive-codex-state` flag is renamed to `--i-understand-this-restores-sensitive-state`. The old flag remains accepted as an alias.

## JSON output key changes

Output keys are generalized:

- `codex_home` -> `home` (in doctor, manifest, restore output)
- `target_codex_home` -> `target_home` (in restore output)

Old key names are not emitted. This is acceptable because the JSON consumer is the skill layer (not external tooling), and both skills update simultaneously.

## Skill integration

### Existing Codex skill (updated)

`skills/codex-environment-backup/` — SKILL.md and agents/openai.yaml remain. CLI references in SKILL.md update to new module name with explicit `--profile codex`.

### New Claude Code skill

`skills/claude-code-environment-backup/SKILL.md`:

- Trigger patterns: "back up Claude Code environment", "restore Claude Code backup", "check Claude Code backup health", "list Claude Code backups", and Chinese equivalents
- CLI invocations use `--profile claude-code`
- Safety model and result handling mirror Codex skill
- No `agents/` subdirectory needed

Each skill explicitly passes `--profile`, so users experience automatic profile selection based on their agent context.

## Lifecycle docs

### Existing `.codex/` docs (updated)

Update CLI references from `codex_environment_backup` to `agent_environment_backup`. Add `--profile codex` where appropriate.

### New `.claude/` docs

`.claude/INSTALL.md`, `.claude/UPDATE.md`, `.claude/UNINSTALL.md`:

- Clone target: `~/.claude/agent-environment-backup`
- pip install same package
- Skill link for Claude Code (path TBD based on Claude Code's skill discovery mechanism; may use `~/.claude/plugins/` or manual configuration in `settings.json`)
- Doctor command: `python -m agent_environment_backup --profile claude-code doctor`

## Testing

### Existing tests

All existing tests remain, updated for new module name. They use `CODEX_PROFILE` either explicitly or via default.

### New tests

- `test_claude_code_profile`: Constructs a fake `~/.claude` home with `settings.json`, `credentials.json`, `projects/`, `memory/`, etc. Runs backup, doctor, restore, list-backups with `CLAUDE_CODE_PROFILE`. Asserts correct paths, prefixes, and inspector behavior.
- `test_profile_registry`: Asserts `PROFILES` contains both profiles, default resolution works.
- `test_cross_profile_restore_rejected`: Verifies that restoring a claude-code backup with codex profile (or vice versa) produces a clear warning in the result.
- `test_shim_package_imports`: Verifies `import codex_environment_backup` still works and re-exports correctly.
- `test_docs.py` additions: Structural assertions for `.claude/` lifecycle docs.

### Test infrastructure

`make_home` helper becomes profile-aware: `make_codex_home` (current) and `make_claude_code_home` (new).

## Backward compatibility

- `resolve_codex_home`, `doctor_codex_environment` remain as importable aliases in `agent_environment_backup`
- `src/codex_environment_backup/` shim package re-exports all public API from `agent_environment_backup`
- `python -m codex_environment_backup` continues to work via shim
- `__init__.py` exports both old and new names
- Default profile is `codex` everywhere
- Codex skill and `.codex/` lifecycle docs continue to work
- The `--codex-home` and `--i-understand-this-restores-sensitive-codex-state` CLI arguments are accepted as aliases
- Manifests without `profile` field are treated as `"codex"`

## Files changed

- `src/agent_environment_backup/core.py` — profile dataclass, profile instances, config inspectors, function signature changes
- `src/agent_environment_backup/cli.py` — `--profile` and `--home` arguments
- `src/agent_environment_backup/__init__.py` — re-exports with aliases
- `src/agent_environment_backup/__main__.py` — module rename only
- `src/codex_environment_backup/` — shim package (re-exports + `__main__.py` forwarding)
- `pyproject.toml` — package name, module path, entry point
- `skills/codex-environment-backup/SKILL.md` — CLI references updated
- `skills/claude-code-environment-backup/SKILL.md` — new
- `.codex/INSTALL.md`, `.codex/UPDATE.md`, `.codex/UNINSTALL.md` — CLI references updated
- `.claude/INSTALL.md`, `.claude/UPDATE.md`, `.claude/UNINSTALL.md` — new
- `tests/test_core.py` — module rename, new profile tests
- `tests/test_docs.py` — new structural assertions
- `scripts/*.py` — module rename
- `CLAUDE.md` — update
- `README.md` — update
