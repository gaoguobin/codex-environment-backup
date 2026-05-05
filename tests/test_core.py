from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from shutil import rmtree
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_environment_backup.core as core_module  # noqa: E402
from agent_environment_backup.core import (  # noqa: E402
    BackupError,
    create_backup,
    doctor_codex_environment,
    doctor_environment,
    list_backups,
    restore_backup,
)


class CodexEnvironmentBackupTests(unittest.TestCase):
    @contextmanager
    def temp_root(self):
        temp_root = ROOT / "test_tmp_runtime" / "codex-environment-backup-tests"
        path = temp_root / f"case-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=False)
        try:
            yield path
        finally:
            rmtree(path, ignore_errors=True)

    def make_home(self, root: Path) -> Path:
        home = root / "codex-home"
        (home / "sessions").mkdir(parents=True)
        (home / "archived_sessions").mkdir()
        (home / "memories").mkdir()
        (home / "skills").mkdir()
        (home / "plugins").mkdir()
        (home / "rules").mkdir()
        (home / "automations").mkdir()
        (home / ".sandbox").mkdir()
        (home / ".sandbox-bin").mkdir()
        (home / ".sandbox-secrets").mkdir()
        (home / ".tmp").mkdir()
        (home / "tmp").mkdir()
        (home / "history.jsonl").write_text('{"event":"demo"}\n', encoding="utf-8")
        (home / "hooks.json").write_text('{"hooks":[]}\n', encoding="utf-8")
        (home / "config.toml").write_text(
            """
model_provider = "demo"

[model_providers.demo]
base_url = "https://example.invalid/v1"
env_key = "DEMO_API_KEY"
service_tier = "auto"
""".strip()
            + "\n",
            encoding="utf-8",
        )
        (home / "auth.json").write_text('{"access_token":"FAKE-TOKEN-123"}\n', encoding="utf-8")

        self.make_sqlite(home / "logs_2.sqlite", "logs")
        self.make_sqlite(home / "state_5.sqlite", "state")
        (home / "state_5.sqlite-wal").write_text("live wal", encoding="utf-8")
        (home / "logs_2.sqlite-shm").write_text("live shm", encoding="utf-8")
        return home

    def make_sqlite(self, path: Path, table_name: str) -> None:
        conn = sqlite3.connect(path)
        try:
            conn.execute(f"create table {table_name}(id integer primary key, value text)")
            conn.execute(f"insert into {table_name}(value) values (?)", (f"{table_name}-row",))
            conn.commit()
        finally:
            conn.close()

    def test_backup_creates_manifest_and_excludes_live_sqlite_sidecars(self) -> None:
        with self.temp_root() as temp_dir:
            root = Path(temp_dir)
            home = self.make_home(root)
            backup_root = root / "backups"

            result = create_backup(
                home,
                backup_root=backup_root,
                timestamp="codex-backup-test",
                run_doctor_commands=False,
            )

            self.assertTrue(result["ok"], result)
            self.assertTrue(Path(result["archive"]).exists())
            self.assertTrue(Path(result["sha256_file"]).exists())
            for helper_path in result["restore_kit"].values():
                self.assertTrue(Path(helper_path).exists(), helper_path)
            restore_ps1 = Path(result["restore_kit"]["restore_ps1"]).read_text(encoding="utf-8")
            restore_sh = Path(result["restore_kit"]["restore_sh"]).read_text(encoding="utf-8")
            self.assertIn("$LASTEXITCODE", restore_ps1)
            self.assertIn("continue", restore_ps1)
            self.assertIn("for candidate in python3 python", restore_sh)
            self.assertIn('command -v "$candidate"', restore_sh)
            with tarfile.open(result["archive"], "r:gz") as archive:
                names = {Path(member.name).name for member in archive.getmembers()}
            self.assertIn("RESTORE.md", names)
            self.assertIn("RESTORE_INSTRUCTIONS.txt", names)
            self.assertIn("restore-environment.cmd", names)
            self.assertIn("restore-environment.ps1", names)
            self.assertIn("restore-environment.command", names)
            self.assertIn("restore-environment.sh", names)
            self.assertIn("restore-standalone.py", names)
            standalone = subprocess.run(
                [
                    sys.executable,
                    result["restore_kit"]["restore_py"],
                    "--backup-dir",
                    result["backup_dir"],
                    "--codex-home",
                    str(root / "standalone-dry-run-target"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(standalone.returncode, 0, standalone.stderr)
            self.assertIn('"dry_run": true', standalone.stdout)

            standalone_target = root / "standalone-restore-target"
            standalone_target.mkdir()
            (standalone_target / "config.toml").write_text("old config", encoding="utf-8")
            standalone_prebacks = root / "standalone-prebacks"
            standalone_apply = subprocess.run(
                [
                    sys.executable,
                    result["restore_kit"]["restore_py"],
                    "--backup-dir",
                    result["backup_dir"],
                    "--codex-home",
                    str(standalone_target),
                    "--backup-root",
                    str(standalone_prebacks),
                    "--apply",
                    "--confirm",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(standalone_apply.returncode, 0, standalone_apply.stderr)
            pre_restore_dirs = sorted(standalone_prebacks.glob("pre-restore-codex-backup-*"))
            self.assertTrue(pre_restore_dirs, standalone_apply.stdout)
            pre_restore_dir = pre_restore_dirs[0]
            for helper_name in (
                "RESTORE.md",
                "RESTORE_INSTRUCTIONS.txt",
                "restore-environment.cmd",
                "restore-environment.ps1",
                "restore-environment.command",
                "restore-environment.sh",
                "restore-standalone.py",
            ):
                self.assertTrue((pre_restore_dir / helper_name).exists(), helper_name)

            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            paths = {entry["relative_path"] for entry in manifest["entries"]}
            self.assertIn("config.toml", paths)
            self.assertIn("hooks.json", paths)
            self.assertIn("history.jsonl", paths)
            self.assertIn("logs_2.sqlite", paths)
            self.assertIn("state_5.sqlite", paths)
            self.assertNotIn(".sandbox-secrets", "".join(paths))
            self.assertFalse(any(path.endswith("-wal") or path.endswith("-shm") for path in paths))
            self.assertEqual(manifest["counts"]["sqlite_databases"], 2)

            checks = json.loads(Path(result["sqlite_integrity"]).read_text(encoding="utf-8"))
            self.assertTrue(all(check["ok"] for check in checks), checks)

            report = doctor_codex_environment(home, run_commands=False)
            report_json = json.dumps(report)
            self.assertNotIn("FAKE-TOKEN-123", report_json)

    def test_backup_records_walk_errors_and_reports_not_ok(self) -> None:
        with self.temp_root() as temp_dir:
            root = Path(temp_dir)
            home = self.make_home(root)
            backup_root = root / "backups"
            original_walk = core_module.os.walk

            def fake_walk(top, *args, **kwargs):
                top_path = Path(top)
                if top_path == home:
                    yield str(home), ["sessions"], ["config.toml"]
                    onerror = kwargs.get("onerror")
                    if onerror is not None:
                        onerror(PermissionError(13, "Access denied", str(home / "sessions")))
                    return
                yield from original_walk(top, *args, **kwargs)

            with mock.patch.object(core_module.os, "walk", side_effect=fake_walk):
                result = create_backup(
                    home,
                    backup_root=backup_root,
                    timestamp="codex-backup-walk-error",
                    run_doctor_commands=False,
                )

            self.assertFalse(result["ok"], result)
            self.assertEqual(result["counts"]["errors"], 1)
            self.assertEqual(result["errors"][0]["method"], "walk")
            self.assertEqual(result["errors"][0]["relative_path"], "sessions")
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["counts"]["errors"], 1)
            self.assertTrue(Path(result["archive"]).exists())

    def test_restore_dry_run_and_apply_overlay(self) -> None:
        with self.temp_root() as temp_dir:
            root = Path(temp_dir)
            source_home = self.make_home(root)
            backup_result = create_backup(
                source_home,
                backup_root=root / "backups",
                timestamp="codex-backup-test",
                run_doctor_commands=False,
            )
            archive = Path(backup_result["archive"])

            dry_run_target = root / "dry-run-home"
            dry_run = restore_backup(archive, dry_run_target)
            self.assertTrue(dry_run["dry_run"])
            self.assertFalse(dry_run_target.exists())

            target_home = root / "restored-home"
            (target_home / ".sandbox-secrets").mkdir(parents=True)
            (target_home / ".sandbox-secrets" / "keep.txt").write_text("keep", encoding="utf-8")
            (target_home / "config.toml").write_text("old config", encoding="utf-8")
            (target_home / "old.txt").write_text("old", encoding="utf-8")

            restore_result = restore_backup(
                archive,
                target_home,
                backup_root=root / "prebacks",
                apply=True,
                confirm=True,
            )

            self.assertTrue(restore_result["ok"], restore_result)
            self.assertTrue(restore_result["pre_restore_backup"])
            self.assertEqual(restore_result["post_restore_doctor_mode"], "structural")
            self.assertFalse(restore_result["post_restore_doctor"]["command_summary"]["run"])
            self.assertEqual(
                (target_home / "config.toml").read_text(encoding="utf-8"),
                (source_home / "config.toml").read_text(encoding="utf-8"),
            )
            self.assertTrue((target_home / ".sandbox-secrets" / "keep.txt").exists())
            self.assertTrue((target_home / "old.txt").exists())
            self.assertTrue(restore_result["restore"]["restored_files"] >= 1)

    def test_restore_aborts_when_pre_restore_backup_is_incomplete(self) -> None:
        with self.temp_root() as temp_dir:
            root = Path(temp_dir)
            source_home = self.make_home(root)
            backup_result = create_backup(
                source_home,
                backup_root=root / "backups",
                timestamp="codex-backup-test",
                run_doctor_commands=False,
            )
            archive = Path(backup_result["archive"])

            target_home = root / "target-home"
            target_home.mkdir()
            (target_home / "config.toml").write_text("old config", encoding="utf-8")
            self.make_sqlite(target_home / "broken.sqlite", "broken")

            original_sqlite_backup = core_module.backup_sqlite_database

            def fail_for_broken_sqlite(source: Path, destination: Path) -> None:
                if source.name == "broken.sqlite":
                    raise RuntimeError("simulated sqlite backup failure")
                original_sqlite_backup(source, destination)

            with mock.patch.object(
                core_module,
                "backup_sqlite_database",
                side_effect=fail_for_broken_sqlite,
            ):
                restore_result = restore_backup(
                    archive,
                    target_home,
                    backup_root=root / "prebacks",
                    apply=True,
                    confirm=True,
                )

            self.assertFalse(restore_result["ok"], restore_result)
            self.assertFalse(restore_result["pre_restore_backup"]["ok"])
            self.assertEqual(restore_result["restore"]["restored_files"], 0)
            self.assertEqual(
                restore_result["restore"]["errors"][0]["error"],
                "pre_restore_backup_failed",
            )
            self.assertEqual(
                (target_home / "config.toml").read_text(encoding="utf-8"),
                "old config",
            )

    def test_list_backups_includes_restore_backups(self) -> None:
        with self.temp_root() as temp_dir:
            root = Path(temp_dir)
            source_home = self.make_home(root)
            backup_root = root / "backups"
            backup_result = create_backup(
                source_home,
                backup_root=backup_root,
                timestamp="codex-backup-test",
                run_doctor_commands=False,
            )
            archive = Path(backup_result["archive"])
            target_home = root / "restored-home"
            target_home.mkdir()
            (target_home / "config.toml").write_text("old config", encoding="utf-8")
            restore_backup(
                archive,
                target_home,
                backup_root=backup_root,
                apply=True,
                confirm=True,
            )

            listing = list_backups(backup_root)
            backup_dirs = {Path(item["backup_dir"]).name for item in listing["backups"]}
            self.assertIn("codex-backup-test", backup_dirs)
            self.assertTrue(any(name.startswith("pre-restore-codex-backup-") for name in backup_dirs))

    def test_list_backups_summarizes_legacy_manifest(self) -> None:
        with self.temp_root() as temp_dir:
            root = Path(temp_dir)
            backup_root = root / "backups"
            legacy = backup_root / "codex-backup-legacy"
            legacy.mkdir(parents=True)
            (legacy / "config.toml").write_text("model = 'demo'\n", encoding="utf-8")
            (legacy / "logs_2.sqlite").write_text("placeholder", encoding="utf-8")
            (legacy / "manifest.json").write_text(
                json.dumps(
                    {
                        "generated_at": "2026-05-03T12:44:27+08:00",
                        "included_root_files": ["config.toml"],
                        "included_directories": ["sessions"],
                        "sqlite_online_backup": ["logs_2.sqlite"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            listing = list_backups(backup_root)
            item = listing["backups"][0]

            self.assertEqual(item["status"], "legacy_manifest")
            self.assertEqual(item["created_at"], "2026-05-03T12:44:27+08:00")
            self.assertGreaterEqual(item["files"], 2)
            self.assertEqual(item["sqlite_databases"], 1)
            self.assertEqual(item["errors"], 0)
            self.assertEqual(item["legacy_summary"]["root_files"], 1)

    def test_restore_apply_requires_confirmation(self) -> None:
        with self.temp_root() as temp_dir:
            root = Path(temp_dir)
            source_home = self.make_home(root)
            backup_result = create_backup(
                source_home,
                backup_root=root / "backups",
                timestamp="codex-backup-test",
                run_doctor_commands=False,
            )
            archive = Path(backup_result["archive"])
            with self.assertRaises(BackupError):
                restore_backup(archive, root / "target", apply=True, confirm=False)

    def test_restore_rejects_tar_symlink_members(self) -> None:
        with self.temp_root() as temp_dir:
            root = Path(temp_dir)
            archive_path = root / "malicious.tar"
            manifest = b'{"schema_version":1,"counts":{"files":0}}\n'

            with tarfile.open(archive_path, "w") as archive:
                manifest_info = tarfile.TarInfo("malicious-backup/manifest.json")
                manifest_info.size = len(manifest)
                archive.addfile(manifest_info, io.BytesIO(manifest))

                files_info = tarfile.TarInfo("malicious-backup/files")
                files_info.type = tarfile.DIRTYPE
                archive.addfile(files_info)

                link_info = tarfile.TarInfo("malicious-backup/files/link")
                link_info.type = tarfile.SYMTYPE
                link_info.linkname = "../outside"
                archive.addfile(link_info)

            with self.assertRaises(BackupError):
                restore_backup(archive_path, root / "target")

    def test_doctor_commands_scope_to_requested_codex_home(self) -> None:
        with self.temp_root() as temp_dir:
            root = Path(temp_dir)
            home = self.make_home(root)
            seen_envs: list[dict[str, str] | None] = []

            def fake_which(command: str, path: str | None = None) -> str | None:
                if command == "codex":
                    return r"C:\\fake\\codex.exe"
                return None

            def fake_run(command, **kwargs):
                seen_envs.append(kwargs.get("env"))
                return subprocess.CompletedProcess(command, 0, "ok", "")

            with (
                mock.patch.object(core_module.shutil, "which", side_effect=fake_which),
                mock.patch.object(core_module.subprocess, "run", side_effect=fake_run),
                mock.patch.object(core_module.importlib.util, "find_spec", return_value=None),
            ):
                report = doctor_codex_environment(home, run_commands=True)

            self.assertTrue(report["ok"], report)
            self.assertGreaterEqual(len(seen_envs), 2)
            self.assertTrue(all(env and env.get("CODEX_HOME") == str(home) for env in seen_envs))

    def test_doctor_command_failures_are_visible(self) -> None:
        with self.temp_root() as temp_dir:
            root = Path(temp_dir)
            home = self.make_home(root)

            def fake_which(command: str, path: str | None = None) -> str | None:
                if command == "codex":
                    return r"C:\\fake\\codex.exe"
                return None

            def fake_run(command, **kwargs):
                if command == ["codex", "mcp", "list"]:
                    return subprocess.CompletedProcess(command, 1, "", "access denied")
                return subprocess.CompletedProcess(command, 0, "ok", "")

            with (
                mock.patch.object(core_module.shutil, "which", side_effect=fake_which),
                mock.patch.object(core_module.subprocess, "run", side_effect=fake_run),
                mock.patch.object(core_module.importlib.util, "find_spec", return_value=None),
            ):
                report = doctor_codex_environment(home, run_commands=True)

            self.assertFalse(report["ok"], report)
            self.assertTrue(report["core_ok"])
            self.assertTrue(report["path_scan_ok"])
            self.assertFalse(report["command_ok"])
            failed_names = {item["name"] for item in report["command_summary"]["failed"]}
            self.assertIn("codex_mcp_list", failed_names)

    def test_fast_proxy_doctor_output_is_summarized(self) -> None:
        with self.temp_root() as temp_dir:
            root = Path(temp_dir)
            home = self.make_home(root)

            def fake_which(command: str, path: str | None = None) -> str | None:
                if command == "codex":
                    return r"C:\\fake\\codex.exe"
                return None

            def fake_run(command, **kwargs):
                if "codex_fast_proxy" in command:
                    payload = {
                        "ok": True,
                        "status": "running",
                        "provider": "private-provider",
                        "base_url": "https://private.example/v1",
                        "log": r"C:\\Users\\example\\.codex\\state\\fast_proxy.jsonl",
                        "health": {
                            "ok": True,
                            "service_tier": "priority",
                            "upstream_base": "https://private.example/v1",
                            "runtime_id": "secret-runtime-id",
                        },
                    }
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                return subprocess.CompletedProcess(command, 0, "ok", "")

            with (
                mock.patch.object(core_module.shutil, "which", side_effect=fake_which),
                mock.patch.object(core_module.subprocess, "run", side_effect=fake_run),
                mock.patch.object(core_module.importlib.util, "find_spec", return_value=object()),
            ):
                report = doctor_codex_environment(home, run_commands=True)

            encoded = json.dumps(report)
            self.assertNotIn("private-provider", encoded)
            self.assertNotIn("https://private.example/v1", encoded)
            self.assertNotIn("secret-runtime-id", encoded)
            status_result = report["commands"]["codex_fast_proxy_status"]
            self.assertNotIn("stdout", status_result)
            self.assertTrue(status_result["stdout_summary"]["provider_present"])
            self.assertTrue(status_result["stdout_summary"]["base_url_present"])

    def test_profile_registry_contains_both_profiles(self) -> None:
        from agent_environment_backup.core import PROFILES, CODEX_PROFILE, CLAUDE_CODE_PROFILE
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

    def test_resolve_home_uses_profile_default(self) -> None:
        from agent_environment_backup.core import resolve_home, CODEX_PROFILE, CLAUDE_CODE_PROFILE
        env_without_codex_home = {k: v for k, v in os.environ.items() if k != "CODEX_HOME"}
        with mock.patch.dict(os.environ, env_without_codex_home, clear=True):
            codex_home = resolve_home(CODEX_PROFILE)
            claude_home = resolve_home(CLAUDE_CODE_PROFILE)
        self.assertEqual(codex_home, (Path.home() / ".codex").resolve())
        self.assertEqual(claude_home, (Path.home() / ".claude").resolve())

    def test_resolve_home_respects_env_var(self) -> None:
        from agent_environment_backup.core import resolve_home, CODEX_PROFILE, CLAUDE_CODE_PROFILE
        with mock.patch.dict(os.environ, {"CODEX_HOME": "/tmp/custom-codex"}):
            result = resolve_home(CODEX_PROFILE)
        self.assertEqual(result, Path("/tmp/custom-codex").resolve())
        env_without_codex_home = {k: v for k, v in os.environ.items() if k != "CODEX_HOME"}
        with mock.patch.dict(os.environ, env_without_codex_home, clear=True):
            result = resolve_home(CLAUDE_CODE_PROFILE)
        self.assertEqual(result, (Path.home() / ".claude").resolve())

    def test_resolve_home_override_takes_precedence(self) -> None:
        from agent_environment_backup.core import resolve_home, CODEX_PROFILE
        with mock.patch.dict(os.environ, {"CODEX_HOME": "/tmp/env"}):
            result = resolve_home(CODEX_PROFILE, "/tmp/explicit")
        self.assertEqual(result, Path("/tmp/explicit").resolve())

    def test_default_backup_root_uses_profile(self) -> None:
        from agent_environment_backup.core import default_backup_root, CODEX_PROFILE, CLAUDE_CODE_PROFILE
        codex_root = default_backup_root(CODEX_PROFILE)
        claude_root = default_backup_root(CLAUDE_CODE_PROFILE)
        self.assertTrue(str(codex_root).endswith("CodexBackups"))
        self.assertTrue(str(claude_root).endswith("ClaudeCodeBackups"))

    def test_inspect_claude_code_config(self) -> None:
        from agent_environment_backup.core import inspect_claude_code_config
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
        from agent_environment_backup.core import inspect_claude_code_config
        with self.temp_root() as temp_dir:
            home = Path(temp_dir) / "claude-home"
            home.mkdir()
            result = inspect_claude_code_config(home)
            self.assertFalse(result["present"])

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
        from agent_environment_backup.core import doctor_environment, CLAUDE_CODE_PROFILE
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
            self.assertEqual(report["profile"], "claude-code")
            report_json = json.dumps(report)
            self.assertNotIn("FAKE-CLAUDE-TOKEN", report_json)

    def test_backup_claude_code_profile(self) -> None:
        from agent_environment_backup.core import create_backup, CLAUDE_CODE_PROFILE
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

    def test_restore_claude_code_profile_dry_run_and_apply(self) -> None:
        from agent_environment_backup.core import (
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

    def test_cli_doctor_is_structural_by_default(self) -> None:
        from agent_environment_backup.cli import build_parser

        args = build_parser().parse_args(["doctor"])
        self.assertFalse(args.run_commands)
        self.assertFalse(args.no_run_commands)

    def test_cli_doctor_supports_explicit_command_probe(self) -> None:
        from agent_environment_backup.cli import build_parser

        args = build_parser().parse_args(["doctor", "--run-commands"])
        self.assertTrue(args.run_commands)
        self.assertFalse(args.no_run_commands)

    def test_cli_profile_argument_default(self) -> None:
        from agent_environment_backup.cli import build_parser
        args = build_parser().parse_args(["doctor"])
        self.assertEqual(args.profile, "codex")

    def test_cli_profile_argument_claude_code(self) -> None:
        from agent_environment_backup.cli import build_parser
        args = build_parser().parse_args(["--profile", "claude-code", "doctor"])
        self.assertEqual(args.profile, "claude-code")

    def test_cli_home_argument_and_codex_home_alias(self) -> None:
        from agent_environment_backup.cli import build_parser
        args = build_parser().parse_args(["doctor", "--home", "/tmp/test"])
        self.assertEqual(args.home, "/tmp/test")
        args2 = build_parser().parse_args(["doctor", "--codex-home", "/tmp/test2"])
        self.assertEqual(args2.home, "/tmp/test2")

    def test_shim_package_reexports(self) -> None:
        import codex_environment_backup as shim
        import agent_environment_backup as main_pkg
        self.assertIs(shim.BackupError, main_pkg.BackupError)
        self.assertIs(shim.create_backup, main_pkg.create_backup)
        self.assertIs(shim.CODEX_PROFILE, main_pkg.CODEX_PROFILE)
        self.assertIs(shim.CLAUDE_CODE_PROFILE, main_pkg.CLAUDE_CODE_PROFILE)
        self.assertIs(shim.resolve_home, main_pkg.resolve_home)

    def test_shim_submodule_imports(self) -> None:
        import codex_environment_backup.core as shim_core
        import codex_environment_backup.cli as shim_cli
        import agent_environment_backup.core as real_core
        import agent_environment_backup.cli as real_cli
        self.assertIs(shim_core.create_backup, real_core.create_backup)
        self.assertIs(shim_cli.main, real_cli.main)

    def test_cli_restore_confirm_flag_and_alias(self) -> None:
        from agent_environment_backup.cli import build_parser
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

    def test_restore_kit_uses_profile_display_name(self) -> None:
        from agent_environment_backup.core import create_backup, CLAUDE_CODE_PROFILE
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
                item.get("profile") for item in listing["backups"]
            }
            self.assertIn("codex", profiles_found)
            self.assertIn("claude-code", profiles_found)

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


    # -- Fix 2: Cross-profile restore mismatch warning --
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

    # -- Fix 4: Thread extra_excluded_dirs through restore --
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

    # -- Fix 6: Generalize remaining hardcoded text --
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

    # -- Fix 7: Collision-suffixed backup_name --
    def test_backup_name_matches_directory_after_collision(self) -> None:
        from agent_environment_backup.core import create_backup, CODEX_PROFILE
        with self.temp_root() as temp_dir:
            root = Path(temp_dir)
            home = self.make_home(root)
            backup_root = root / "backups"
            result1 = create_backup(
                home, backup_root=backup_root, profile=CODEX_PROFILE,
                timestamp="collide-test", run_doctor_commands=False,
            )
            result2 = create_backup(
                home, backup_root=backup_root, profile=CODEX_PROFILE,
                timestamp="collide-test", run_doctor_commands=False,
            )
            manifest2 = json.loads(Path(result2["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest2["backup_name"], Path(result2["backup_dir"]).name)


if __name__ == "__main__":
    unittest.main()
