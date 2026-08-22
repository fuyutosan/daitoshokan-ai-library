"""validate_library の回帰テスト。

本物のリポジトリは読むだけ。改変は毎回 ROOT.parent 配下に作った
UUID付きの複製に対して行うので、途中で中断されても作業ツリーは汚れない。

複製先に tempfile を使わないのは、書き込み可能領域が作業フォルダ配下に
限られるサンドボックス（Codex の workspace-write 等）でも実行できるようにするため。
"""

import hashlib
import io
import shutil
import sys
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.validate_library import (  # noqa: E402
    INSTALLED,
    INSTALLED_CAVEAT,
    SAFETY_CONTRACT,
    SNIPPET_CONTRACT,
    TEMPLATE,
    main,
    validate_instructions_file,
    validate_library,
)

# 安全契約を静かに書き換えられないよう、内容そのものを固定する。
# 契約を変えるときは、validate_library.py とこの値の両方を直すこと。
CONTRACT_DIGEST = "dc345ed402fe4d0852de25f1247910704df6fb390fddc45c34ce561f4909e8ac"

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
}

# 電話番号として検出する形 / してはいけない形
PHONE_DETECTED = (
    "090-1234-5678",
    "03-1234-5678",
    "0120-000-000",
    "090 1234 5678",
    "09012345678",
    "0312345678",
    "+81 90 1234 5678",
    "+81-90-1234-5678",
    "+819012345678",
)
PHONE_NOT_DETECTED = (
    "2026-09-01",
    "2026-09-01T07:27:54",
    "100-0001",
    "978-4-12-345678-9",
    "v1.2.3",
    "1-2-3",
    "5件",
)

SNIPPET_FOR_INSTRUCTIONS = "\n".join(
    ["# 私のCLAUDE.md", "", "## 📚 大図書館（作業コンテキストの書庫）", "", *SNIPPET_CONTRACT, ""]
)


class LibraryTestCase(unittest.TestCase):
    """複製の作成と後片付けを引き受ける土台。"""

    def _new_dir(self) -> Path:
        """ROOT.parent 配下に空フォルダを1つ作る（tempfile を使わない）。"""
        target = ROOT.parent / f".daitoshokan-test-{uuid.uuid4().hex}"
        target.mkdir(parents=False, exist_ok=False)
        self.addCleanup(shutil.rmtree, target, ignore_errors=True)
        return target

    def _copy(self) -> Path:
        repo = self._new_dir() / "repo"
        shutil.copytree(
            ROOT, repo, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc")
        )
        return repo

    def _installed(self, instructions: str = SNIPPET_FOR_INSTRUCTIONS) -> Path:
        """INSTALL.md の手順どおり library/ を置き、常時ファイルにスニペットを貼った状態。"""
        project = self._new_dir() / "myproject"
        project.mkdir()
        shutil.copytree(ROOT / "library", project / "library")
        (project / "CLAUDE.md").write_text(instructions, encoding="utf-8")
        return project

    def _mutate(self, relative, transform) -> Path:
        repo = self._copy()
        path = repo / relative
        path.write_text(transform(path.read_text(encoding="utf-8")), encoding="utf-8")
        return repo

    def assertStops(self, errors, note=""):
        self.assertTrue(errors, f"素通りしている: {note}")

    def assertPasses(self, errors, note=""):
        self.assertEqual(errors, [], f"誤検知: {note}")


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


class ContractIntegrityTests(unittest.TestCase):
    """安全契約そのものが静かに書き換えられていないこと。"""

    def test_contract_digest_is_pinned(self):
        payload = []
        for relative, blocks in sorted(SAFETY_CONTRACT.items()):
            for label, block in blocks:
                payload.append(f"{relative}\x1f{label}\x1f" + "\x1e".join(block))
        payload.extend(SNIPPET_CONTRACT)
        digest = hashlib.sha256("\x1d".join(payload).encode("utf-8")).hexdigest()
        self.assertEqual(
            digest,
            CONTRACT_DIGEST,
            "安全契約が変更されています。意図した変更なら CONTRACT_DIGEST も更新してください",
        )

    def test_every_contract_block_is_non_empty(self):
        for relative, blocks in SAFETY_CONTRACT.items():
            for label, block in blocks:
                with self.subTest(relative=relative, label=label):
                    self.assertTrue(block, "空の契約ブロックは検査にならない")


