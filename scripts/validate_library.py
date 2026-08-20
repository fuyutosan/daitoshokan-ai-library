"""Static checks for the public 大図書館 template (standard library only)."""

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
            if not target_path.exists():
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
    for path in _markdown_files(root):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"(?:未転記|✅の無い|✅` の無い)[^\n]{0,80}?([0-9]+)件", text):
            values.add(int(match.group(1)))
    if values != {5}:
        errors.append(f"しきい値: 5件で統一されていません ({sorted(values)})")


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

    install = root / "INSTALL.md"
    snippet = root / "snippets" / "claude-md-snippet.md"
    if install.exists() and snippet.exists():
        install_text = install.read_text(encoding="utf-8")
        snippet_text = snippet.read_text(encoding="utf-8")
        source = re.search(r"```markdown\n(.*?)\n```", snippet_text, re.S)
        if not source or _extract_install_snippet(install_text) != source.group(1).strip():
            errors.append("スニペット: INSTALL.md と snippets/claude-md-snippet.md が一致しません")

    for relative in ("INSTALL.md", "library/運用ルール.md", "snippets/claude-md-snippet.md"):
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


def main() -> int:
    errors = validate_library(Path(__file__).resolve().parents[1])
    if errors:
        print("\n".join(errors))
        return 1
    print("大図書館の検証に合格しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
