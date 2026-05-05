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

import codex_environment_backup.core as core_module  # noqa: E402
from codex_environment_backup.core import (  # noqa: E402
    BackupError,
    create_backup,
    doctor_codex_environment,
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
            self.assertIn("restore-codex-environment.cmd", names)
            self.assertIn("restore-codex-environment.ps1", names)
            self.assertIn("restore-codex-environment.command", names)
            self.assertIn("restore-codex-environment.sh", names)
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
                "restore-codex-environment.cmd",
                "restore-codex-environment.ps1",
                "restore-codex-environment.command",
                "restore-codex-environment.sh",
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

    def test_resolve_home_uses_profile_default(self) -> None:
        from codex_environment_backup.core import resolve_home, CODEX_PROFILE, CLAUDE_CODE_PROFILE
        env_without_codex_home = {k: v for k, v in os.environ.items() if k != "CODEX_HOME"}
        with mock.patch.dict(os.environ, env_without_codex_home, clear=True):
            codex_home = resolve_home(CODEX_PROFILE)
            claude_home = resolve_home(CLAUDE_CODE_PROFILE)
        self.assertEqual(codex_home, (Path.home() / ".codex").resolve())
        self.assertEqual(claude_home, (Path.home() / ".claude").resolve())

    def test_resolve_home_respects_env_var(self) -> None:
        from codex_environment_backup.core import resolve_home, CODEX_PROFILE, CLAUDE_CODE_PROFILE
        with mock.patch.dict(os.environ, {"CODEX_HOME": "/tmp/custom-codex"}):
            result = resolve_home(CODEX_PROFILE)
        self.assertEqual(result, Path("/tmp/custom-codex").resolve())
        env_without_codex_home = {k: v for k, v in os.environ.items() if k != "CODEX_HOME"}
        with mock.patch.dict(os.environ, env_without_codex_home, clear=True):
            result = resolve_home(CLAUDE_CODE_PROFILE)
        self.assertEqual(result, (Path.home() / ".claude").resolve())

    def test_resolve_home_override_takes_precedence(self) -> None:
        from codex_environment_backup.core import resolve_home, CODEX_PROFILE
        with mock.patch.dict(os.environ, {"CODEX_HOME": "/tmp/env"}):
            result = resolve_home(CODEX_PROFILE, "/tmp/explicit")
        self.assertEqual(result, Path("/tmp/explicit").resolve())

    def test_default_backup_root_uses_profile(self) -> None:
        from codex_environment_backup.core import default_backup_root, CODEX_PROFILE, CLAUDE_CODE_PROFILE
        codex_root = default_backup_root(CODEX_PROFILE)
        claude_root = default_backup_root(CLAUDE_CODE_PROFILE)
        self.assertTrue(str(codex_root).endswith("CodexBackups"))
        self.assertTrue(str(claude_root).endswith("ClaudeCodeBackups"))

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

    def test_cli_doctor_is_structural_by_default(self) -> None:
        from codex_environment_backup.cli import build_parser

        args = build_parser().parse_args(["doctor"])
        self.assertFalse(args.run_commands)
        self.assertFalse(args.no_run_commands)

    def test_cli_doctor_supports_explicit_command_probe(self) -> None:
        from codex_environment_backup.cli import build_parser

        args = build_parser().parse_args(["doctor", "--run-commands"])
        self.assertTrue(args.run_commands)
        self.assertFalse(args.no_run_commands)


if __name__ == "__main__":
    unittest.main()