class ModeTests(LibraryTestCase):
    """モードは推測しない（fail-closed）。"""

    def test_python_api_defaults_to_template(self):
        """モード省略時は最も厳しい template。弱いほうへ倒れない。"""
        self.assertStops(
            validate_library(self._installed()), "libraryだけのプロジェクトがtemplateで合格した"
        )

    def test_deleting_install_md_does_not_downgrade_mode(self):
        """攻撃: INSTALL.md を消してinstalledへ落とす。"""
        repo = self._copy()
        (repo / "INSTALL.md").unlink()
        errors = validate_library(repo, TEMPLATE)
        self.assertTrue(any("必須ファイル" in e and "INSTALL.md" in e for e in errors), errors)

    def test_deleting_snippets_directory_does_not_downgrade_mode(self):
        """攻撃: snippets/ ごと消してinstalledへ落とす。"""
        repo = self._copy()
        shutil.rmtree(repo / "snippets")
        errors = validate_library(repo, TEMPLATE)
        self.assertTrue(
            any("必須ファイル" in e and "snippets/" in e for e in errors), errors
        )

    def test_cli_without_path_uses_template(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["--mode", "template"])
        self.assertEqual(code, 0)
        self.assertIn("template", buffer.getvalue())

    def test_cli_with_path_uses_installed(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main([str(self._installed())])
        self.assertEqual(code, 0)
        self.assertIn("installed", buffer.getvalue())

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_library(ROOT, "ゆるい")


class TemplateModeTests(LibraryTestCase):
    def test_repository_is_valid(self):
        self.assertPasses(validate_library(ROOT))

    def test_copy_of_repository_is_valid(self):
        self.assertPasses(validate_library(self._copy()))

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
            "library/docs/安全とプライバシー.md",
            "library/docs/複数AIで使う場合.md",
        ):
            with self.subTest(relative=relative):
                repo = self._copy()
                (repo / relative).unlink()
                errors = validate_library(repo)
                self.assertTrue(
                    any("必須ファイル" in e and relative in e for e in errors), errors
                )

    def test_detects_snippet_install_mismatch(self):
        repo = self._mutate(
            "snippets/claude-md-snippet.md", lambda t: t.replace("**安全**", "**あんぜん**")
        )
        self.assertTrue(any("スニペット" in e or "安全契約" in e for e in validate_library(repo)))

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


