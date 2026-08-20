import os
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ValidateLibraryTests(unittest.TestCase):
    def setUp(self):
        from scripts.validate_library import validate_library

        self.validate_library = validate_library

    def test_repository_is_valid(self):
        self.assertEqual(self.validate_library(ROOT), [])

    def _mutate(self, relative, transform):
        path = ROOT / relative
        original = path.read_text(encoding="utf-8")
        path.write_text(transform(original), encoding="utf-8")
        self.addCleanup(path.write_text, original, encoding="utf-8")
        return ROOT

    def test_detects_broken_markdown_link(self):
        repo = self._mutate("README.md", lambda text: text + "\n[broken](missing.md)\n")
        errors = self.validate_library(repo)
        self.assertTrue(any("link" in error.lower() for error in errors))

    def test_detects_catalog_shelf_mismatch(self):
        repo = self._mutate("library/目録.md", lambda text: text.replace("[仕事](棚/仕事.md)", "[欠落](棚/欠落.md)"))
        errors = self.validate_library(repo)
        self.assertTrue(any("棚" in error for error in errors))

    def test_detects_missing_safety_rule(self):
        repo = self._mutate("library/運用ルール.md", lambda text: text.replace("APIキー", "認証情報"))
        errors = self.validate_library(repo)
        self.assertTrue(any("安全" in error or "API" in error for error in errors))

    def test_detects_fixed_sample_date(self):
        repo = self._mutate("README.md", lambda text: text + "\n例: 2026-08-05\n")
        errors = self.validate_library(repo)
        self.assertTrue(any("日付" in error for error in errors))

    def test_detects_inconsistent_threshold(self):
        repo = self._mutate("README.md", lambda text: text.replace("未転記が5件", "未転記が6件", 1))
        errors = self.validate_library(repo)
        self.assertTrue(any("しきい値" in error for error in errors))

    def test_detects_snippet_safety_mismatch(self):
        repo = self._mutate("snippets/claude-md-snippet.md", lambda text: text.replace("APIキー", "認証情報"))
        errors = self.validate_library(repo)
        self.assertTrue(any("スニペット" in error for error in errors))

    def test_detects_missing_required_file(self):
        source = ROOT / "LICENSE"
        backup = ROOT / "LICENSE.test-backup"
        os.replace(source, backup)

        def restore():
            if backup.exists():
                os.replace(backup, source)

        self.addCleanup(restore)
        errors = self.validate_library(ROOT)
        self.assertTrue(any("必須ファイル" in error and "LICENSE" in error for error in errors))

if __name__ == "__main__":
    unittest.main()
