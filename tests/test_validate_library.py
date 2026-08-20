"""validate_library の回帰テスト。

本物のリポジトリは読むだけ。改変は毎回 ROOT.parent 配下に作った
UUID付きの複製に対して行うので、途中で中断されても作業ツリーは汚れない。

複製先に tempfile を使わないのは、書き込み可能領域が作業フォルダ配下に
限られるサンドボックス（Codex の workspace-write 等）でも実行できるようにするため。
"""

import shutil
import sys
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.validate_library import validate_library  # noqa: E402


# 検体はすべてダミー。実在の資格情報ではない。
SECRET_SAMPLES = {
    "Anthropic APIキー": "sk-ant-api03-" + "A" * 40,
    "OpenAI APIキー": "sk-" + "B" * 40,
    "OpenAI APIキー(proj形式)": "sk-proj-" + "C" * 40,
    "GitHubトークン": "ghp_" + "D" * 36,
    "GitHubトークン(fine-grained)": "github_pat_" + "E" * 22 + "_" + "F" * 59,
    "AWSアクセスキー": "AKIA" + "G" * 16,
    "Slackトークン": "xoxb-" + "1" * 12 + "-abcdef",
    "Google APIキー": "AIza" + "H" * 35,
    "秘密鍵": "-----BEGIN RSA PRIVATE KEY-----",
    "メールアドレス": "taro@realdomain.co.jp",
    "電話番号": "090-1234-5678",
}


class LibraryTestCase(unittest.TestCase):
    """複製の作成と後片付けを引き受ける土台。"""

    def _new_dir(self) -> Path:
        """ROOT.parent 配下に空フォルダを1つ作る（tempfile を使わない）。"""
        target = ROOT.parent / f".daitoshokan-test-{uuid.uuid4().hex}"
        target.mkdir(parents=False, exist_ok=False)
        self.addCleanup(shutil.rmtree, target, ignore_errors=True)
        return target

    def _copy(self) -> Path:
        """テンプレート一式を複製し、そのパスを返す。"""
        repo = self._new_dir() / "repo"
        shutil.copytree(
            ROOT,
            repo,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
        )
        return repo

    def _installed(self) -> Path:
        """INSTALL.md の手順どおり library/ だけを置いた利用者プロジェクトを作る。"""
        project = self._new_dir() / "myproject"
        project.mkdir()
        shutil.copytree(ROOT / "library", project / "library")
        (project / "CLAUDE.md").write_text("# 利用者のCLAUDE.md\n", encoding="utf-8")
        return project

    def _mutate(self, relative, transform) -> Path:
        repo = self._copy()
        path = repo / relative
        path.write_text(transform(path.read_text(encoding="utf-8")), encoding="utf-8")
        return repo


class TemporaryCopyTests(LibraryTestCase):
    """複製方法そのものの回帰（サンドボックス互換）。"""

    def test_copy_is_created_under_root_parent(self):
        repo = self._copy()
        self.assertEqual(repo.parent.parent, ROOT.parent)
        self.assertTrue(repo.is_dir())

    def test_two_copies_do_not_collide(self):
        self.assertNotEqual(self._copy(), self._copy())

    def test_real_repository_is_never_modified(self):
        before = (ROOT / "LICENSE").read_text(encoding="utf-8")
        repo = self._copy()
        (repo / "LICENSE").unlink()
        self.assertEqual((ROOT / "LICENSE").read_text(encoding="utf-8"), before)


class TemplateModeTests(LibraryTestCase):
    """公開テンプレ全体の検証。"""

    def test_repository_is_valid(self):
        self.assertEqual(validate_library(ROOT), [])

    def test_copy_of_repository_is_valid(self):
        self.assertEqual(validate_library(self._copy()), [])

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

    def test_detects_snippet_safety_mismatch(self):
        repo = self._mutate(
            "snippets/claude-md-snippet.md", lambda t: t.replace("APIキー", "認証情報")
        )
        self.assertTrue(any("スニペット" in e for e in validate_library(repo)))

    def test_detects_fixed_sample_date(self):
        repo = self._mutate("README.md", lambda t: t + "\n例: 2026-08-05\n")
        self.assertTrue(any("日付" in e for e in validate_library(repo)))


