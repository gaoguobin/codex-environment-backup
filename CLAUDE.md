# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Offline backup, restore, and health-check tool for local Codex and Claude Code environments. The `--profile codex|claude-code` flag selects which environment to operate on. Designed to be driven by Codex or Claude Code via natural language; the Python CLI is the deterministic implementation layer.

## Commands

```bash
# Run tests (unittest, no pytest)
python -m unittest discover -s tests

# Run a single test
python -m unittest tests.test_core.CodexEnvironmentBackupTests.test_backup_creates_manifest_and_excludes_live_sqlite_sidecars

# CLI smoke check (Codex, default profile)
python -m agent_environment_backup doctor
python -m agent_environment_backup backup --help

# CLI smoke check (Claude Code profile)
python -m agent_environment_backup --profile claude-code doctor
python -m agent_environment_backup --profile claude-code backup --help
```

No build step, no linter configured, no type checker configured. Zero external dependencies — stdlib only.

## Architecture

**Single-module package** under `src/agent_environment_backup/`:

- `core.py` — All business logic. Four main operations: `doctor_environment`, `create_backup`, `restore_backup`, `list_backups`. Each returns a dict that the CLI layer serializes to JSON. Also contains the embedded `RESTORE_STANDALONE_PY` string — a self-contained restore script included in every backup archive so users can restore without installing the package. The `EnvironmentProfile` frozen dataclass encapsulates per-agent differences (home directory, config inspector, external commands, backup naming, exclusion rules) so each operation is profile-aware without scattered if/else branches. Legacy aliases (`doctor_codex_environment`, `resolve_codex_home`) remain for backward compatibility.
- `cli.py` — argparse wrapper that calls core functions and prints JSON. Adds `--profile codex|claude-code` as a top-level flag. No logic beyond argument parsing and profile selection.
- `__init__.py` — Re-exports core public API and `__version__`.

A thin compatibility shim at `src/codex_environment_backup/` forwards imports and `python -m codex_environment_backup` to the new module.

**Skill integration** (`skills/codex-environment-backup/` and `skills/claude-code-environment-backup/`):

- Each directory has a `SKILL.md` — natural-language contract defining trigger patterns, CLI workflows, and safety model. The Codex skill uses `--profile codex`; the Claude Code skill uses `--profile claude-code`.
- `agents/openai.yaml` — OpenAI agent metadata (Codex skill only).

**Scripts** (`scripts/`) — Thin entry points that prepend subcommand names and forward to `cli.main`. Convenience for direct invocation without `python -m`.

**Lifecycle docs** (`.codex/` and `.claude/`, each with `INSTALL.md`, `UPDATE.md`, `UNINSTALL.md`) — Agent-executable install/update/uninstall instructions. Each set targets its respective environment. `test_docs.py` asserts their structural invariants.

## Key Design Decisions

- **All CLI output is JSON.** Every operation returns `{"ok": bool, ...}`. The skill/agent layer consumes this JSON; human-readable summaries exist only inside backup artifacts.
- **SQLite files use the `sqlite3` online backup API**, not file copy. Integrity is verified with `PRAGMA integrity_check` after backup.
- **Restore is always overlay** — it writes backed-up files on top of the existing environment home without pruning. Excluded paths (`.sandbox*`, `.tmp`, WAL/SHM, plus profile-specific exclusions) are never touched.
- **Pre-restore backup is mandatory** — before apply, the tool backs up the current environment home. If that fails, restore aborts.
- **Doctor has two modes**: structural (default, no subprocess) and command-level (`--run-commands`, spawns profile-specific commands like `codex --version` or `claude --version`). Backup embeds a structural doctor by default.
- **Secrets are never printed** — `redact_text()` strips API keys and tokens from command output. `auth.json` contents are backed up as files but never logged.
- **Archive safety** — `safe_extract_tar` and `safe_extract_zip` reject symlinks and path-traversal members before extraction.
- **Every backup includes a restore kit** — platform-specific scripts (`restore-environment.cmd`, `.ps1`, `.command`, `.sh`) plus `restore-standalone.py` so users without the installed package can restore by double-clicking. Text content is templated with the profile's display name.

## Testing Patterns

Tests use `unittest` with a `temp_root()` context manager that creates per-case directories under `test_tmp_runtime/` and cleans up after. External commands are mocked via `unittest.mock.patch` on `core_module.subprocess.run` and `core_module.shutil.which`. `test_docs.py` asserts structural invariants of documentation files (bilingual sections, Python discovery blocks).

## Line Ending Rules

`.gitattributes` enforces LF for all text except `.ps1` and `.cmd` which use CRLF.
