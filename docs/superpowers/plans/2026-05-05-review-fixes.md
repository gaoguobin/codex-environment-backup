# Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 8 issues found in Codex code review of the environment profile implementation.

**Architecture:** Targeted fixes to core.py (standalone script, restore flow, snapshot text), skill docs, and shim package. No new abstractions — just threading existing profile data through paths that were missed.

**Tech Stack:** Python 3.11+ stdlib only. unittest.

---

### Task 1: Fix RESTORE_STANDALONE_PY to read profile from manifest (HIGH #1)

**Files:**
- Modify: `src/agent_environment_backup/core.py` (RESTORE_STANDALONE_PY embedded string, ~L762-1100)
- Modify: `tests/test_core.py`

The standalone script's `resolve_target_home` falls back to `CODEX_HOME` / `~/.codex`. It should read the manifest's `profile` field and choose the correct default home.

- [ ] **Step 1: Write test for Claude Code restore kit subprocess**

Add to `tests/test_core.py`:

```python
def test_standalone_restore_claude_code_uses_claude_home(self) -> None:
    from agent_environment_backup.core import create_backup, CLAUDE_CODE_PROFILE
    with self.temp_root() as temp_dir:
        root = Path(temp_dir)
        home = self.make_claude_code_home(root)
        result = create_backup(
            home,
            backup_root=root / "backups",
            profile=CLAUDE_CODE_PROFILE,
            timestamp="claude-code-standalone-test",
            run_doctor_commands=False,
        )
        dry_target = root / "standalone-dry"
        standalone = subprocess.run(
            [
                sys.executable,
                result["restore_kit"]["restore_py"],
                "--backup-dir",
                result["backup_dir"],
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(standalone.returncode, 0, standalone.stderr)
        output = json.loads(standalone.stdout)
        self.assertTrue(output["dry_run"])
        self.assertIn(".claude", output["target_home"])
        self.assertNotIn(".codex", output["target_home"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_core.CodexEnvironmentBackupTests.test_standalone_restore_claude_code_uses_claude_home -v`
Expected: FAIL — target_home contains `.codex`.

- [ ] **Step 3: Update RESTORE_STANDALONE_PY**

In the embedded `RESTORE_STANDALONE_PY` string in core.py, update `resolve_target_home` to read the manifest profile:

```python
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
```

Update `main()` in the embedded script to read `manifest.get("profile", "codex")` and pass it to `resolve_target_home`:

```python
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
profile = manifest.get("profile", "codex")
target_home = resolve_target_home(args.target_home, profile)
```

Also update the embedded `create_backup` to use the profile from the source manifest for pre-restore backup prefix selection:

```python
pre_prefix = "pre-restore-claude-code-backup" if profile == "claude-code" else "pre-restore-codex-backup"
```

Update the embedded `default_backup_root` similarly:

```python
def default_backup_root(profile: str | None = None) -> Path:
    subdir = "ClaudeCodeBackups" if profile == "claude-code" else "CodexBackups"
    return (Path.home() / "Documents" / subdir).resolve()
```

Update the JSON output key from `"target_codex_home"` to `"target_home"` in the embedded script's dry-run output.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_core.CodexEnvironmentBackupTests.test_standalone_restore_claude_code_uses_claude_home -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_environment_backup/core.py tests/test_core.py
git commit -m "fix: standalone restore reads manifest profile for correct default home"
```

---

### Task 2: Reject cross-profile restore mismatch (HIGH #2)

**Files:**
- Modify: `src/agent_environment_backup/core.py` (restore_backup function, ~L1572)
- Modify: `tests/test_core.py`

- [ ] **Step 1: Write test**

```python
def test_restore_warns_on_profile_mismatch(self) -> None:
    from agent_environment_backup.core import (
        create_backup, restore_backup, CODEX_PROFILE, CLAUDE_CODE_PROFILE,
    )
    with self.temp_root() as temp_dir:
        root = Path(temp_dir)
        claude_home = self.make_claude_code_home(root)
        backup_result = create_backup(
            claude_home,
            backup_root=root / "backups",
            profile=CLAUDE_CODE_PROFILE,
            timestamp="claude-mismatch-test",
            run_doctor_commands=False,
        )
        archive = Path(backup_result["archive"])
        result = restore_backup(archive, root / "target", profile=CODEX_PROFILE)
        self.assertTrue(result["dry_run"])
        self.assertTrue(result.get("profile_mismatch"))
        self.assertEqual(result["profile_mismatch"]["backup_profile"], "claude-code")
        self.assertEqual(result["profile_mismatch"]["restore_profile"], "codex")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_core.CodexEnvironmentBackupTests.test_restore_warns_on_profile_mismatch -v`
Expected: FAIL — no `profile_mismatch` key.

- [ ] **Step 3: Implement profile mismatch warning in restore_backup**

In `restore_backup`, after reading the manifest (~L1592), add:

```python
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
```

Add this to the result dict before the dry-run return and before the apply path. This is a warning, not a hard block — the user may intentionally cross-restore.

- [ ] **Step 4: Run full test suite**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_environment_backup/core.py tests/test_core.py
git commit -m "fix: warn on cross-profile restore mismatch"
```

