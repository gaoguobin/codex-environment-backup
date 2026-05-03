---
name: codex-environment-backup
description: Back up, restore, inspect, list, install, update, or uninstall local Codex environment backup tooling. Use when the user asks to back up the current Codex environment, restore Codex from a backup, check whether a backup is valid, list Codex backups, install/update/uninstall the Codex backup skill, or set up manual/periodic Codex environment backup workflows with phrases such as "备份当前 Codex 环境", "恢复 Codex 环境", "检查 Codex 备份", "列出 Codex 备份", "安装 Codex 环境备份工具", "更新 Codex 环境备份工具", "卸载 Codex 环境备份工具", or "定期备份 Codex".
---

Use this skill when the user wants Codex to manage local Codex environment backups. The user-facing interface is natural language; the Python CLI is the source of truth that Codex runs behind the scenes.

## Trigger patterns

- Backup requests such as `备份当前 Codex 环境`, `back up current Codex environment`
- Restore requests such as `恢复 Codex 环境`, `restore this Codex backup`
- Inspection requests such as `检查 Codex 备份`, `Codex backup doctor`
- Listing requests such as `列出 Codex 备份`, `show Codex environment backups`
- Maintenance requests such as `安装 Codex 环境备份工具`, `更新 Codex 环境备份工具`, `卸载 Codex 环境备份工具`
- Periodic backup requests such as `定期备份 Codex`

## How to execute

Resolve the Python command first. Prefer `python3`; fall back to `python` only when it is Python 3.11 or newer. Do not use the Windows `py` launcher as the default command.

Run the CLI as the source of truth:

```text
<python-cmd> -m codex_environment_backup doctor
<python-cmd> -m codex_environment_backup backup
<python-cmd> -m codex_environment_backup list-backups
<python-cmd> -m codex_environment_backup restore --archive <backup>
<python-cmd> -m codex_environment_backup restore --archive <backup> --apply --i-understand-this-restores-sensitive-codex-state
```

Resolve `CODEX_HOME` in this order:

1. User-provided `--codex-home`
2. `CODEX_HOME` environment variable
3. `~/.codex`

Use `--backup-root <path>` when the user names a backup destination. Otherwise use the CLI default `~/Documents/CodexBackups`.

## Backup workflow

For natural language backup requests:

1. Run `<python-cmd> -m codex_environment_backup doctor`.
2. Run `<python-cmd> -m codex_environment_backup backup`.
3. Report `ok`, `backup_dir`, `archive`, `archive_sha256`, `sha256_file`, and `counts`.
4. Remind the user that the archive is local and sensitive.

For backup requests, `core_ok=false` or `path_scan_ok=false` blocks backup. `command_ok=false` is a health warning, not a backup blocker; report the failed command summary and continue unless the user only asked for a health check.

Do not ask the user to run commands for normal backup requests. Ask for approval only when sandbox, filesystem, network, or install policy requires it.

## Restore workflow

For restore requests:

1. Ask for or locate the backup archive/directory.
2. Run dry-run first with `<python-cmd> -m codex_environment_backup restore --archive <backup>`.
3. Report the restore plan and whether the target appears to be the active `CODEX_HOME`.
4. If the user only wanted a plan, stop after dry-run.
5. Apply only after explicit confirmation.

Hard boundary: if applying to the same `CODEX_HOME` used by the current Codex App/session, tell the user that the current Codex process should be closed before files are overwritten. Do not pretend the active app can safely restore itself in-place. Prefer the no-command restore kit for non-CLI users:

- If the user has a backup directory, point them to `RESTORE.md` and the platform helper in that directory. Use `RESTORE_INSTRUCTIONS.txt` as a plain-text fallback.
- If the user has a backup archive, tell them to extract it first, open `RESTORE.md` or `RESTORE_INSTRUCTIONS.txt`, close Codex App, and run the platform helper.
- On Windows, the normal non-command handoff is double-clicking `restore-codex-environment.cmd`.
- On macOS, the normal non-command handoff is opening `restore-codex-environment.command`.
- On Linux, use `restore-codex-environment.sh`.

Provide the exact CLI handoff only for advanced users or when the restore helper is missing.

When apply is safe to run from the current context, run:

```text
<python-cmd> -m codex_environment_backup restore --archive <backup> --apply --i-understand-this-restores-sensitive-codex-state
```

The CLI creates a pre-restore backup before applying. Restore overlays backed-up files and does not prune excluded paths.
The default post-restore doctor is structural only. Do not add `--run-post-restore-commands` unless the user explicitly wants command-level validation immediately, because external `codex` commands can recreate runtime directories in the target environment.

## Doctor and listing workflows

For health checks:

```text
<python-cmd> -m codex_environment_backup doctor
```

For listing backups:

```text
<python-cmd> -m codex_environment_backup list-backups
```

Report `core_ok`, `path_scan_ok`, `command_ok`, command failures, and presence/counts for config, hooks, sessions, archived sessions, memories, skills, plugins, rules, automations, and optional `codex-fast-proxy` status. If `codex_fast_proxy` is not installed, report it as skipped rather than failed. Do not print provider URLs, local state paths, or full integration stdout from optional integrations.

## Install, update, uninstall

When the user asks to install, update, or uninstall this tool, fetch and follow the corresponding repository instruction file as source of truth:

- `.codex/INSTALL.md`
- `.codex/UPDATE.md`
- `.codex/UNINSTALL.md`

These flows are still natural-language initiated. The user should paste or ask Codex to fetch the instruction file; Codex runs the exact blocks, requests approval when needed, reports JSON, and gives the restart handoff.

## Safety model

- Treat every backup archive as sensitive. It can contain conversation history, login state, provider configuration, local hooks, skills, plugins, rules, automations, and third-party provider settings.
- Do not print API keys, `auth.json` contents, conversation contents, request bodies, or full session files.
- Do not upload backup archives to GitHub or any remote storage unless the user explicitly asks after the sensitivity warning.
- Do not delete old backups by default.
- Do not edit ACLs. Diagnose Windows sandbox ACL problems only.
- Do not include `.sandbox-secrets`, `.sandbox`, `.sandbox-bin`, `.tmp`, `tmp`, or live SQLite WAL/SHM files.
- Do not ask the user to manually run CLI commands in normal backup/doctor/list workflows.

## Periodic backup requests

Initial versions are manual-trigger only. If the user asks for periodic backups, explain that the repository does not install Task Scheduler, launchd, cron, or Codex automations yet. Offer to create a manual backup now and discuss scheduler design separately.

## Result handling

- Treat CLI JSON as authoritative.
- For health-check-only requests, if `ok` is false, stop and report `core_ok`, `path_scan_ok`, `command_ok`, and the failed command summary.
- For backup requests, continue when only `command_ok` is false; stop when `core_ok` or `path_scan_ok` is false.
- Prefer exact paths from JSON output.
- Do not infer that restore succeeded from natural language alone; use the restore JSON, restored file count, errors list, and post-restore structural doctor report.
- For install/update/uninstall, report the executed instruction source and the final structural doctor/status result.
