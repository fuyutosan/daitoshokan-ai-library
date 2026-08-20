"""Static checks for the public 大図書館 template (standard library only).

利用者の個人用 library/ にも使えます:
    python scripts/validate_library.py <libraryのある親フォルダ>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


REQUIRED_FILES = (
    "README.md",
    "INSTALL.md",
    "LICENSE",
    "CHANGELOG.md",
    "docs/安全とプライバシー.md",
    "docs/複数AIで使う場合.md",
    "snippets/claude-md-snippet.md",
    "library/運用ルール.md",
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
SAFETY_FILES = (
    "INSTALL.md",
    "library/運用ルール.md",
    "snippets/claude-md-snippet.md",
)
# 値そのものは絶対に出力しない。種類と場所だけを報告する。
SECRET_PATTERNS = (
    ("Anthropic APIキー", re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")),
    ("OpenAI APIキー", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("GitHubトークン", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("AWSアクセスキー", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slackトークン", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("Google APIキー", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("秘密鍵", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("メールアドレス", re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.(?:com|org|net))[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("電話番号", re.compile(r"\b0\d{1,4}-\d{1,4}-\d{4}\b")),
)


def _markdown_files(root: Path):
    return root.rglob("*.md")


def _check_links(root: Path, errors: list[str]) -> None:
    pattern = re.compile(r"!?\[[^]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
    for path in _markdown_files(root):
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


def _check_threshold(root: Path, errors: list[str]) -> None:
    values = set()
    # 「N件以上」「N件たまったら」だけを拾う（「0件なら」等の別文脈は拾わない）
    pattern = re.compile(r"([0-9]+)\s*件(?:以上|たま)")
    for path in _markdown_files(root):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            values.add(int(match.group(1)))
    if values != {5}:
        errors.append(f"しきい値: 5件で統一されていません ({sorted(values)})")


def _check_secrets(root: Path, errors: list[str]) -> None:
    for path in sorted(_markdown_files(root)):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for label, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    errors.append(f"秘密情報: {path.relative_to(root)}:{number} に{label}らしき記述")
                    break


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


def validate_library(root: Path | str) -> list[str]:
    root = Path(root)
    errors: list[str] = []
    for relative in REQUIRED_FILES:
        if not (root / relative).exists():
            errors.append(f"必須ファイル: {relative}")
    _check_links(root, errors)
    _check_catalog(root, errors)
    _check_threshold(root, errors)
    _check_secrets(root, errors)

    install = root / "INSTALL.md"
    snippet = root / "snippets" / "claude-md-snippet.md"
    if install.exists() and snippet.exists():
        install_text = install.read_text(encoding="utf-8")
        snippet_text = snippet.read_text(encoding="utf-8")
        source = re.search(r"```markdown\n(.*?)\n```", snippet_text, re.S)
        if not source or _extract_install_snippet(install_text) != source.group(1).strip():
            errors.append("スニペット: INSTALL.md と snippets/claude-md-snippet.md が一致しません")

    for relative in SAFETY_FILES:
        path = root / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        missing = [term for term in SAFETY_TERMS if term not in text]
        if missing:
            errors.append(f"安全文: {relative} に不足 ({', '.join(missing)})")
    for path in _markdown_files(root):
        if "2026-08-05" in path.read_text(encoding="utf-8"):
            errors.append(f"日付: 固定サンプル日付が残っています ({path.relative_to(root)})")
    return errors


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(argv[0]) if argv else Path(__file__).resolve().parents[1]
    errors = validate_library(root)
    if errors:
        print("\n".join(errors))
        return 1
    print("大図書館の検証に合格しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
