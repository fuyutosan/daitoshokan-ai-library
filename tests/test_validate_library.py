"""validate_library の回帰テスト。

本物のリポジトリは読むだけ。改変は毎回テンポラリの複製に対して行うので、
途中で中断されても作業ツリーは汚れない。
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.validate_library import validate_library  # noqa: E402


class ValidateLibraryTests(unittest.TestCase):
    def _copy(self) -> Path:
        """リポジトリをテンポラリへ複製し、そのパスを返す（後片付けは自動）。"""
        tmp = tempfile.mkdtemp(prefix="daitoshokan-test-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        repo = Path(tmp) / "repo"
        shutil.copytree(
            ROOT,
            repo,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
        )
        return repo

    def _mutate(self, relative, transform) -> Path:
        repo = self._copy()
        path = repo / relative
        path.write_text(transform(path.read_text(encoding="utf-8")), encoding="utf-8")
        return repo

    # --- 正常系 ---------------------------------------------------------

    def test_repository_is_valid(self):
        self.assertEqual(validate_library(ROOT), [])

    def test_copy_of_repository_is_valid(self):
        self.assertEqual(validate_library(self._copy()), [])

    def test_example_domain_is_not_flagged_as_secret(self):
        repo = self._mutate("README.md", lambda t: t + "\n連絡先の例: taro@example.com\n")
        self.assertEqual([e for e in validate_library(repo) if "秘密情報" in e], [])

    # --- 構造チェック ---------------------------------------------------

    def test_detects_broken_markdown_link(self):
        repo = self._mutate("README.md", lambda t: t + "\n[broken](missing.md)\n")
        self.assertTrue(any(e.startswith("link") for e in validate_library(repo)))

    def test_detects_link_to_directory(self):
        repo = self._mutate("README.md", lambda t: t + "\n[dir](library/棚)\n")
        self.assertTrue(any(e.startswith("link") for e in validate_library(repo)))

    def test_detects_catalog_shelf_mismatch(self):
        repo = self._mutate(
            "library/目録.md", lambda t: t.replace("[仕事](棚/仕事.md)", "[欠落](棚/欠落.md)")
        )
        self.assertTrue(any("棚" in e for e in validate_library(repo)))

    def test_detects_missing_required_file(self):
        repo = self._copy()
        (repo / "LICENSE").unlink()
        errors = validate_library(repo)
        self.assertTrue(any("必須ファイル" in e and "LICENSE" in e for e in errors))

    def test_detects_missing_operating_files(self):
        """スニペット・運用ルール・コマンドが消えたら落ちること（fail-open の防止）。"""
        for relative in (
            "snippets/claude-md-snippet.md",
            "library/運用ルール.md",
            "commands/log.md",
            "commands/tidy.md",
        ):
            with self.subTest(relative=relative):
                repo = self._copy()
                (repo / relative).unlink()
                errors = validate_library(repo)
                self.assertTrue(
                    any("必須ファイル" in e and relative in e for e in errors),
                    f"{relative} の欠落を検出できていない: {errors}",
                )

    # --- しきい値 -------------------------------------------------------

    def test_detects_inconsistent_threshold(self):
        repo = self._mutate("README.md", lambda t: t.replace("5件たまったら", "6件たまったら", 1))
        self.assertTrue(any("しきい値" in e for e in validate_library(repo)))

    def test_detects_threshold_drift_in_snippet_only(self):
        """AIが実際に読むスニペット側だけがズレた場合も検出すること。"""
        repo = self._copy()
        for relative in ("INSTALL.md", "snippets/claude-md-snippet.md"):
            path = repo / relative
            path.write_text(
                path.read_text(encoding="utf-8").replace("**5件以上**", "**8件以上**"),
                encoding="utf-8",
            )
        self.assertTrue(any("しきい値" in e for e in validate_library(repo)))

    def test_zero_count_phrase_is_not_a_threshold(self):
        """「0件なら即終了」のような別文脈を誤検出しないこと。"""
        repo = self._mutate("commands/tidy.md", lambda t: t + "\n- 対象が0件なら何もしない\n")
        self.assertEqual([e for e in validate_library(repo) if "しきい値" in e], [])

    # --- 安全 -----------------------------------------------------------

    def test_detects_missing_safety_rule(self):
        repo = self._mutate("library/運用ルール.md", lambda t: t.replace("APIキー", "認証情報"))
        self.assertTrue(any("安全文" in e for e in validate_library(repo)))

    def test_detects_snippet_safety_mismatch(self):
        repo = self._mutate(
            "snippets/claude-md-snippet.md", lambda t: t.replace("APIキー", "認証情報")
        )
        self.assertTrue(any("スニペット" in e for e in validate_library(repo)))

    def test_detects_secrets_in_markdown(self):
        cases = {
            "Anthropic APIキー": "sk-ant-api03-" + "A" * 40,
            "GitHubトークン": "ghp_" + "B" * 36,
            "AWSアクセスキー": "AKIA" + "C" * 16,
            "Slackトークン": "xoxb-" + "1" * 12 + "-abcdef",
            "Google APIキー": "AIza" + "D" * 35,
            "秘密鍵": "-----BEGIN RSA PRIVATE KEY-----",
            "メールアドレス": "taro@realdomain.co.jp",
            "電話番号": "090-1234-5678",
        }
        for label, value in cases.items():
            with self.subTest(label=label):
                repo = self._mutate("library/棚/仕事.md", lambda t: t + f"\n- メモ: {value}\n")
                errors = [e for e in validate_library(repo) if "秘密情報" in e]
                self.assertTrue(errors, f"{label} を検出できていない")
                self.assertTrue(any(label in e for e in errors), errors)

    def test_secret_report_never_prints_the_value(self):
        """報告に値そのものを含めない（このリポジトリ自身のルール）。"""
        secret = "ghp_" + "E" * 36
        repo = self._mutate("library/棚/仕事.md", lambda t: t + f"\n- token: {secret}\n")
        for error in validate_library(repo):
            self.assertNotIn(secret, error)

    # --- 日付 -----------------------------------------------------------

    def test_detects_fixed_sample_date(self):
        repo = self._mutate("README.md", lambda t: t + "\n例: 2026-08-05\n")
        self.assertTrue(any("日付" in e for e in validate_library(repo)))


if __name__ == "__main__":
    unittest.main()
