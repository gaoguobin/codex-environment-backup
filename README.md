# codex-environment-backup

Offline backup, restore, and health checks for a local Codex environment.

This tool is designed to be operated by Codex through natural language. The
Python CLI is the deterministic implementation layer and remains available for
advanced users, automation, smoke tests, and recovery when Codex itself is
unavailable.

[中文说明](#chinese) · [Install](#install) · [Daily Use](#daily-use) · [Restore](#restore) · [Update](#update) · [Uninstall](#uninstall) · [Safety](#safety-model) · [Advanced CLI](#advanced-cli)

## Why

Codex environment state can include conversations, provider configuration,
local hooks, skills, plugins, rules, automations, and optional local tool state.
This repository turns that state into a repeatable local backup and restore
workflow instead of relying on memory or manual copy steps.

## Highlights

| Capability | What it means |
| --- | --- |
| Natural-language first | Normal users ask Codex to back up, list, check, or restore; Codex runs the CLI behind the scenes. |
| Local and offline | Backups are written to local disk by default and are never uploaded by this tool. |
| Provider agnostic | `config.toml`, `hooks.json`, provider sections, `base_url`, `env_key`, and `service_tier` are backed up as files without assuming a provider. |
| SQLite safe | `*.sqlite` databases are copied with the Python `sqlite3` backup API and checked with `PRAGMA integrity_check`. |
| Restore guarded | Restore defaults to dry-run, requires explicit apply confirmation, and creates a pre-restore backup first. |
| No-command handoff | Each backup includes `RESTORE.md`, plain-text instructions, platform helpers, and a standalone restore script. |
| Sandbox conservative | `.sandbox-secrets`, sandbox caches, temp folders, and live SQLite WAL/SHM files are excluded. |

## Install

Paste this into Codex:

```text
Fetch and follow instructions from https://raw.githubusercontent.com/gaoguobin/codex-environment-backup/main/.codex/INSTALL.md
```

The install flow clones this repository to `~/.codex/codex-environment-backup`,
installs the Python package in editable user mode, and links the bundled skill
into `~/.agents/skills`.

After installation, restart Codex App and return to the same conversation, or
open a new Codex CLI process. Then ask:

```text
Back up current Codex environment
```

## Daily Use

Ask Codex:

```text
Back up current Codex environment
```

Codex will run a health check, create a local backup directory, create an
archive, write SHA256, and report the backup path.

Ask Codex:

```text
List Codex environment backups
```

Ask Codex:

```text
Check Codex environment backup health
```

The default health check is structural: it checks `CODEX_HOME`, important paths,
config parsing, and backup readiness without launching nested `codex` commands.
Ask for a command-level doctor only when you explicitly want Codex CLI/MCP
subprocess probing too.

## Restore

Ask Codex:

```text
Restore this Codex backup
```

Codex will dry-run the restore first and report what would be restored. Applying
a restore has a hard safety boundary: the Codex App that uses the target
`CODEX_HOME` should be closed before files are overwritten. If you are restoring
the same environment that is running the current conversation, use the backup's
restore kit after closing Codex.

Each backup includes a no-command restore kit:

```text
RESTORE.md
RESTORE_INSTRUCTIONS.txt
restore-codex-environment.cmd
restore-codex-environment.ps1
restore-codex-environment.command
restore-codex-environment.sh
restore-standalone.py
```

For a non-command-line restore, extract the backup archive, open `RESTORE.md` or
the plain-text fallback `RESTORE_INSTRUCTIONS.txt`, close Codex App, and use the
platform restore helper. On Windows the normal handoff is double-clicking
`restore-codex-environment.cmd`.

## Defaults

`CODEX_HOME` is resolved in this order:

1. User-provided `--codex-home`
2. `CODEX_HOME` environment variable
3. `~/.codex`

Backups are written under `~/Documents/CodexBackups` by default:

```text
~/Documents/CodexBackups/codex-backup-YYYYMMDD-HHMMSS/
~/Documents/CodexBackups/codex-backup-YYYYMMDD-HHMMSS.tar.gz
~/Documents/CodexBackups/codex-backup-YYYYMMDD-HHMMSS.tar.gz.sha256
```

Each backup directory contains:

```text
codex-backup-YYYYMMDD-HHMMSS/
  files/
  manifest.json
  environment-snapshot.txt
  sqlite-integrity-check.json
  doctor-report.json
  backup-summary.txt
  RESTORE.md
  RESTORE_INSTRUCTIONS.txt
  restore-codex-environment.cmd
  restore-codex-environment.ps1
  restore-codex-environment.command
  restore-codex-environment.sh
  restore-standalone.py
```

## Backup Retention

The tool never deletes old backups by default. Keep at least one known-good
backup until a newer backup has been verified. A practical verification means:

- `backup` reported `ok=true`
- the archive and `.sha256` file exist
- `list-backups` shows the new backup with readable metadata
- a restore dry-run against the new archive reports `ok=true`

After that, older backups can be deleted as a separate cleanup task if the user
explicitly asks for it. Treat deletion as destructive because backups may be the
only recovery path for lost Codex history or provider state.

## Compatibility

- Python 3.11+.
- Windows, macOS, and Linux.
- Python standard library only.
- Paths are handled with `pathlib`.
- SQLite databases ending in `.sqlite` are copied with the Python `sqlite3`
  backup API and then checked with `PRAGMA integrity_check`.
- Windows uses junctions for skill installation; macOS and Linux use symlinks.

`codex-fast-proxy` is optional. If `python -m codex_fast_proxy` is available in
the current Python environment, `doctor` records safe `status` / `doctor`
summaries without printing provider URLs, local state paths, or full integration
stdout. If it is not available, the integration is skipped.

## Update

Paste this into Codex:

```text
Fetch and follow instructions from https://raw.githubusercontent.com/gaoguobin/codex-environment-backup/main/.codex/UPDATE.md
```

If skill files changed, restart Codex App or open a new CLI process so Codex
rescans `~/.agents/skills`.

## Uninstall

Paste this into Codex:

```text
Fetch and follow instructions from https://raw.githubusercontent.com/gaoguobin/codex-environment-backup/main/.codex/UNINSTALL.md
```

Uninstall removes the installed package, repo, and skill link. It does not
delete generated backup archives.

## Safety Model

Backups can contain Codex history, provider configuration, local hooks, login
state, skills, plugins, rules, automations, and other sensitive environment
data. Do not commit or upload generated backup archives.

The tool does not print API keys, `auth.json` payloads, or conversation
contents. Restore defaults to dry-run and requires an explicit confirmation flag
before it writes to `CODEX_HOME`.

Excluded paths include:

- `.sandbox/`
- `.sandbox-bin/`
- `.sandbox-secrets/`
- `.tmp/`
- `tmp/`
- live SQLite WAL/SHM files such as `*.sqlite-wal` and `*.sqlite-shm`

The tool does not change ACLs. Windows sandbox ACL issues are diagnostic only.

## Privacy

Provider configuration is treated as first-class data. `config.toml`,
`hooks.json`, provider sections, `base_url`, `env_key`, and `service_tier` are
backed up as files, but secrets are not printed. The backup archive itself is
sensitive because it can contain login state and conversation history.

## Advanced CLI

The CLI is for advanced users, CI, smoke tests, automation, and recovery when
Codex cannot operate.

Use Python 3.11 or newer. Prefer `python3` when it exists; on Windows, `python`
is acceptable when it points to Python 3.11+.

```powershell
python -m codex_environment_backup doctor
python -m codex_environment_backup doctor --run-commands
python -m codex_environment_backup backup
python -m codex_environment_backup list-backups
python -m codex_environment_backup restore --archive C:\path\to\codex-backup-YYYYMMDD-HHMMSS.tar.gz
python -m codex_environment_backup restore --archive C:\path\to\codex-backup-YYYYMMDD-HHMMSS.tar.gz --apply --i-understand-this-restores-sensitive-codex-state
```

On systems where `python3` is the Python 3 command, replace `python` with
`python3`.

`doctor` is structural by default. Add `--run-commands` for explicit
command-level probes such as `codex --version`, `codex mcp list`, and optional
integration checks.

Before an apply restore, the tool creates a pre-restore backup of the current
`CODEX_HOME`. After apply, the default post-restore check is structural only so
external `codex` commands do not recreate runtime directories in the restored
environment. Advanced users can add `--run-post-restore-commands` after they
intend to run a full command-level doctor immediately.

## Development

Run tests:

```powershell
python -m unittest discover -s tests
```

The test suite includes a fake-home smoke path that creates a local archive and
checks dry-run restore behavior.

<a id="chinese"></a>

## 中文说明

`codex-environment-backup` 是一个面向 Codex 的本地环境备份/恢复工具。正常用户不需要先学命令行：
安装后直接对 Codex 说“备份当前 Codex 环境”“列出 Codex 备份”“检查 Codex 备份”或“恢复这个备份”，
Codex 会在背后运行确定性的 Python CLI。

这个仓库的目标是把 Codex 环境备份固化成可复用流程，避免只靠自然语言记忆或手动复制。

### 快速安装

把这句话贴给 Codex：

```text
Fetch and follow instructions from https://raw.githubusercontent.com/gaoguobin/codex-environment-backup/main/.codex/INSTALL.md
```

安装完成后，重启 Codex App 并回到原对话，或新开 CLI 实例，然后说：

```text
备份当前 Codex 环境
```

安装只会安装仓库、Python 包和 skill 链接；不会创建备份、恢复文件、修改 provider、写 hooks 或上传数据。

### 日常用法

对 Codex 说：

```text
备份当前 Codex 环境
```

Codex 会先跑体检，然后生成本地备份目录、压缩包、SHA256、manifest、环境快照和 SQLite 完整性检查结果。

也可以说：

```text
列出 Codex 环境备份
```

```text
检查 Codex 环境备份
```

### 恢复

对 Codex 说：

```text
恢复这个 Codex 备份
```

恢复默认先 dry-run，只汇报会恢复什么。真正覆盖 `CODEX_HOME` 前，工具会先创建 pre-restore backup，并且
必须有明确确认。
apply 后默认只做结构体检；如果你确实要立刻跑完整命令体检，可以加 `--run-post-restore-commands`。

有一个硬边界不能假装不存在：如果要恢复的正是当前 Codex App 正在使用的 `CODEX_HOME`，应先关闭
Codex App，再执行覆盖恢复。为了照顾不懂命令行的用户，每个备份都会带恢复套件：

```text
RESTORE.md
RESTORE_INSTRUCTIONS.txt
restore-codex-environment.cmd
restore-codex-environment.ps1
restore-codex-environment.command
restore-codex-environment.sh
restore-standalone.py
```

非命令行用户可以解压备份包，打开 `RESTORE.md` 或 `RESTORE_INSTRUCTIONS.txt`，关闭 Codex App，然后使用
对应平台的恢复助手。Windows 上默认是双击 `restore-codex-environment.cmd`；macOS 上是打开
`restore-codex-environment.command`；Linux 上使用 `restore-codex-environment.sh`。

### 默认路径

`CODEX_HOME` 的识别顺序是：

1. 用户显式传入 `--codex-home`
2. `CODEX_HOME` 环境变量
3. `~/.codex`

默认备份位置是：

```text
~/Documents/CodexBackups
```

Windows 上通常类似：

```text
C:\Users\<you>\Documents\CodexBackups
```

### 备份保留

工具不会默认删除旧备份。正式安装后，建议先完成一次新的正式备份，并确认：

- `backup` 返回 `ok=true`
- 压缩包和 `.sha256` 文件都存在
- `list-backups` 能正常列出新备份和元数据
- 用新压缩包跑一次 restore dry-run，结果是 `ok=true`

确认新备份可用后，旧备份可以作为单独清理任务删除；删除前要把它当成破坏性操作处理，因为旧备份可能是
找回历史记录、provider 配置或登录状态的最后兜底。

### 更新

把这句话贴给 Codex：

```text
Fetch and follow instructions from https://raw.githubusercontent.com/gaoguobin/codex-environment-backup/main/.codex/UPDATE.md
```

如果更新了 skill 文件，需要重启 Codex App 或新开 CLI 实例，让 Codex 重新扫描 `~/.agents/skills`。

### 卸载

把这句话贴给 Codex：

```text
Fetch and follow instructions from https://raw.githubusercontent.com/gaoguobin/codex-environment-backup/main/.codex/UNINSTALL.md
```

卸载会移除 Python 包、仓库和 skill 链接，但不会删除已经生成的备份目录或备份压缩包。

### 行为边界

- 备份包可能包含历史对话、登录状态、provider 配置、本地 hooks、skills、plugins、rules 和 automations。
- 不要把生成的备份包提交到 GitHub，也不要默认上传到任何远端。
- 工具不会打印 API key、`auth.json` 内容或历史对话内容。
- 不默认删除旧备份。
- 不自动修改 ACL；Windows sandbox ACL 问题只诊断。
- 不备份 `.sandbox-secrets`、`.sandbox`、`.sandbox-bin`、`.tmp`、`tmp`。
- 不直接备份 live SQLite WAL/SHM 文件。
- `*.sqlite` 使用 Python `sqlite3` online backup API 复制，并执行 `PRAGMA integrity_check`。
- 第三方 provider 配置是一等公民：`config.toml`、`hooks.json`、`model_provider`、`model_providers`、
  `base_url`、`env_key`、`service_tier` 等都会作为文件备份，但不会泄露内容到日志。
- `codex-fast-proxy` 只是可选集成；检测到可用时记录安全摘要，不打印 provider URL、本机状态路径或完整 stdout；
  不可用时记为 skipped，不会失败。

### 高级 CLI

CLI 留给高级用户、CI、smoke test、自动化和 Codex 自身不可用时的恢复兜底。普通备份、检查和列出备份
不应要求用户手动运行命令。

```powershell
python -m codex_environment_backup doctor
python -m codex_environment_backup doctor --run-commands
python -m codex_environment_backup backup
python -m codex_environment_backup list-backups
python -m codex_environment_backup restore --archive C:\path\to\codex-backup-YYYYMMDD-HHMMSS.tar.gz
python -m codex_environment_backup restore --archive C:\path\to\codex-backup-YYYYMMDD-HHMMSS.tar.gz --apply --i-understand-this-restores-sensitive-codex-state
python -m codex_environment_backup restore --archive C:\path\to\codex-backup-YYYYMMDD-HHMMSS.tar.gz --apply --i-understand-this-restores-sensitive-codex-state --run-post-restore-commands
```

`doctor` 默认只做结构体检。确实需要命令级探测 `codex --version`、`codex mcp list` 和可选集成时，
再加 `--run-commands`。

如果系统里 `python3` 才是 Python 3，请把上面的 `python` 换成 `python3`。
apply 恢复后的默认体检只做结构检查，避免外部 `codex` 命令在恢复目标里重新生成运行期目录。
高级用户确实要立刻跑完整命令体检时，可以额外加 `--run-post-restore-commands`。