---

### Task 3: Add shim submodule forwarding (MED #3)

**Files:**
- Create: `src/codex_environment_backup/core.py`
- Create: `src/codex_environment_backup/cli.py`
- Modify: `tests/test_core.py`

- [ ] **Step 1: Write test**

```python
def test_shim_submodule_imports(self) -> None:
    import codex_environment_backup.core as shim_core
    import codex_environment_backup.cli as shim_cli
    import agent_environment_backup.core as real_core
    import agent_environment_backup.cli as real_cli
    self.assertIs(shim_core.create_backup, real_core.create_backup)
    self.assertIs(shim_cli.main, real_cli.main)
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — `codex_environment_backup.core` module not found.

- [ ] **Step 3: Create shim submodules**

Create `src/codex_environment_backup/core.py`:

```python
"""Backward-compatible shim — imports from agent_environment_backup.core."""
from agent_environment_backup.core import *  # noqa: F401,F403
```

Create `src/codex_environment_backup/cli.py`:

```python
"""Backward-compatible shim — imports from agent_environment_backup.cli."""
from agent_environment_backup.cli import *  # noqa: F401,F403
from agent_environment_backup.cli import main  # noqa: F811
```

- [ ] **Step 4: Run full test suite**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_environment_backup/core.py src/codex_environment_backup/cli.py tests/test_core.py
git commit -m "fix: add shim submodule forwarding for codex_environment_backup.core and .cli"
```

---

### Task 4: Thread extra_excluded_dirs through restore path (MED #4)

**Files:**
- Modify: `src/agent_environment_backup/core.py` (restore_plan, copy_backup_files, ~L1525-1569)
- Modify: `tests/test_core.py`

- [ ] **Step 1: Write test**

```python
def test_restore_respects_profile_exclusions(self) -> None:
    from agent_environment_backup.core import (
        create_backup, restore_backup, CLAUDE_CODE_PROFILE,
    )
    with self.temp_root() as temp_dir:
        root = Path(temp_dir)
        home = self.make_claude_code_home(root)
        cache_dir = home / "cache"
        cache_dir.mkdir()
        (cache_dir / "temp.bin").write_text("cached", encoding="utf-8")
        result = create_backup(
            home,
            backup_root=root / "backups",
            profile=CLAUDE_CODE_PROFILE,
            timestamp="excl-test",
            run_doctor_commands=False,
        )
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        paths = {e["relative_path"] for e in manifest["entries"]}
        self.assertNotIn("cache/temp.bin", paths)

        target = root / "restore-target"
        target.mkdir()
        restore_result = restore_backup(
            Path(result["archive"]),
            target,
            backup_root=root / "prebacks",
            profile=CLAUDE_CODE_PROFILE,
            apply=True,
            confirm=True,
        )
        self.assertTrue(restore_result["ok"], restore_result)
        self.assertFalse((target / "cache" / "temp.bin").exists())
```

- [ ] **Step 2: Run test — should already pass for backup exclusion but verify restore path**

Run: `python -m unittest tests.test_core.CodexEnvironmentBackupTests.test_restore_respects_profile_exclusions -v`

- [ ] **Step 3: Update restore_plan and copy_backup_files**

Add `extra_excluded_dirs` parameter to both functions:

```python
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
    ...
    "will_skip_excluded_paths": sorted(EXCLUDED_DIR_NAMES | extra_excluded_dirs),


def copy_backup_files(
    backup_dir: Path,
    target_home: Path,
    extra_excluded_dirs: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    ...
    if is_excluded(relative, extra_excluded_dirs):
    ...
```

In `restore_backup`, pass `frozenset(profile.extra_excluded_dirs)` to both calls:

```python
extra_excluded = frozenset(profile.extra_excluded_dirs)
plan = restore_plan(backup_dir, home, extra_excluded)
...
copy_result = copy_backup_files(backup_dir, home, extra_excluded)
```

Also rename the `codex_home` parameter in `restore_plan` and `copy_backup_files` to `target_home` for consistency.

- [ ] **Step 4: Run full test suite**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_environment_backup/core.py tests/test_core.py
git commit -m "fix: thread extra_excluded_dirs through restore path"
```

---

### Task 5: Fix Claude Code skill doc references (MED #5)

**Files:**
- Modify: `skills/claude-code-environment-backup/SKILL.md`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Add test**

Add to `tests/test_docs.py`:

```python
def test_claude_code_skill_has_no_codex_leftovers(self) -> None:
    skill = self.read("skills/claude-code-environment-backup/SKILL.md")
    self.assertNotIn("--codex-home", skill)
    self.assertNotIn("CODEX_HOME", skill)
    self.assertNotIn("CodexBackups", skill)
    self.assertNotIn("restore-codex-environment", skill)
    self.assertIn("ClaudeCodeBackups", skill)
    self.assertIn("restore-environment.", skill)
    self.assertIn("--home", skill)
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Fix the skill file**

