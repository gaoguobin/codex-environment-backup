# agent-environment-backup uninstall for Claude Code

Use these instructions when a user asks Claude Code to uninstall the environment backup skill and CLI.

## One-paste prompt

```text
Fetch and follow instructions from https://raw.githubusercontent.com/gaoguobin/codex-environment-backup/main/.claude/UNINSTALL.md
```

## Uninstall boundary

This file is for Claude Code to execute after the user asks for uninstall in natural language or pastes the one-paste prompt. Do not ask the user to manually type the command block unless Claude Code itself is unavailable.

Uninstall removes the package, skill link, and cloned repo. It must not delete generated backup archives or backup directories unless the user explicitly asks for that separate cleanup.
If the user later asks to remove old backups, first confirm that a newer backup
has been created, appears in `list-backups`, has a SHA256 file, and passes a
restore dry-run.

If any command fails because of permissions, process locks, or link removal, do not try unrelated workarounds. Ask for approval and rerun the same intended uninstall step.

### Windows PowerShell

Run this PowerShell block exactly:

```powershell
$repoRoot = Join-Path $HOME '.claude\agent-environment-backup'
$skillNamespace = Join-Path $HOME '.claude\skills\claude-code-environment-backup'
$pythonCmd = $null

foreach ($candidate in @('python3', 'python')) {
    if (-not (Get-Command $candidate -ErrorAction SilentlyContinue)) {
        continue
    }
    try {
        $versionText = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -ne 0) {
            continue
        }
        $version = [version]($versionText | Select-Object -First 1)
        if ($version -ge [version]'3.11') {
            $pythonCmd = $candidate
            break
        }
    } catch {
        continue
    }
}

if (-not $pythonCmd) {
    foreach ($candidate in @('python3', 'python')) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) {
            $pythonCmd = $candidate
            break
        }
    }
}

if ($pythonCmd) {
    & $pythonCmd -m pip uninstall -y agent-environment-backup
}

if (Test-Path $skillNamespace) {
    cmd /d /c "rmdir `"$skillNamespace`""
}

if (Test-Path $repoRoot) {
    Remove-Item -LiteralPath $repoRoot -Recurse -Force
}
```

### macOS or Linux shell

Run this shell block exactly:

```bash
set -euo pipefail

repo_root="$HOME/.claude/agent-environment-backup"
skill_namespace="$HOME/.claude/skills/claude-code-environment-backup"
python_cmd="${PYTHON:-}"

if [ -z "$python_cmd" ]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      python_cmd="$candidate"
      break
    fi
  done
fi

if [ -z "$python_cmd" ]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      python_cmd="$candidate"
      break
    fi
  done
fi

if [ -n "$python_cmd" ]; then
  "$python_cmd" -m pip uninstall -y agent-environment-backup
fi

if [ -L "$skill_namespace" ] || [ -e "$skill_namespace" ]; then
  rm "$skill_namespace"
fi

if [ -d "$repo_root" ]; then
  rm -rf "$repo_root"
fi
```

When cleanup completed, explicitly tell the user:

```text
Please restart Claude Code, or open a new CLI session, so Claude Code removes claude-code-environment-backup from the skill list.

请重启 Claude Code，或新开 CLI 实例，让它从 skill 列表中移除 claude-code-environment-backup。
```
