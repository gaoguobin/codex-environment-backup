from __future__ import annotations

import argparse
import json
import sys

from .core import (
    BackupError,
    create_backup,
    doctor_codex_environment,
    list_backups,
    restore_backup,
)


def emit_json(data: object) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-environment-backup",
        description="Back up, restore, and inspect a local Codex environment.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="Create an offline Codex backup")
    backup.add_argument("--codex-home", help="Codex home path. Defaults to CODEX_HOME or ~/.codex.")
    backup.add_argument("--backup-root", help="Directory where backups are stored.")
    backup.add_argument("--format", choices=["tar.gz", "zip"], default="tar.gz")
    backup.add_argument("--no-archive", action="store_true", help="Create only the backup directory.")
    backup.add_argument(
        "--no-doctor-commands",
        action="store_true",
        help="Skip external doctor commands such as codex --version.",
    )

    restore = subparsers.add_parser("restore", help="Dry-run or apply a Codex restore")
    restore.add_argument("--archive", required=True, help="Backup archive or backup directory.")
    restore.add_argument("--codex-home", help="Codex home path. Defaults to CODEX_HOME or ~/.codex.")
    restore.add_argument("--backup-root", help="Directory for the mandatory pre-restore backup.")
    restore.add_argument("--format", choices=["tar.gz", "zip"], default="tar.gz")
    restore.add_argument("--apply", action="store_true", help="Apply restore. Default is dry-run.")
    restore.add_argument(
        "--i-understand-this-restores-sensitive-codex-state",
        action="store_true",
        dest="confirm",
        help="Required with --apply.",
    )
    restore.add_argument(
        "--run-post-restore-commands",
        action="store_true",
        help="Run external post-restore doctor commands after apply. Default is structural checks only.",
    )

    doctor = subparsers.add_parser("doctor", help="Inspect Codex environment health")
    doctor.add_argument("--codex-home", help="Codex home path. Defaults to CODEX_HOME or ~/.codex.")
    doctor.add_argument(
        "--no-run-commands",
        action="store_true",
        help="Skip codex and optional integration commands.",
    )

    list_cmd = subparsers.add_parser("list-backups", help="List local backup manifests")
    list_cmd.add_argument("--backup-root", help="Directory where backups are stored.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "backup":
            result = create_backup(
                args.codex_home,
                backup_root=args.backup_root,
                archive_format=args.format,
                make_archive=not args.no_archive,
                run_doctor_commands=not args.no_doctor_commands,
            )
            emit_json(result)
            return 0 if result.get("ok") else 1
        if args.command == "restore":
            result = restore_backup(
                args.archive,
                args.codex_home,
                backup_root=args.backup_root,
                apply=args.apply,
                confirm=args.confirm,
                archive_format=args.format,
                run_post_restore_commands=args.run_post_restore_commands,
            )
            emit_json(result)
            return 0 if result.get("ok") else 1
        if args.command == "doctor":
            result = doctor_codex_environment(
                args.codex_home,
                run_commands=not args.no_run_commands,
            )
            emit_json(result)
            return 0 if result.get("ok") else 1
        if args.command == "list-backups":
            emit_json(list_backups(args.backup_root))
            return 0
    except BackupError as exc:
        emit_json({"ok": False, "error": str(exc)})
        return 2
    except KeyboardInterrupt:
        emit_json({"ok": False, "error": "interrupted"})
        return 130

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