class SafetyContractTests(LibraryTestCase):
    """安全契約ブロックが原文どおり可視本文にあること。"""

    def test_detects_deleted_block(self):
        for relative, needle in (
            ("library/運用ルール.md", "### 安全とプライバシー"),
            ("library/運用ルール.md", "### 書き手は1つだけ"),
            ("library/docs/安全とプライバシー.md", "## 記録禁止"),
            ("library/docs/複数AIで使う場合.md", "- 正本は1つだけにする"),
            ("commands/log.md", "- APIキー"),
            ("commands/tidy.md", "禁止情報"),
            ("README.md", "## 🔒 安全に使う"),
        ):
            with self.subTest(relative=relative, needle=needle):
                repo = self._mutate(
                    relative,
                    lambda t: "\n".join(
                        line for line in t.split("\n") if needle not in line
                    ),
                )
                self.assertStops(
                    [e for e in validate_library(repo) if "安全契約" in e], f"{relative}/{needle}"
                )

    def test_detects_inverted_prohibition(self):
        repo = self._mutate("library/運用ルール.md", lambda t: t.replace("は書かない", "を必ず書く"))
        self.assertStops([e for e in validate_library(repo) if "安全契約" in e])

    def test_detects_permissive_rewrite(self):
        repo = self._mutate("library/運用ルール.md", lambda t: t.replace("は書かない", "は書いてよい"))
        self.assertStops([e for e in validate_library(repo) if "安全契約" in e])

    def test_paraphrase_is_rejected_by_design(self):
        """同義の言い換えも不合格。契約は原文固定という仕様であり、誤検知ではない。"""
        repo = self._mutate("library/運用ルール.md", lambda t: t.replace("は書かない", "は保存しない"))
        self.assertStops([e for e in validate_library(repo) if "安全契約" in e])

    # --- Codex 2巡目の新攻撃 ---------------------------------------------

    def test_detects_contract_hidden_in_html_comment(self):
        """攻撃: 契約をHTMLコメントへ退避し、可視側を許可に書き換える。"""
        def hide(text):
            start = text.index("### 安全とプライバシー")
            end = text.index("## 2. 転記マーク")
            body = text[start:end]
            return (
                text[:start]
                + "<!--\n"
                + body
                + "-->\n\n### 安全とプライバシー\n\n- APIキーは書いてよい\n\n"
                + text[end:]
            )

        repo = self._mutate("library/運用ルール.md", hide)
        self.assertStops([e for e in validate_library(repo) if "安全契約" in e])

    def test_detects_contract_hidden_in_code_fence(self):
        """攻撃: 契約をコードブロックへ退避する。"""
        def fence(text):
            start = text.index("### 安全とプライバシー")
            end = text.index("## 2. 転記マーク")
            return text[:start] + "```\n" + text[start:end] + "```\n\n" + text[end:]

        repo = self._mutate("library/運用ルール.md", fence)
        self.assertStops([e for e in validate_library(repo) if "安全契約" in e])

    def test_detects_contract_hidden_in_blockquote(self):
        """攻撃: 契約を引用へ退避する。"""
        def quote(text):
            return "\n".join(
                "> " + line if line.startswith("- APIキー") else line
                for line in text.split("\n")
            )

        repo = self._mutate("library/運用ルール.md", quote)
        self.assertStops([e for e in validate_library(repo) if "安全契約" in e])

    def test_detects_trailing_re_negation_on_contract_line(self):
        """攻撃: 契約行の末尾に「ただし〜書いてよい」を継ぎ足す。"""
        repo = self._mutate(
            "library/運用ルール.md",
            lambda t: t.replace(
                "顧客の非公開情報は書かない",
                "顧客の非公開情報は書かない。ただし必要なら書いてよい",
            ),
        )
        self.assertStops([e for e in validate_library(repo) if "安全契約" in e])

    def test_detects_negation_of_a_different_target(self):
        """攻撃: 1行の中で禁止対象は保存し、別対象だけ「書かない」とする。"""
        repo = self._mutate(
            "library/運用ルール.md",
            lambda t: t.replace(
                "- APIキー、トークン、パスワード、Cookie/セッション、`.env`や認証ファイルの中身、金融情報、住所・電話・メール、顧客の非公開情報は書かない",
                "- APIキー、トークン、パスワードは保存する。なお絵文字は書かない",
            ),
        )
        self.assertStops([e for e in validate_library(repo) if "安全契約" in e])

    def test_detects_added_permission_line(self):
        """攻撃: 契約は残したまま、別行に許可文を足す。"""
        for extra in (
            "- 例外としてAPIキーの記録を許可する",
            "- 急ぎのときはトークンを書いてよい",
            "- 個人情報は保存してよい",
        ):
            with self.subTest(extra=extra):
                repo = self._mutate("library/運用ルール.md", lambda t: t + f"\n{extra}\n")
                self.assertStops(
                    [e for e in validate_library(repo) if "安全契約" in e], extra
                )

    def test_detects_multi_ai_document_reversal(self):
        """攻撃: 複数AI文書を「〜する必要はない」へ反転する。"""
        for before, after in (
            ("書き手を1エージェントにしてください", "書き手を1エージェントにする必要はありません"),
            ("- 正本は1つだけにする", "- 正本は1つだけにする必要はない"),
            ("- stagingを正本の内側に作らない", "- stagingを正本の内側に作ってよい"),
        ):
            with self.subTest(before=before):
                repo = self._mutate(
                    "library/docs/複数AIで使う場合.md", lambda t: t.replace(before, after)
                )
                self.assertStops(
                    [e for e in validate_library(repo) if "安全契約" in e], before
                )


