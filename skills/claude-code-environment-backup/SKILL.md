---
name: claude-code-environment-backup
description: Claude Code environment backup, restore, doctor, and list workflows for local ~/.claude state. Use for requests like back up Claude Code, restore Claude Code backup, list/check Claude Code backups, or install/update/uninstall the backup skill.
---

Use this skill when the user wants Claude Code to manage local Claude Code environment backups. The user-facing interface is natural language; the Python CLI is the source of truth that Claude Code runs behind the scenes.

## Trigger patterns

- Backup requests such as `备份 Claude Code 环境`, `back up Claude Code environment`
- Restore requests such as `恢复 Claude Code 备份`, `restore Claude Code backup`
- Inspection requests such as `检查 Claude Code 备份`, `check Claude Code backup health`
- Listing requests such as `列出 Claude Code 备份`, `list Claude Code backups`
- Maintenance requests such as `安装 Claude Code 环境备份工具`, `更新 Claude Code 环境备份工具`, `卸载 Claude Code 环境备份工具`
- Periodic backup requests such as `定期备份 Claude Code`

## How to execute

Resolve the Python command first. Prefer `python3`; fall back to `python` only when it is Python 3.11 or newer. Do not use the Windows `py` launcher as the default command.

Run the CLI as the source of truth:

```text
<python-cmd> -m agent_environment_backup --profile claude-code doctor
<python-cmd> -m agent_environment_backup --profile claude-code doctor --run-commands
<python-cmd> -m agent_environment_backup --profile claude-code backup
<python-cmd> -m agent_environment_backup --profile claude-code list-backups
<python-cmd> -m agent_environment_backup --profile claude-code restore --archive <backup>
<python-cmd> -m agent_environment_backup --profile claude-code restore --archive <backup> --apply --i-understand-this-restores-sensitive-state
```

Resolve the Claude Code home directory in this order:

1. User-provided `--home`
2. `~/.claude`

Use `--backup-root <path>` when the user names a backup destination. Otherwise use the CLI default `~/Documents/ClaudeCodeBackups`.

## Backup workflow

For natural language backup requests:

1. Run `<python-cmd> -m agent_environment_backup --profile claude-code doctor`.
2. Run `<python-cmd> -m agent_environment_backup --profile claude-code backup`.
3. Report `ok`, `backup_dir`, `archive`, `archive_sha256`, `sha256_file`, and `counts`.
4. Remind the user that the archive is local and sensitive.

The default doctor is structural and does not launch nested commands. For backup requests, `core_ok=false` or `path_scan_ok=false` blocks backup. `command_ok=false` is a health warning, not a backup blocker; report the failed command summary and continue unless the user only asked for a health check.

Do not ask the user to run commands for normal backup requests. Ask for approval only when sandbox, filesystem, network, or install policy requires it.

## Restore workflow

For restore requests:

1. Ask for or locate the backup archive/directory.
2. Run dry-run first with `<python-cmd> -m agent_environment_backup --profile claude-code restore --archive <backup>`.
3. Report the restore plan and whether the target appears to be the active Claude Code home directory (`~/.claude`).
4. If the user only wanted a plan, stop after dry-run.
5. Apply only after explicit confirmation.

Hard boundary: if applying to the same Claude Code home directory (`~/.claude`) used by the current Claude Code session, tell the user that Claude Code should be closed before files are overwritten. Do not pretend the active process can safely restore itself in-place. Prefer the no-command restore kit for non-CLI users:

- If the user has a backup directory, point them to `RESTORE.md` and the platform helper in that directory. Use `RESTORE_INSTRUCTIONS.txt` as a plain-text fallback.
- If the user has a backup archive, tell them to extract it first, open `RESTORE.md` or `RESTORE_INSTRUCTIONS.txt`, close Claude Code, and run the platform helper.
- On Windows, the normal non-command handoff is double-clicking `restore-environment.cmd`.
- On macOS, the normal non-command handoff is opening `restore-environment.command`.
- On Linux, use `restore-environment.sh`.

Provide the exact CLI handoff only for advanced users or when the restore helper is missing.

When apply is safe to run from the current context, run:

```text
<python-cmd> -m agent_environment_backup --profile claude-code restore --archive <backup> --apply --i-understand-this-restores-sensitive-state
```

The CLI creates a pre-restore backup before applying. Restore overlays backed-up files and does not prune excluded paths.
The default post-restore doctor is structural only. Do not add `--run-post-restore-commands` unless the user explicitly wants command-level validation immediately, because external commands can recreate runtime directories in the target environment.

## Doctor and listing workflows

For health checks:

```text
<python-cmd> -m agent_environment_backup --profile claude-code doctor
```

This is the default path for natural-language health checks. It reports backup readiness without external command probe noise.

For explicit command-level checks only:

```text
<python-cmd> -m agent_environment_backup --profile claude-code doctor --run-commands
```

For listing backups:

```text
<python-cmd> -m agent_environment_backup --profile claude-code list-backups
```

Report `core_ok`, `path_scan_ok`, `command_ok`, whether command probes were skipped, and presence/counts for `settings.json`, `settings.local.json`, `credentials.json`, `statsig`, `projects`, `memory`, `todos`, `plugins`, and `keybindings.json`. Do not print provider URLs, local state paths, or full integration stdout from optional integrations.

## Install, update, uninstall

When the user asks to install, update, or uninstall this tool, fetch and follow the corresponding repository instruction file as source of truth:

- `.claude/INSTALL.md`
- `.claude/UPDATE.md`
- `.claude/UNINSTALL.md`

These flows are still natural-language initiated. The user should paste or ask Claude Code to fetch the instruction file; Claude Code runs the exact blocks, requests approval when needed, reports JSON, and gives the restart handoff.

## Safety model

- Treat every backup archive as sensitive. It can contain conversation history, login state, provider configuration, local hooks, skills, plugins, rules, and third-party provider settings.
- Do not print API keys, `credentials.json` contents, conversation contents, request bodies, or full session files.
- Do not upload backup archives to GitHub or any remote storage unless the user explicitly asks after the sensitivity warning.
- Do not delete old backups by default.
- Before deleting old backups, verify that a newer backup reported `ok=true`, has an archive and SHA256 file, appears in `list-backups`, and passes restore dry-run.
- Do not edit ACLs. Diagnose Windows sandbox ACL problems only.
- Do not include `.sandbox-secrets`, `.sandbox`, `.sandbox-bin`, `.tmp`, `tmp`, or live SQLite WAL/SHM files.
- Do not ask the user to manually run CLI commands in normal backup/doctor/list workflows.

## Periodic backup requests

Initial versions are manual-trigger only. If the user asks for periodic backups, explain that the repository does not install Task Scheduler, launchd, or cron yet. Offer to create a manual backup now and discuss scheduler design separately.

## Result handling

- Treat CLI JSON as authoritative.
- For health-check-only requests, if `ok` is false, stop and report `core_ok`, `path_scan_ok`, `command_ok`, and the failed command summary.
- For backup requests, continue when only `command_ok` is false; stop when `core_ok` or `path_scan_ok` is false.
- Prefer exact paths from JSON output.
- Do not infer that restore succeeded from natural language alone; use the restore JSON, restored file count, errors list, and post-restore structural doctor report.
- For install/update/uninstall, report the executed instruction source and the final structural doctor/status result.
