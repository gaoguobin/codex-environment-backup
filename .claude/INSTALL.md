# agent-environment-backup install for Claude Code

Use these instructions when a user asks Claude Code to install the environment backup skill and CLI.

## One-paste prompt

Paste this into Claude Code:

```text
Fetch and follow instructions from https://raw.githubusercontent.com/gaoguobin/codex-environment-backup/main/.claude/INSTALL.md
```

## What this installs

- Git repo: `~/.claude/agent-environment-backup`
- Python package: editable user install of `agent-environment-backup`
- Skill link: `~/.claude/skills/claude-code-environment-backup -> ~/.claude/agent-environment-backup/skills/claude-code-environment-backup`

The install does not create a backup, restore anything, change providers, write hooks, or upload data.

## Install steps

This file is for Claude Code to execute after the user asks for installation in natural language or pastes the one-paste prompt. Do not ask the user to manually type the command block unless Claude Code itself is unavailable.

If any command fails because of network, permissions, or link creation, do not try unrelated workarounds. Ask for approval and rerun the same intended install step.

### Windows PowerShell

Run this PowerShell block exactly:

```powershell
$repoRoot = Join-Path $HOME '.claude\agent-environment-backup'
$skillsRoot = Join-Path $HOME '.claude\skills'
$skillNamespace = Join-Path $skillsRoot 'claude-code-environment-backup'
$skillSource = Join-Path $repoRoot 'skills\claude-code-environment-backup'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'git is required before installing agent-environment-backup.'
}

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
    throw 'Python 3.11+ is required before installing agent-environment-backup.'
}

if (Test-Path $repoRoot) {
    throw 'agent-environment-backup is already installed. Follow UPDATE.md instead.'
}

if (Test-Path $skillNamespace) {
    throw 'The skill namespace link already exists. Remove it or follow UNINSTALL.md before reinstalling.'
}

New-Item -ItemType Directory -Force -Path $skillsRoot | Out-Null
git clone https://github.com/gaoguobin/codex-environment-backup.git $repoRoot
& $pythonCmd -m pip install --user -e $repoRoot
cmd /d /c "mklink /J `"$skillNamespace`" `"$skillSource`""
& $pythonCmd -m agent_environment_backup --profile claude-code doctor
```

### macOS or Linux shell

Run this shell block exactly:

```bash
set -euo pipefail

repo_root="$HOME/.claude/agent-environment-backup"
skills_root="$HOME/.claude/skills"
skill_namespace="$skills_root/claude-code-environment-backup"
skill_source="$repo_root/skills/claude-code-environment-backup"

command -v git >/dev/null || { echo "git is required before installing agent-environment-backup." >&2; exit 1; }
python_cmd="${PYTHON:-}"
if [ -n "$python_cmd" ]; then
  "$python_cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1 || {
    echo "Python 3.11+ is required before installing agent-environment-backup." >&2
    exit 1
  }
else
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      python_cmd="$candidate"
      break
    fi
  done
  if [ -z "$python_cmd" ]; then
    echo "Python 3.11+ is required before installing agent-environment-backup." >&2
    exit 1
  fi
fi

if [ -e "$repo_root" ]; then
  echo "agent-environment-backup is already installed. Follow UPDATE.md instead." >&2
  exit 1
fi

if [ -e "$skill_namespace" ]; then
  echo "The skill namespace link already exists. Remove it or follow UNINSTALL.md before reinstalling." >&2
  exit 1
fi

mkdir -p "$skills_root"
git clone https://github.com/gaoguobin/codex-environment-backup.git "$repo_root"
"$python_cmd" -m pip install --user -e "$repo_root"
ln -s "$skill_source" "$skill_namespace"
"$python_cmd" -m agent_environment_backup --profile claude-code doctor
```

## After install

Report the structural doctor JSON from the install block. Then explicitly tell the user:

```text
Please restart Claude Code and return to this conversation, or open a new CLI session. Then say "Back up current Claude Code environment".

请重启 Claude Code 并回到这个对话，或新开 CLI 实例；然后说"备份当前 Claude Code 环境"。
```

Do not claim the skill is available before the restart.

After restarting, the user can ask:

- `Back up current Claude Code environment`
- `备份当前 Claude Code 环境`
- `List Claude Code environment backups`
- `列出 Claude Code 环境备份`
- `Check Claude Code environment backup health`
- `检查 Claude Code 环境备份`
- `Restore this Claude Code backup`
- `恢复 Claude Code 备份`

## Existing install

If the repository already exists, fetch and follow:

- `https://raw.githubusercontent.com/gaoguobin/codex-environment-backup/main/.claude/UPDATE.md`
