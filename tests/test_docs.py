from __future__ import annotations

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
        self.assertIn("Fetch and follow instructions from", readme)
        self.assertIn("备份当前 Codex 环境", readme)
        self.assertIn("CLI 留给高级用户", readme)
        self.assertLess(readme.index("## Daily Use"), readme.index("## Advanced CLI"))
        self.assertLess(readme.index("## 中文说明"), readme.index("### 高级 CLI"))

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
        self.assertIn("让它从 skill 列表中移除 codex-environment-backup", uninstall)

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
        uninstall = self.read(".claude/UNINSTALL.md")
        self.assertIn("Claude Code", uninstall)
        self.assertIn("agent-environment-backup", uninstall)

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

    def test_codex_skill_uses_new_module_name(self) -> None:
        skill = self.read("skills/codex-environment-backup/SKILL.md")
        self.assertIn("agent_environment_backup", skill)
        self.assertIn("--profile codex", skill)


if __name__ == "__main__":
    unittest.main()