class ThresholdTests(LibraryTestCase):
    def test_detects_inconsistent_threshold(self):
        repo = self._mutate("README.md", lambda t: t.replace("5件たまったら", "6件たまったら", 1))
        self.assertTrue(any("しきい値" in e for e in validate_library(repo)))

    def test_detects_threshold_drift_in_snippet_only(self):
        repo = self._copy()
        for relative in ("INSTALL.md", "snippets/claude-md-snippet.md"):
            path = repo / relative
            path.write_text(
                path.read_text(encoding="utf-8").replace("**5件以上**", "**8件以上**"),
                encoding="utf-8",
            )
        self.assertTrue(any("しきい値" in e for e in validate_library(repo)))

    def test_zero_count_phrase_is_not_a_threshold(self):
        repo = self._mutate("commands/tidy.md", lambda t: t + "\n- 対象が0件なら何もしない\n")
        self.assertEqual([e for e in validate_library(repo) if "しきい値" in e], [])


class SafetyMeaningTests(LibraryTestCase):
    """安全ルールが『語の存在』ではなく『意味』として残っているかの回帰。"""

    def test_detects_inverted_prohibition(self):
        """禁止文を「必ず書く」へ反転したら落ちること。"""
        repo = self._mutate("library/運用ルール.md", lambda t: t.replace("は書かない", "を必ず書く"))
        errors = validate_library(repo)
        self.assertTrue(errors, "反転が素通りしている")
        self.assertTrue(any("安全" in e for e in errors), errors)

    def test_detects_permissive_rewrite(self):
        """「書いてよい」への書き換えも落ちること。"""
        repo = self._mutate("library/運用ルール.md", lambda t: t.replace("は書かない", "は書いてよい"))
        self.assertTrue(any("安全" in e for e in validate_library(repo)))

    def test_detects_gutted_safety_document(self):
        """安全とプライバシーを見出しだけにしたら落ちること。"""
        repo = self._copy()
        (repo / "library" / "docs" / "安全とプライバシー.md").write_text(
            "# 安全とプライバシー\n", encoding="utf-8"
        )
        self.assertTrue(any("安全" in e for e in validate_library(repo)))

    def test_detects_gutted_multi_ai_document(self):
        """複数AIで使う場合を見出しだけにしたら落ちること。"""
        repo = self._copy()
        (repo / "library" / "docs" / "複数AIで使う場合.md").write_text(
            "# 複数AIで使う場合\n", encoding="utf-8"
        )
        self.assertTrue(any("安全" in e for e in validate_library(repo)))

    def test_detects_deleted_readme_safety_section(self):
        """README の安全節を削除したら落ちること。"""
        repo = self._mutate(
            "README.md", lambda t: t.split("## 🔒 安全に使う")[0] + "\n## ❓ FAQ\n"
        )
        self.assertTrue(any("安全" in e for e in validate_library(repo)))

    def test_detects_deleted_log_command_safety_line(self):
        repo = self._mutate(
            "commands/log.md",
            lambda t: "\n".join(line for line in t.split("\n") if "APIキー" not in line),
        )
        self.assertTrue(any("安全" in e for e in validate_library(repo)))

    def test_detects_deleted_tidy_command_safety_line(self):
        repo = self._mutate(
            "commands/tidy.md",
            lambda t: "\n".join(line for line in t.split("\n") if "禁止情報" not in line),
        )
        self.assertTrue(any("安全" in e for e in validate_library(repo)))

    def test_detects_deleted_single_writer_rule(self):
        """単一書き手のルールを消したら落ちること。"""
        repo = self._mutate(
            "library/運用ルール.md", lambda t: t.replace("### 書き手は1つだけ", "### むかしの話")
        )
        self.assertTrue(any("安全" in e for e in validate_library(repo)))

    def test_detects_deleted_snippet_writer_rule(self):
        """スニペットから書き手ルールを消したら落ちること。"""
        repo = self._copy()
        for relative in ("INSTALL.md", "snippets/claude-md-snippet.md"):
            path = repo / relative
            path.write_text(
                path.read_text(encoding="utf-8").replace("**書き手**", "**むかし**"),
                encoding="utf-8",
            )
        self.assertTrue(any("安全" in e for e in validate_library(repo)))

    def test_detects_missing_safety_terms(self):
        repo = self._mutate("library/運用ルール.md", lambda t: t.replace("APIキー", "認証情報"))
        self.assertTrue(any("安全" in e for e in validate_library(repo)))