In `skills/claude-code-environment-backup/SKILL.md`:
- Replace `--codex-home` with `--home`
- Replace `CODEX_HOME` with `~/.claude`
- Replace `~/Documents/CodexBackups` with `~/Documents/ClaudeCodeBackups`
- Replace `restore-codex-environment.cmd` with `restore-environment.cmd` (and `.command`, `.sh`)
- Update the home resolution order to: 1. `--home`, 2. `~/.claude`

- [ ] **Step 4: Run full test suite**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/claude-code-environment-backup/SKILL.md tests/test_docs.py
git commit -m "fix: remove Codex-specific references from Claude Code skill doc"
```

---

### Task 6: Generalize remaining hardcoded text (LOW #6)

**Files:**
- Modify: `src/agent_environment_backup/core.py` (write_environment_snapshot, restore_backup messages)

- [ ] **Step 1: Write test**

```python
def test_snapshot_uses_profile_display_name(self) -> None:
    from agent_environment_backup.core import create_backup, CLAUDE_CODE_PROFILE
    with self.temp_root() as temp_dir:
        root = Path(temp_dir)
        home = self.make_claude_code_home(root)
        result = create_backup(
            home,
            backup_root=root / "backups",
            profile=CLAUDE_CODE_PROFILE,
            timestamp="snapshot-text-test",
            run_doctor_commands=False,
        )
        snapshot = (Path(result["backup_dir"]) / "environment-snapshot.txt").read_text(encoding="utf-8")
        self.assertIn("Claude Code", snapshot)
        self.assertNotIn("Codex environment snapshot", snapshot)
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Fix write_environment_snapshot**

Update `write_environment_snapshot` to accept `display_name` parameter:

```python
def write_environment_snapshot(path: Path, doctor_report: dict[str, Any], display_name: str = "Codex") -> None:
    sensitive_note = _make_sensitive_note(display_name)
    lines = [
        f"{display_name} environment snapshot",
        f"Created: {doctor_report['created_at']}",
        f"{display_name} home: {doctor_report['home']}",
        ...
        "",
        sensitive_note,
        ...
    ]
```

Update the call in `create_backup` to pass `profile.display_name`.

Also fix the remaining hardcoded messages in `restore_backup`:
- L1623: `"Dry run only. Close Codex App before restore..."` → `f"Dry run only. Close {profile.display_name} before restore, then rerun with --apply and --i-understand-this-restores-sensitive-state."`
- L1629: `"Restore apply requires --i-understand-this-restores-sensitive-codex-state"` → `"Restore apply requires --i-understand-this-restores-sensitive-state"`
- L1652: `"current CODEX_HOME could not be backed up"` → `f"current {profile.display_name} home could not be backed up completely."`
- L1675: `"Reopen Codex or start a fresh CLI session..."` → `f"Reopen {profile.display_name} or start a fresh CLI session, then run a full doctor check against the restored home."`

- [ ] **Step 4: Run full test suite**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_environment_backup/core.py tests/test_core.py
git commit -m "fix: generalize remaining hardcoded Codex text with profile display_name"
```

---

### Task 7: Fix collision-suffixed backup_name in manifest (LOW #7)

**Files:**
- Modify: `src/agent_environment_backup/core.py` (~L1295-1300)
- Modify: `tests/test_core.py`

- [ ] **Step 1: Write test**

```python
def test_backup_name_matches_directory_after_collision(self) -> None:
    from agent_environment_backup.core import create_backup, CODEX_PROFILE
    with self.temp_root() as temp_dir:
        root = Path(temp_dir)
        home = self.make_home(root)
        backup_root = root / "backups"
        result1 = create_backup(
            home,
            backup_root=backup_root,
            profile=CODEX_PROFILE,
            timestamp="collide-test",
            run_doctor_commands=False,
        )
        result2 = create_backup(
            home,
            backup_root=backup_root,
            profile=CODEX_PROFILE,
            timestamp="collide-test",
            run_doctor_commands=False,
        )
        manifest2 = json.loads(Path(result2["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest2["backup_name"], Path(result2["backup_dir"]).name)
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Fix create_backup**

After the collision loop in `create_backup` (~L1298-1300), reassign `backup_name`:

```python
    while backup_dir.exists():
        backup_dir = root / f"{backup_name}-{suffix}"
        suffix += 1
    backup_name = backup_dir.name  # sync with actual directory name after collision resolution
```

- [ ] **Step 4: Run full test suite**

Run: `python -m unittest discover -s tests -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_environment_backup/core.py tests/test_core.py
git commit -m "fix: sync manifest backup_name with actual directory after collision"
```
