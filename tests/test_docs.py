from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocumentationShapeTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_readme_keeps_bilingual_natural_language_entry(self) -> None:
        readme = self.read("README.md")
        self.assertIn("[中文说明](#chinese)", readme)
        self.assertIn("## 中文说明", readme)
        self.assertIn("[Agent Skills](#agent-skills-and-discovery)", readme)
        self.assertIn("[Plugin Readiness](#plugin-readiness)", readme)
        self.assertIn("Fetch and follow instructions from", readme)
        self.assertIn("备份当前 Codex 环境", readme)
        self.assertIn(".claude/INSTALL.md", readme)
        self.assertIn("备份当前 Claude Code 环境", readme)
        self.assertIn("CLI 留给高级用户", readme)
        self.assertNotIn("restore-codex-environment", readme)
        self.assertIn("restore-environment.cmd", readme)
        self.assertLess(readme.index("## Daily Use"), readme.index("## Advanced CLI"))
        self.assertLess(readme.index("## 中文说明"), readme.index("### 高级 CLI"))

    def test_readme_declares_agent_skills_and_discovery_boundaries(self) -> None:
        readme = self.read("README.md")
        self.assertIn("## Agent Skills and Discovery", readme)
        self.assertIn("`codex-environment-backup`", readme)
        self.assertIn("`skills/codex-environment-backup/SKILL.md`", readme)
        self.assertIn("`claude-code-environment-backup`", readme)
        self.assertIn("`skills/claude-code-environment-backup/SKILL.md`", readme)
        self.assertIn("Tools that index public GitHub repositories for Agent Skills", readme)
        self.assertIn("does not claim to be listed on SkillsMP", readme)
        self.assertIn("not an official OpenAI plugin", readme)
        self.assertIn("There is no benchmark command", readme)

    def test_readme_declares_plugin_readiness_without_marketplace_claims(self) -> None:
        readme = self.read("README.md")
        normalized = " ".join(readme.split())
        self.assertIn("## Plugin Readiness", readme)
        self.assertIn("`.codex-plugin/plugin.json`", readme)
        self.assertIn("`./skills/`", readme)
        self.assertIn("preparatory discovery and packaging metadata only", normalized)
        self.assertIn("does not install hooks", readme)
        self.assertIn("imply an official marketplace listing", normalized)

    def test_codex_lifecycle_docs_keep_chinese_handoff(self) -> None:
        install = self.read(".codex/INSTALL.md")
        update = self.read(".codex/UPDATE.md")
        uninstall = self.read(".codex/UNINSTALL.md")

        self.assertIn("请重启 Codex App 并回到这个对话", install)
        self.assertIn("备份当前 Codex 环境", install)
        self.assertIn("agent_environment_backup", install)
        self.assertNotIn("doctor --no-run-commands", install)
        self.assertIn("请重启 Codex App 并回到这个对话", update)
        self.assertIn("agent_environment_backup", update)
        self.assertNotIn("doctor --no-run-commands", update)
        self.assertIn("pip uninstall -y codex-environment-backup", update)
        self.assertIn("让它从 skill 列表中移除 codex-environment-backup", uninstall)
        self.assertIn("agent-environment-backup codex-environment-backup", uninstall)

    def test_python_discovery_keeps_candidate_fallbacks(self) -> None:
        for relative_path in (".codex/INSTALL.md", ".codex/UPDATE.md"):
            content = self.read(relative_path)
            self.assertIn("$LASTEXITCODE", content)
            self.assertIn("continue", content)
            self.assertIn("for candidate in python3 python", content)
            self.assertIn('command -v "$candidate"', content)

        uninstall = self.read(".codex/UNINSTALL.md")
        self.assertIn("$LASTEXITCODE", uninstall)
        self.assertIn("for candidate in python3 python", uninstall)


    def test_claude_code_lifecycle_docs_exist(self) -> None:
        for name in ("INSTALL.md", "UPDATE.md"):
            content = self.read(f".claude/{name}")
            self.assertIn("agent_environment_backup", content)
            self.assertIn("--profile claude-code", content)
            self.assertIn("Claude Code", content)
            self.assertIn("skills/claude-code-environment-backup", content.replace("\\", "/"))
        uninstall = self.read(".claude/UNINSTALL.md")
        self.assertIn("Claude Code", uninstall)
        self.assertIn("agent-environment-backup", uninstall)

    def test_claude_local_settings_are_not_committed(self) -> None:
        self.assertFalse((ROOT / ".claude/settings.local.json").exists())
        self.assertIn(".claude/settings.local.json", self.read(".gitignore"))

    def test_codex_lifecycle_docs_use_new_module(self) -> None:
        for name in ("INSTALL.md", "UPDATE.md"):
            content = self.read(f".codex/{name}")
            self.assertIn("agent_environment_backup", content)

    def test_claude_code_skill_exists_and_has_profile(self) -> None:
        skill = self.read("skills/claude-code-environment-backup/SKILL.md")
        self.assertIn("--profile claude-code", skill)
        self.assertIn("agent_environment_backup", skill)
        self.assertIn("Claude Code", skill)
        self.assertNotIn("codex_environment_backup", skill)

    def test_claude_code_skill_has_no_codex_leftovers(self) -> None:
        skill = self.read("skills/claude-code-environment-backup/SKILL.md")
        self.assertNotIn("--codex-home", skill)
        self.assertNotIn("CODEX_HOME", skill)
        self.assertNotIn("CodexBackups", skill)
        self.assertNotIn("restore-codex-environment", skill)
        self.assertIn("ClaudeCodeBackups", skill)
        self.assertIn("restore-environment.", skill)
        self.assertIn("--home", skill)

    def test_codex_skill_uses_new_module_name(self) -> None:
        skill = self.read("skills/codex-environment-backup/SKILL.md")
        self.assertIn("agent_environment_backup", skill)
        self.assertIn("--profile codex", skill)
        self.assertNotIn("restore-codex-environment", skill)
        self.assertIn("restore-environment.cmd", skill)

    def test_skill_ui_metadata_exists_for_both_skills(self) -> None:
        codex = self.read("skills/codex-environment-backup/agents/openai.yaml")
        claude = self.read("skills/claude-code-environment-backup/agents/openai.yaml")
        self.assertIn("interface:", codex)
        self.assertIn('display_name: "Codex Environment Backup"', codex)
        self.assertIn("$codex-environment-backup", codex)
        self.assertIn("interface:", claude)
        self.assertIn('display_name: "Claude Code Environment Backup"', claude)
        self.assertIn("$claude-code-environment-backup", claude)

    def test_pyproject_has_discovery_metadata(self) -> None:
        data = tomllib.loads(self.read("pyproject.toml"))
        project = data["project"]
        self.assertEqual(project["name"], "agent-environment-backup")
        for keyword in (
            "agent-skills",
            "codex",
            "openai-codex",
            "codex-skill",
            "claude-code",
            "environment-backup",
            "backup-restore",
            "sqlite-backup",
        ):
            self.assertIn(keyword, project["keywords"])
        urls = project["urls"]
        self.assertEqual(urls["Repository"], "https://github.com/gaoguobin/codex-environment-backup")
        self.assertEqual(urls["Issues"], "https://github.com/gaoguobin/codex-environment-backup/issues")
        self.assertIn("#readme", urls["Documentation"])

    def test_codex_plugin_manifest_is_valid_and_points_to_skills(self) -> None:
        manifest = json.loads(self.read(".codex-plugin/plugin.json"))
        self.assertEqual(manifest["name"], "agent-environment-backup")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["repository"], "https://github.com/gaoguobin/codex-environment-backup")
        self.assertIn("agent-skills", manifest["keywords"])
        self.assertIn("codex", manifest["keywords"])
        self.assertIn("claude-code", manifest["keywords"])
        self.assertIn("interface", manifest)
        self.assertEqual(manifest["interface"]["category"], "Developer Tools")
        self.assertLessEqual(len(manifest["interface"]["defaultPrompt"]), 3)
        self.assertNotIn("hooks", manifest)
        self.assertNotIn("mcpServers", manifest)
        self.assertNotIn("apps", manifest)


if __name__ == "__main__":
    unittest.main()