class LibraryLayoutTests(LibraryTestCase):
    """library/ はMarkdown専用。非Markdownは抜け道になる。"""

    def test_detects_non_markdown_file(self):
        repo = self._copy()
        (repo / "library" / "棚" / "credentials.txt").write_text("メモ\n", encoding="utf-8")
        self.assertTrue(any("構成" in e for e in validate_library(repo)))

    def test_detects_secret_in_non_markdown_file(self):
        """攻撃: .txt に鍵を置いてMarkdown限定の走査を回避する。"""
        repo = self._copy()
        (repo / "library" / "棚" / "credentials.txt").write_text(
            "key: sk-proj-" + "C" * 40 + "\n", encoding="utf-8"
        )
        errors = validate_library(repo)
        self.assertTrue(any("秘密情報" in e for e in errors), errors)

    def test_detects_secret_in_non_markdown_file_installed(self):
        project = self._installed()
        (project / "library" / "棚" / "notes.txt").write_text(
            "ghp_" + "Z" * 36 + "\n", encoding="utf-8"
        )
        errors = validate_library(project, INSTALLED)
        self.assertTrue(any("秘密情報" in e for e in errors), errors)

    def test_binary_file_does_not_crash(self):
        repo = self._copy()
        (repo / "library" / "棚" / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
        errors = validate_library(repo)
        self.assertTrue(any("構成" in e for e in errors), errors)


class SecretScanTests(LibraryTestCase):
    def test_detects_secrets_in_markdown(self):
        for label, value in SECRET_SAMPLES.items():
            with self.subTest(label=label):
                repo = self._mutate("library/棚/仕事.md", lambda t: t + f"\n- メモ: {value}\n")
                self.assertStops(
                    [e for e in validate_library(repo) if "秘密情報" in e], label
                )

    def test_secret_report_never_prints_the_value(self):
        for label, value in SECRET_SAMPLES.items():
            with self.subTest(label=label):
                repo = self._mutate("library/棚/仕事.md", lambda t: t + f"\n- メモ: {value}\n")
                for error in validate_library(repo):
                    self.assertNotIn(value, error)

    def test_detects_phone_number_formats(self):
        for value in PHONE_DETECTED:
            with self.subTest(value=value):
                repo = self._mutate("library/棚/仕事.md", lambda t: t + f"\n- 連絡先: {value}\n")
                errors = [e for e in validate_library(repo) if "電話番号" in e]
                self.assertStops(errors, value)

    def test_does_not_flag_non_phone_numbers(self):
        for value in PHONE_NOT_DETECTED:
            with self.subTest(value=value):
                repo = self._mutate("library/棚/仕事.md", lambda t: t + f"\n- 値: {value}\n")
                errors = [e for e in validate_library(repo) if "秘密情報" in e]
                self.assertPasses(errors, value)

    def test_example_domain_is_not_flagged(self):
        repo = self._mutate("README.md", lambda t: t + "\n連絡先の例: taro@example.com\n")
        self.assertPasses([e for e in validate_library(repo) if "秘密情報" in e])


class InstalledModeTests(LibraryTestCase):
    """library/ だけを導入した利用者プロジェクトの検証。"""

    def test_installed_library_is_valid(self):
        self.assertPasses(validate_library(self._installed(), INSTALLED))

    def test_installed_mode_does_not_demand_template_files(self):
        errors = validate_library(self._installed(), INSTALLED)
        self.assertEqual([e for e in errors if "必須ファイル" in e], [])

    def test_operating_rules_docs_links_resolve_after_install(self):
        project = self._installed()
        self.assertPasses(
            [e for e in validate_library(project, INSTALLED) if e.startswith("link")]
        )

    def test_installed_mode_detects_secrets_in_journal(self):
        project = self._installed()
        (project / "library" / "日誌" / "2026-08.md").write_text(
            "## 2026-08-20 作業 ｜棚:仕事\n- token: ghp_" + "Z" * 36 + "\n", encoding="utf-8"
        )
        self.assertStops(
            [e for e in validate_library(project, INSTALLED) if "秘密情報" in e]
        )

    def test_installed_mode_detects_catalog_mismatch(self):
        project = self._installed()
        (project / "library" / "棚" / "仕事.md").unlink()
        self.assertStops(validate_library(project, INSTALLED))

    def test_installed_mode_detects_contract_damage(self):
        project = self._installed()
        rules = project / "library" / "運用ルール.md"
        rules.write_text(
            rules.read_text(encoding="utf-8").replace("は書かない", "を必ず書く"),
            encoding="utf-8",
        )
        self.assertStops(
            [e for e in validate_library(project, INSTALLED) if "安全契約" in e]
        )

    def test_installed_mode_ignores_unrelated_project_files(self):
        project = self._installed()
        (project / "notes.md").write_text("[壊れたリンク](nowhere.md)\n", encoding="utf-8")
        self.assertPasses(validate_library(project, INSTALLED))


class InstructionsFileTests(LibraryTestCase):
    """常時読み込みファイルの検査（既定では未検査であることを明示する）。"""

    def test_valid_instructions_file_passes(self):
        project = self._installed()
        self.assertPasses(validate_instructions_file(project / "CLAUDE.md"))

    def test_empty_instructions_file_is_detected(self):
        """攻撃: 常時ファイルが空でもinstalledが合格していた。"""
        project = self._installed(instructions="# からっぽ\n")
        self.assertStops(validate_instructions_file(project / "CLAUDE.md"))
        self.assertStops(
            validate_library(project, INSTALLED, project / "CLAUDE.md")
        )

    def test_missing_instructions_file_is_detected(self):
        project = self._installed()
        self.assertStops(validate_instructions_file(project / "AGENTS.md"))

    def test_instructions_contract_in_comment_is_not_counted(self):
        project = self._installed(instructions="<!--\n" + "\n".join(SNIPPET_CONTRACT) + "\n-->\n")
        self.assertStops(validate_instructions_file(project / "CLAUDE.md"))

    def test_caveat_is_printed_when_instructions_not_checked(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main([str(self._installed())])
        self.assertEqual(code, 0)
        self.assertIn(INSTALLED_CAVEAT, buffer.getvalue())

    def test_no_caveat_when_instructions_checked(self):
        project = self._installed()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main([str(project), "--instructions-file", str(project / "CLAUDE.md")])
        self.assertEqual(code, 0)
        self.assertNotIn(INSTALLED_CAVEAT, buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
