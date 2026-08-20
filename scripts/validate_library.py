"""大図書館の静的検査（標準ライブラリのみ）。

2つのモードがある。

- template  … 公開テンプレート全体（このリポジトリ）を検査する
- installed … 利用者のプロジェクトに導入された `library/` だけを検査する

省略時は中身を見て自動判定する。使い方:

    python scripts/validate_library.py                    # このリポジトリ
    python scripts/validate_library.py <プロジェクト>      # 導入済みのlibrary
    python scripts/validate_library.py <パス> --mode installed
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


TEMPLATE = "template"
INSTALLED = "installed"

# 導入先にも必ずコピーされるもの（library/ の中身）
LIBRARY_REQUIRED_FILES = (
    "library/目録.md",
    "library/運用ルール.md",
    "library/docs/安全とプライバシー.md",
    "library/docs/複数AIで使う場合.md",
)
# 公開テンプレートにだけ存在するもの
TEMPLATE_ONLY_REQUIRED_FILES = (
    "README.md",
    "INSTALL.md",
    "LICENSE",
    "CHANGELOG.md",
    "snippets/claude-md-snippet.md",
    "commands/log.md",
    "commands/tidy.md",
)

SAFETY_TERMS = (
    "APIキー",
    "トークン",
    "パスワード",
    "Cookie/セッション",
    ".env",
    "金融情報",
    "住所・電話・メール",
    "生ログ",
)
# 安全語がそろっているかを見るファイル
LIBRARY_SAFETY_TERM_FILES = ("library/運用ルール.md",)
TEMPLATE_SAFETY_TERM_FILES = ("INSTALL.md", "snippets/claude-md-snippet.md")

# 「語がある」ではなく「その意味の文が残っている」ことを見る。
# 反転（〜を必ず書く）や節ごと削除は、ここで落ちる。
LIBRARY_REQUIRED_STATEMENTS = {
    "library/運用ルール.md": (
        ("安全とプライバシーの節", r"^### 安全とプライバシー\s*$"),
        ("秘密情報を書かない旨", r"APIキー[^\n]*は書かない"),
        ("生ログを書かない旨", r"生ログ[^\n]*は書かない"),
        ("漏えい時に無効化を最優先する旨", r"漏えい[^\n]*無効化"),
        ("書き手を1つに絞る節", r"^### 書き手は1つだけ\s*$"),
        ("書き手を1つに決める旨", r"AIは1つに決める"),
    ),
    "library/docs/安全とプライバシー.md": (
        ("Git管理から外す案内", r"^## GitHubへ公開しない\s*$"),
        ("記録禁止の節", r"^## 記録禁止\s*$"),
        ("記録しない旨", r"APIキー[^\n]*は記録しません"),
        ("漏えい時の節", r"^## 漏えい時\s*$"),
        ("資格情報を無効化する旨", r"資格情報を直ちに無効化"),
    ),
    "library/docs/複数AIで使う場合.md": (
        ("書き手を1エージェントにする旨", r"書き手を1エージェント"),
        ("正本を1つにする旨", r"正本は1つだけにする"),
        ("stagingを正本の内側に作らない旨", r"stagingを正本の内側に作らない"),
    ),
}
TEMPLATE_REQUIRED_STATEMENTS = {
    "README.md": (
        ("安全に使う節", r"^## 🔒 安全に使う\s*$"),
        ("書かないでほしい旨", r"書かないでください"),
    ),
    "commands/log.md": (
        ("秘密情報を書かない旨", r"APIキー[^\n]*書かない"),
    ),
    "commands/tidy.md": (
        ("禁止情報を転記しない旨", r"禁止情報[^\n]*転記しない"),
    ),
    "snippets/claude-md-snippet.md": (
        ("常時指示の安全行", r"\*\*安全\*\*[^\n]*記録しない"),
        ("常時指示の書き手行", r"\*\*書き手\*\*[^\n]*1つだけ"),
    ),
    "INSTALL.md": (
        ("常時指示の安全行", r"\*\*安全\*\*[^\n]*記録しない"),
        ("常時指示の書き手行", r"\*\*書き手\*\*[^\n]*1つだけ"),
    ),
}

# 書いてあってはいけない表現（禁止の反転・緩和）
FORBIDDEN_STATEMENTS = (
    (
        "禁止情報を書く指示",
        re.compile(
            r"(APIキー|トークン|パスワード|個人情報|秘密情報|生ログ)"
            r"[^\n]{0,80}?"
            r"(を必ず書く|は必ず書く|を必ず記録|は必ず記録|は書いてよい|を書いてよい"
            r"|も書いてよい|は記録してよい|を記録してよい|は書いても(?:よい|構わない))"
        ),
    ),
)

# 値そのものは絶対に出力しない。種類と場所だけを報告する。
SECRET_PATTERNS = (
    ("Anthropic APIキー", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}")),
    ("OpenAI APIキー", re.compile(r"\bsk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}")),
    ("OpenAI APIキー", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("GitHubトークン", re.compile(r"\bghp_[A-Za-z0-9]{20,}|\bgh[ousr]_[A-Za-z0-9]{20,}")),
    ("GitHubトークン", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}")),
    ("AWSアクセスキー", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slackトークン", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("Google APIキー", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("秘密鍵", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "メールアドレス",
        re.compile(
            r"[A-Za-z0-9._%+-]+@(?!example\.(?:com|org|net))[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
        ),
    ),
    ("電話番号", re.compile(r"\b0\d{1,4}-\d{1,4}-\d{4}\b")),
)


def detect_mode(root: Path) -> str:
    """公開テンプレートか、導入済みの library かを中身から判定する。"""
    if (root / "INSTALL.md").is_file() and (root / "snippets").is_dir():
        return TEMPLATE
    return INSTALLED


def _scan_roots(root: Path, mode: str) -> tuple[Path, ...]:
    """Markdownを走査する範囲。導入済みモードでは利用者の他のファイルを見に行かない。"""
    return (root,) if mode == TEMPLATE else (root / "library",)


def _markdown_files(root: Path, mode: str):
    for base in _scan_roots(root, mode):
        if base.is_dir():
            yield from sorted(base.rglob("*.md"))


def _check_links(root: Path, mode: str, errors: list[str]) -> None:
    pattern = re.compile(r"!?\[[^]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
    for path in _markdown_files(root, mode):
        text = path.read_text(encoding="utf-8")
        for target in pattern.findall(text):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target_path = (path.parent / target.split("#", 1)[0]).resolve()
            if not target_path.is_file():
                errors.append(f"link: {path.relative_to(root)} -> {target}")


def _check_catalog(root: Path, errors: list[str]) -> None:
    catalog = root / "library" / "目録.md"
    if not catalog.exists():
        errors.append("棚: library/目録.md がありません")
        return
    text = catalog.read_text(encoding="utf-8")
    listed = set(re.findall(r"\]\(棚/([^/)]+\.md)\)", text))
    actual = {p.name for p in (root / "library" / "棚").glob("*.md")}
    if listed != actual:
        errors.append(f"棚: 目録={sorted(listed)} 実棚={sorted(actual)}")


def _check_threshold(root: Path, mode: str, errors: list[str]) -> None:
    values = set()
    # 「N件以上」「N件たまったら」だけを拾う（「0件なら」等の別文脈は拾わない）
    pattern = re.compile(r"([0-9]+)\s*件(?:以上|たま)")
    for path in _markdown_files(root, mode):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            values.add(int(match.group(1)))
    if values != {5}:
        errors.append(f"しきい値: 5件で統一されていません ({sorted(values)})")


def _check_secrets(root: Path, mode: str, errors: list[str]) -> None:
    for path in _markdown_files(root, mode):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for label, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    errors.append(
                        f"秘密情報: {path.relative_to(root)}:{number} に{label}らしき記述"
                    )
                    break


def _check_required_statements(root: Path, required: dict, errors: list[str]) -> None:
    """安全ルールが『意味』として残っているかを見る。"""
    for relative, statements in required.items():
        path = root / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for label, expression in statements:
            if not re.search(expression, text, re.M):
                errors.append(f"安全文: {relative} に「{label}」が見当たりません")


def _check_forbidden_statements(root: Path, mode: str, errors: list[str]) -> None:
    for path in _markdown_files(root, mode):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for label, pattern in FORBIDDEN_STATEMENTS:
                if pattern.search(line):
                    errors.append(
                        f"安全文: {path.relative_to(root)}:{number} に{label}があります"
                    )
                    break


def _check_safety_terms(root: Path, relatives, errors: list[str]) -> None:
    for relative in relatives:
        path = root / relative
        if not path.exists():
            continue
        missing = [t for t in SAFETY_TERMS if t not in path.read_text(encoding="utf-8")]
        if missing:
            errors.append(f"安全文: {relative} に不足 ({', '.join(missing)})")


def _extract_install_snippet(text: str) -> str:
    marker = "追記する内容"
    start = text.find(marker)
    if start < 0:
        return ""
    block_start = text.find("```markdown", start)
    if block_start < 0:
        return ""
    block_start = text.find("\n", block_start) + 1
    block_end = text.find("```", block_start)
    return text[block_start:block_end].strip()


def _check_snippet_matches_install(root: Path, errors: list[str]) -> None:
    install = root / "INSTALL.md"
    snippet = root / "snippets" / "claude-md-snippet.md"
    if not (install.exists() and snippet.exists()):
        return
    source = re.search(
        r"```markdown\n(.*?)\n```", snippet.read_text(encoding="utf-8"), re.S
    )
    installed = _extract_install_snippet(install.read_text(encoding="utf-8"))
    if not source or installed != source.group(1).strip():
        errors.append(
            "スニペット: INSTALL.md と snippets/claude-md-snippet.md が一致しません"
        )


def validate_library(root: Path | str, mode: str | None = None) -> list[str]:
    root = Path(root)
    mode = mode or detect_mode(root)
    if mode not in (TEMPLATE, INSTALLED):
        raise ValueError(f"未知のモード: {mode}")

    errors: list[str] = []
    required = LIBRARY_REQUIRED_FILES
    if mode == TEMPLATE:
        required = TEMPLATE_ONLY_REQUIRED_FILES + LIBRARY_REQUIRED_FILES
    for relative in required:
        if not (root / relative).exists():
            errors.append(f"必須ファイル: {relative}")

    _check_links(root, mode, errors)
    _check_catalog(root, errors)
    _check_threshold(root, mode, errors)
    _check_secrets(root, mode, errors)
    _check_required_statements(root, LIBRARY_REQUIRED_STATEMENTS, errors)
    _check_forbidden_statements(root, mode, errors)
    _check_safety_terms(root, LIBRARY_SAFETY_TERM_FILES, errors)

    if mode == TEMPLATE:
        _check_required_statements(root, TEMPLATE_REQUIRED_STATEMENTS, errors)
        _check_safety_terms(root, TEMPLATE_SAFETY_TERM_FILES, errors)
        _check_snippet_matches_install(root, errors)
        for path in _markdown_files(root, mode):
            if "2026-08-05" in path.read_text(encoding="utf-8"):
                errors.append(f"日付: 固定サンプル日付が残っています ({path.relative_to(root)})")
    return errors


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = None
    if "--mode" in argv:
        index = argv.index("--mode")
        try:
            mode = argv[index + 1]
        except IndexError:
            print("--mode には template か installed を指定してください")
            return 2
        del argv[index : index + 2]
    root = Path(argv[0]) if argv else Path(__file__).resolve().parents[1]
    if not root.is_dir():
        print(f"フォルダが見つかりません: {root}")
        return 2
    try:
        errors = validate_library(root, mode)
    except ValueError as error:
        print(error)
        return 2
    if errors:
        print("\n".join(errors))
        return 1
    print(f"大図書館の検証に合格しました（{mode or detect_mode(root)}）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