class SecretScanTests(LibraryTestCase):
    def test_detects_secrets_in_markdown(self):
        for label, value in SECRET_SAMPLES.items():
            with self.subTest(label=label):
                repo = self._mutate("library/棚/仕事.md", lambda t: t + f"\n- メモ: {value}\n")
                errors = [e for e in validate_library(repo) if "秘密情報" in e]
                self.assertTrue(errors, f"{label} を検出できていない")

    def test_secret_report_never_prints_the_value(self):
        for label, value in SECRET_SAMPLES.items():
            with self.subTest(label=label):
                repo = self._mutate("library/棚/仕事.md", lambda t: t + f"\n- メモ: {value}\n")
                for error in validate_library(repo):
                    self.assertNotIn(value, error)

    def test_example_domain_is_not_flagged_as_secret(self):
        repo = self._mutate("README.md", lambda t: t + "\n連絡先の例: taro@example.com\n")
        self.assertEqual([e for e in validate_library(repo) if "秘密情報" in e], [])


class InstalledModeTests(LibraryTestCase):
    """library/ だけを導入した利用者プロジェクトの検証。"""

    def test_installed_library_is_valid(self):
        self.assertEqual(validate_library(self._installed(), mode="installed"), [])

    def test_installed_mode_is_autodetected(self):
        self.assertEqual(validate_library(self._installed()), [])

    def test_installed_mode_does_not_demand_template_files(self):
        errors = validate_library(self._installed(), mode="installed")
        self.assertEqual([e for e in errors if "必須ファイル" in e], [])

    def test_operating_rules_docs_links_resolve_after_install(self):
        """運用ルールから参照する文書が、導入先でもリンク切れにならないこと。"""
        project = self._installed()
        self.assertEqual([e for e in validate_library(project) if e.startswith("link")], [])

    def test_installed_mode_detects_secrets_in_journal(self):
        project = self._installed()
        journal = project / "library" / "日誌" / "2026-08.md"
        journal.write_text(
            "## 2026-08-20 作業 ｜棚:仕事\n- token: ghp_" + "Z" * 36 + "\n", encoding="utf-8"
        )
        self.assertTrue(any("秘密情報" in e for e in validate_library(project)))

    def test_installed_mode_detects_catalog_mismatch(self):
        project = self._installed()
        (project / "library" / "棚" / "仕事.md").unlink()
        self.assertTrue(any("棚" in e for e in validate_library(project)))

    def test_installed_mode_detects_inverted_prohibition(self):
        project = self._installed()
        rules = project / "library" / "運用ルール.md"
        rules.write_text(
            rules.read_text(encoding="utf-8").replace("は書かない", "を必ず書く"), encoding="utf-8"
        )
        self.assertTrue(any("安全" in e for e in validate_library(project)))

    def test_installed_mode_ignores_unrelated_project_files(self):
        """利用者の他のファイルまで検査しにいかないこと。"""
        project = self._installed()
        (project / "notes.md").write_text("[壊れたリンク](nowhere.md)\n", encoding="utf-8")
        self.assertEqual(validate_library(project), [])


if __name__ == "__main__":
    unittest.main()
