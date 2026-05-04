# codex-environment-backup install for Codex

Use these instructions when an engineer asks Codex to install the Codex environment backup skill and CLI.

## One-paste prompt for engineers

Paste this into Codex:

```text
Fetch and follow instructions from https://raw.githubusercontent.com/gaoguobin/codex-environment-backup/main/.codex/INSTALL.md
```

## What this installs

- Git repo: `~/.codex/codex-environment-backup`
- Python package: editable user install of `codex-environment-backup`
- Skill namespace link: `~/.agents/skills/codex-environment-backup -> ~/.codex/codex-environment-backup/skills`

The install does not create a backup, restore anything, change providers, write hooks, or upload data.

## Install steps

This file is for Codex to execute after the user asks for installation in natural language or pastes the one-paste prompt. Do not ask the user to manually type the command block unless Codex itself is unavailable.

If the Codex environment uses sandbox or approval controls, request approval/escalation for the install block because it clones from GitHub, installs a Python package, writes under `~/.codex`, and creates a link under `~/.agents`.

If any command fails because of network, permissions, sandbox write limits, or link creation, do not try unrelated workarounds. Ask for approval and rerun the same intended install step.

### Windows PowerShell

Run this PowerShell block exactly:

```powershell
$repoRoot = Join-Path $HOME '.codex\codex-environment-backup'
$skillsRoot = Join-Path $HOME '.agents\skills'
$skillNamespace = Join-Path $skillsRoot 'codex-environment-backup'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'git is required before installing codex-environment-backup.'
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
    throw 'Python 3.11+ is required before installing codex-environment-backup.'
}

if (Test-Path $repoRoot) {
    throw 'codex-environment-backup is already installed. Follow UPDATE.md instead.'
}

if (Test-Path $skillNamespace) {
    throw 'The skill namespace link already exists. Remove it or follow UNINSTALL.md before reinstalling.'
}

New-Item -ItemType Directory -Force -Path $skillsRoot | Out-Null
git clone https://github.com/gaoguobin/codex-environment-backup.git $repoRoot
& $pythonCmd -m pip install --user -e $repoRoot
cmd /d /c "mklink /J `"$skillNamespace`" `"$repoRoot\skills`""
& $pythonCmd -m codex_environment_backup doctor
```

### macOS or Linux shell

Run this shell block exactly:

```bash
set -euo pipefail

repo_root="$HOME/.codex/codex-environment-backup"
skills_root="$HOME/.agents/skills"
skill_namespace="$skills_root/codex-environment-backup"

command -v git >/dev/null || { echo "git is required before installing codex-environment-backup." >&2; exit 1; }
python_cmd="${PYTHON:-}"
if [ -n "$python_cmd" ]; then
  "$python_cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1 || {
    echo "Python 3.11+ is required before installing codex-environment-backup." >&2
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
    echo "Python 3.11+ is required before installing codex-environment-backup." >&2
    exit 1
  fi
fi

if [ -e "$repo_root" ]; then
  echo "codex-environment-backup is already installed. Follow UPDATE.md instead." >&2
  exit 1
fi

if [ -e "$skill_namespace" ]; then
  echo "The skill namespace link already exists. Remove it or follow UNINSTALL.md before reinstalling." >&2
  exit 1
fi

mkdir -p "$skills_root"
git clone https://github.com/gaoguobin/codex-environment-backup.git "$repo_root"
"$python_cmd" -m pip install --user -e "$repo_root"
ln -s "$repo_root/skills" "$skill_namespace"
"$python_cmd" -m codex_environment_backup doctor
```

## After install

Report the structural doctor JSON from the install block. The default doctor intentionally skips external `codex` commands to avoid sandbox noise before restart. Then explicitly tell the user in the user's language:

```text
Please restart Codex App and return to this conversation, or open a new CLI session, so Codex can rescan ~/.agents/skills. Then say "Back up current Codex environment".

请重启 Codex App 并回到这个对话，或新开 CLI 实例，让它重新扫描 ~/.agents/skills；然后说“备份当前 Codex 环境”。
```

Do not claim the skill is available before the restart.

After restarting Codex App or opening a new CLI process, the user can ask:

- `Back up current Codex environment`
- `备份当前 Codex 环境`
- `List Codex environment backups`
- `列出 Codex 环境备份`
- `Check Codex environment backup health`
- `检查 Codex 环境备份`
- `Restore this Codex backup`
- `恢复这个 Codex 备份`

## Existing install

If the repository already exists, fetch and follow:

- `https://raw.githubusercontent.com/gaoguobin/codex-environment-backup/main/.codex/UPDATE.md`
