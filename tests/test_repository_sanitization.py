from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositorySanitizationTests(unittest.TestCase):
    def text_files(self) -> list[Path]:
        suffixes = {
            ".md",
            ".py",
            ".toml",
            ".yml",
            ".yaml",
            ".json",
            ".txt",
            ".ps1",
            ".sh",
            ".cmd",
        }
        ignored_parts = {
            ".git",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            ".venv",
            "__pycache__",
            "test_tmp_runtime",
        }
        return [
            path
            for path in ROOT.rglob("*")
            if path.is_file()
            and path.relative_to(ROOT).as_posix() != "tests/test_repository_sanitization.py"
            and path.suffix.lower() in suffixes
            and ignored_parts.isdisjoint(path.relative_to(ROOT).parts)
        ]

    def test_internal_agent_plans_are_not_committed(self) -> None:
        superpowers_docs = ROOT / "docs" / "superpowers"
        has_files = (
            any(path.is_file() for path in superpowers_docs.rglob("*"))
            if superpowers_docs.exists()
            else False
        )
        self.assertFalse(has_files)

    def test_no_local_absolute_paths_are_committed(self) -> None:
        local_path_patterns = [
            re.compile(r"\b[A-Z]:\\(?:Users|Git_ECU|OneDrive|tmp)\\", re.IGNORECASE),
            re.compile(r"/Users/[^/\s]+/"),
            re.compile(r"/home/[^/\s]+/"),
        ]
        allowed_placeholders = (
            r"C:\path\to",
            r"C:\Users\<you>",
        )

        findings: list[str] = []
        for path in self.text_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line_number, line in enumerate(text.splitlines(), start=1):
                if any(placeholder in line for placeholder in allowed_placeholders):
                    continue
                if any(pattern.search(line) for pattern in local_path_patterns):
                    relative = path.relative_to(ROOT).as_posix()
                    findings.append(f"{relative}:{line_number}: {line.strip()}")

        self.assertEqual([], findings)


if __name__ == "__main__":
    unittest.main()
