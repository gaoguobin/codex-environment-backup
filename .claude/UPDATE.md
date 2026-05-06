# agent-environment-backup update for Claude Code

Use these instructions when a user asks Claude Code to update the environment backup skill and CLI.

## One-paste prompt

```text
Fetch and follow instructions from https://raw.githubusercontent.com/gaoguobin/codex-environment-backup/main/.claude/UPDATE.md
```

## Update steps

This file is for Claude Code to execute after the user asks for update in natural language or pastes the one-paste prompt. Do not ask the user to manually type the command block unless Claude Code itself is unavailable.

If any command fails because of network, permissions, or link creation, do not try unrelated workarounds. Ask for approval and rerun the same intended update step.

### Windows PowerShell

Run this PowerShell block exactly:

```powershell
$repoRoot = Join-Path $HOME '.claude\agent-environment-backup'
$skillNamespace = Join-Path $HOME '.claude\skills\claude-code-environment-backup'
$skillSource = Join-Path $repoRoot 'skills\claude-code-environment-backup'
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
    throw 'Python 3.11+ is required before updating agent-environment-backup.'
}

if (-not (Test-Path $repoRoot)) {
    throw 'agent-environment-backup is not installed. Follow INSTALL.md first.'
}

git -C $repoRoot pull --ff-only
& $pythonCmd -m pip install --user -e $repoRoot

if (-not (Test-Path $skillNamespace)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $skillNamespace) | Out-Null
    cmd /d /c "mklink /J `"$skillNamespace`" `"$skillSource`""
} elseif (-not (Test-Path (Join-Path $skillNamespace 'SKILL.md'))) {
    $skillItem = Get-Item -LiteralPath $skillNamespace
    if (($skillItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        cmd /d /c "rmdir `"$skillNamespace`""
        cmd /d /c "mklink /J `"$skillNamespace`" `"$skillSource`""
    } else {
        throw "Existing skill path is not a link and does not contain SKILL.md: $skillNamespace"
    }
}

& $pythonCmd -m agent_environment_backup --profile claude-code doctor
```

### macOS or Linux shell

Run this shell block exactly:

```bash
set -euo pipefail

repo_root="$HOME/.claude/agent-environment-backup"
skill_namespace="$HOME/.claude/skills/claude-code-environment-backup"
skill_source="$repo_root/skills/claude-code-environment-backup"
python_cmd="${PYTHON:-}"

if [ -n "$python_cmd" ]; then
  "$python_cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1 || {
    echo "Python 3.11+ is required before updating agent-environment-backup." >&2
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
    echo "Python 3.11+ is required before updating agent-environment-backup." >&2
    exit 1
  fi
fi

if [ ! -d "$repo_root" ]; then
  echo "agent-environment-backup is not installed. Follow INSTALL.md first." >&2
  exit 1
fi

git -C "$repo_root" pull --ff-only
"$python_cmd" -m pip install --user -e "$repo_root"

if [ ! -e "$skill_namespace" ]; then
  mkdir -p "$(dirname "$skill_namespace")"
  ln -s "$skill_source" "$skill_namespace"
elif [ ! -f "$skill_namespace/SKILL.md" ]; then
  if [ -L "$skill_namespace" ]; then
    rm "$skill_namespace"
    ln -s "$skill_source" "$skill_namespace"
  else
    echo "Existing skill path is not a link and does not contain SKILL.md: $skill_namespace" >&2
    exit 1
  fi
fi

"$python_cmd" -m agent_environment_backup --profile claude-code doctor
```

Report the final structural doctor JSON. If skill files changed or the skill link was newly created, explicitly tell the user:

```text
Please restart Claude Code and return to this conversation, or open a new CLI session.

请重启 Claude Code 并回到这个对话，或新开 CLI 实例。
```
