"""大図書館の静的検査（標準ライブラリのみ）。

## 何を保証し、何を保証しないか

この検査は **日本語の意味を判定しない**。安全ルールは「安全契約ブロック」として
原文で固定されており、可視本文の中に1文字ずつ一致する形で存在するかだけを見る。

- 契約ブロックを消す・並べ替える・言い換える → **不合格**（同義の言い換えも不合格）
- 契約ブロックをHTMLコメント・コードブロック・引用の中へ退避する → **不合格**
- 契約行の末尾に「ただし〜」を継ぎ足す → **不合格**（行全体で照合するため）

契約を書き換えたいときは、このファイルの `SAFETY_CONTRACT` と、
`tests/test_validate_library.py` の固定ハッシュを両方直す必要がある。片方だけでは通らない。

別行に矛盾する許可文を足す攻撃は `FORBIDDEN_STATEMENTS` で拾うが、
これは**列挙による網であって証明ではない**。網羅は主張しない。

## 2つのモード

- template  … 公開テンプレート全体を検査する
- installed … 利用者のプロジェクトに導入された `library/` **だけ**を検査する

モードは自動判定しない（判定が壊れると弱いほうへ倒れるため）。
Python APIの既定は `template`（最も厳しい側）。CLIはパス省略で `template`、
パス指定で `installed`、`--mode` があればそれが優先される。

    python scripts/validate_library.py --mode template
    python scripts/validate_library.py <プロジェクト> --mode installed
    python scripts/validate_library.py <プロジェクト> --instructions-file <プロジェクト>/CLAUDE.md
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path


TEMPLATE = "template"
INSTALLED = "installed"

LIBRARY_REQUIRED_FILES = (
    "library/目録.md",
    "library/運用ルール.md",
    "library/docs/安全とプライバシー.md",
    "library/docs/複数AIで使う場合.md",
)
TEMPLATE_ONLY_REQUIRED_FILES = (
    "README.md",
    "INSTALL.md",
    "LICENSE",
    "CHANGELOG.md",
    "snippets/claude-md-snippet.md",
    "commands/log.md",
    "commands/tidy.md",
)

# library/ 配下で Markdown 以外に置いてよいもの
LIBRARY_ALLOWED_NON_MARKDOWN = (".gitkeep", ".gitignore")

INSTALLED_CAVEAT = (
    "注記: library/ 本体のみ検査しました。常時読み込みファイル（CLAUDE.md / AGENTS.md 等）は"
    "未検査です。--instructions-file <パス> で指定すると検査します。"
)

# --- 安全契約ブロック（ここが原文の正。文書側をこれに合わせる） ---------------

SAFETY_CONTRACT = {
    "library/運用ルール.md": (
        ("秘密情報の記録禁止", (
            '### 安全とプライバシー',
            '- APIキー、トークン、パスワード、Cookie/セッション、`.env`や認証ファイルの中身、金融情報、住所・電話・メール、顧客の非公開情報は書かない',
            '- 値や全文を記録せず、「APIキーを環境変数へ移した」「認証を設定した」のように完了事実だけを書く',
            '- 誤って漏えいしたら、資格情報の無効化・再発行を最優先に行い、その後で履歴除去、Git除外設定、再発防止を行う。ファイル削除だけで完了扱いにしない',
            '- 生ログ（会話全文・ターミナル全文・エラー全文）は書かない',
            '- 詳しくは [安全とプライバシー](docs/安全とプライバシー.md)',
        )),
        ("書き手は1つだけ", (
            '### 書き手は1つだけ',
            '- この `library/` に書き込むAIは1つに決める。同じ日誌へ複数のAIが同時に追記すると、重複と誤った `✅` が起きる',
            '- 複数のAIを使うなら、未検品の記録は `library/` の**外**の staging に置き、検品済みだけを単一の経路で昇格させる',
            '- 詳しくは [複数AIで使う場合](docs/複数AIで使う場合.md)',
        )),
    ),
    "library/docs/安全とプライバシー.md": (
        ("GitHubへ公開しない", (
            '## GitHubへ公開しない',
            '導入先がpublic、またはvisibilityを確認できない場合は、個人用 `library/` をGit追跡しない運用を推奨します。ローカルの自分だけで除外するなら `.git/info/exclude`、チームで共有するなら `.gitignore` を選びます。AIが勝手に設定を変更せず、変更内容を示して利用者の明示確認を得てください。privateでも、共有範囲は毎回確認します。',
        )),
        ("記録禁止", (
            '## 記録禁止',
            'APIキー、トークン、パスワード、Cookie/セッション、`.env`や認証ファイルの中身、金融情報、住所・電話・メール、顧客の非公開情報、生ログ（会話全文・ターミナル全文・エラー全文）は記録しません。',
            '値を残す代わりに「APIキーを環境変数へ移した」「認証を設定した」とだけ書きます。必要な成果物は安全な保管場所へのリンクまたは一般化した説明にします。',
        )),
        ("漏えい時", (
            '## 漏えい時',
            '1. 資格情報を直ちに無効化し、再発行する',
            '2. 公開範囲を確認し、Git履歴から除去する',
            '3. `.gitignore`または`.git/info/exclude`へ追加する',
            '4. 何が起きたかと再発防止策を、秘密の値を含めず記録する',
            '単にファイルを削除するだけでは、Git履歴に残るため完了扱いにしません。',
        )),
    ),
    "library/docs/複数AIで使う場合.md": (
        ("書き手を1つにする前提", (
            '通常は、1つの `library/` に対して書き手を1エージェントにしてください。同じ日誌へClaudeとCodexが同時に追記すると、重複や誤った `✅` が起きます。初心者にstaging運用を強制する必要はありません。',
        )),
        ("昇格の境界", (
            '- 正本は1つだけにする',
            '- 未検品の記録は `library/` の外のstagingへ置く',
            '- `completed` かつ成果確認済みのものだけ、単一の昇格経路で正本へ移す',
            '- `partial`、`blocked`、`abandoned` は昇格しない',
            '- stagingを正本の内側に作らない',
            '昇格前に、秘密情報・個人情報・顧客の非公開情報・生ログがないこと、目録と棚が一致することを確認します。',
        )),
    ),
    "commands/log.md": (
        ("記録時の禁止", (
            '- APIキー・トークン・パスワード・個人情報・生ログは書かない。値を表示せず「移した／設定した」とだけ記録する',
        )),
    ),
    "commands/tidy.md": (
        ("整理時の禁止", (
            '- APIキー・トークン・パスワード・個人情報・生ログなどの禁止情報を見つけたエントリは棚へ転記しない。値を表示せず利用者へ報告し、資格情報なら無効化・再発行を優先する',
        )),
    ),
    "README.md": (
        ("安全に使う", (
            '## 🔒 安全に使う',
            '公開テンプレと、導入後に作られる個人用 `library/` は別物です。個人用 `library/` には、APIキー、トークン、パスワード、Cookie/セッション、`.env`や認証ファイルの中身、金融情報、住所・電話・メール、顧客の非公開情報、生ログを書かないでください。詳しい確認手順は [安全とプライバシー](library/docs/安全とプライバシー.md) と [複数AIで使う場合](library/docs/複数AIで使う場合.md) を参照してください。',
        )),
    ),
}

# 常時読み込みファイルへ貼る2行（スニペット側の契約）
SNIPPET_CONTRACT = (
    '**安全** — APIキー、トークン、パスワード、Cookie/セッション、`.env`/認証ファイルの中身、金融情報、住所・電話・メール、顧客の非公開情報、生ログは記録しない。値を書かず「移した」「設定した」とだけ記録する。',
    '**書き手** — この `library/` に書くAIは1つだけ。別のAIも使うなら、`library/` の外に staging を作ってそこへ書き、検品したものだけを1つの経路で `library/` へ移す。同じ日誌に2つのAIが同時に追記しない。',
)

# 契約の隣に矛盾する許可文を足す攻撃への網。網羅は主張しない。
FORBIDDEN_STATEMENTS = (
    (
        "禁止情報の記録を許す記述",
        re.compile(
            r"(APIキー|トークン|パスワード|個人情報|秘密情報|生ログ|認証情報|資格情報)"
            r"[^\n]{0,80}?"
            r"(を必ず書く|は必ず書く|を必ず記録|は必ず記録"
            r"|は書いてよい|を書いてよい|も書いてよい|は書いても(?:よい|構わない)"
            r"|は記録してよい|を記録してよい|は保存してよい|を保存してよい"
            r"|は残してよい|を残してよい"
            r"|の記録を許可|の記載を許可|の保存を許可|の記録は許可"
            r"|を許可する|は許可する|を例外とする|は例外とする)"
        ),
    ),
)

# 値そのものは絶対に出力しない。種類と場所だけを報告する。
SECRET_PATTERNS = (
    ("Anthropic APIキー", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}")),
    ("OpenAI APIキー", re.compile(r"\bsk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}")),
    ("OpenAI APIキー", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("GitHubトークン", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
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
    # 電話番号: 区切りあり / ハイフンなし10〜11桁 / 括弧 / 国番号つき
    ("電話番号", re.compile(r"\b0\d{1,3}[-\s]\d{1,4}[-\s]\d{3,4}\b")),
    ("電話番号", re.compile(r"(?<!\d)0\d{9,10}(?!\d)")),
    ("電話番号", re.compile(r"(?<!\d)0\d{1,3}\(\d{1,4}\)\d{3,4}(?!\d)")),
    (
        "電話番号",
        re.compile(r"\+\d{1,3}[-\s]*(?:\(0\)[-\s]*)?(?:\d[-\s]?){8,13}\d"),
    ),
)


RAW_HTML_TAGS = ("pre", "code", "script", "style", "textarea", "template")
RAW_HTML_OPEN = re.compile(
    rf"<\s*({'|'.join(RAW_HTML_TAGS)})\b[^>]*>", re.IGNORECASE
)


def _fence_marker(line: str) -> tuple[str, int, str] | None:
    """0〜3文字字下げされたMarkdownフェンスの文字・長さ・後続を返す。"""
    if line.startswith("\t"):
        return None
    indent = len(line) - len(line.lstrip(" "))
    if indent > 3:
        return None
    body = line[indent:]
    if not body or body[0] not in ("`", "~"):
        return None
    char = body[0]
    size = len(body) - len(body.lstrip(char))
    if size < 3:
        return None
    return char, size, body[size:]


def _strip_hidden_html(
    line: str, in_comment: bool, raw_tag: str | None
) -> tuple[str, bool, str | None]:
    """HTMLコメントと非本文要素を除き、未閉鎖状態を次行へ渡す。"""
    visible: list[str] = []
    position = 0
    while position < len(line):
        if in_comment:
            end = line.find("-->", position)
            if end < 0:
                return "".join(visible), True, raw_tag
            in_comment = False
            position = end + 3
            continue

        if raw_tag is not None:
            close = re.search(rf"</\s*{re.escape(raw_tag)}\s*>", line[position:], re.I)
            if close is None:
                return "".join(visible), in_comment, raw_tag
            position += close.end()
            raw_tag = None
            continue

        comment_at = line.find("<!--", position)
        tag_match = RAW_HTML_OPEN.search(line, position)
        tag_at = tag_match.start() if tag_match else -1
        candidates = [value for value in (comment_at, tag_at) if value >= 0]
        if not candidates:
            visible.append(line[position:])
            break

        hidden_at = min(candidates)
        visible.append(line[position:hidden_at])
        if comment_at == hidden_at:
            in_comment = True
            position = hidden_at + 4
        else:
            raw_tag = tag_match.group(1).lower()
            position = tag_match.end()

    return "".join(visible), in_comment, raw_tag


def _visible_lines(text: str) -> list[str]:
    """契約として数えてよい可視本文だけを返す。

    HTMLコメント・コードブロック・引用・raw HTMLの非本文要素は数えない。
    通常本文として許される0〜3空白の字下げだけを除き、契約行を完全一致で数える。
    """
    lines: list[str] = []
    fence_char: str | None = None
    fence_size = 0
    in_comment = False
    raw_tag: str | None = None
    for raw in text.splitlines():
        if fence_char is not None:
            marker = _fence_marker(raw)
            if (
                marker is not None
                and marker[0] == fence_char
                and marker[1] >= fence_size
                and not marker[2].strip()
            ):
                fence_char = None
                fence_size = 0
            continue

        line, in_comment, raw_tag = _strip_hidden_html(raw, in_comment, raw_tag)
        marker = _fence_marker(line)
        if marker is not None:
            fence_char, fence_size, _ = marker
            continue

        if not line or line.startswith("\t"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent >= 4:
            continue
        line = line[indent:]
        if not line or line.startswith(">"):
            continue
        lines.append(line)
    return lines


def _fence_lines(text: str) -> list[str]:
    """可視本文にあるmarkdownフェンスの中身（スニペットの貼り付け本文）。"""
    in_comment = False
    raw_tag: str | None = None
    fence_char: str | None = None
    fence_size = 0
    captured: list[str] | None = None

    for raw in text.splitlines():
        if fence_char is not None:
            marker = _fence_marker(raw)
            if (
                marker is not None
                and marker[0] == fence_char
                and marker[1] >= fence_size
                and not marker[2].strip()
            ):
                if captured is not None:
                    return [line.strip() for line in captured if line.strip()]
                fence_char = None
                fence_size = 0
                continue
            if captured is not None:
                captured.append(raw)
            continue

        line, in_comment, raw_tag = _strip_hidden_html(raw, in_comment, raw_tag)
        marker = _fence_marker(line)
        if marker is None:
            continue
        fence_char, fence_size, info = marker
        captured = [] if info.strip().lower() == "markdown" else None
    return []


def _contains_block(lines: list[str], block: tuple[str, ...]) -> bool:
    size = len(block)
    return any(lines[i : i + size] == list(block) for i in range(len(lines) - size + 1))


def _markdown_files(root: Path, mode: str):
    base = root if mode == TEMPLATE else root / "library"
    if base.is_dir():
        yield from sorted(base.rglob("*.md"))


def _library_files(root: Path):
    base = root / "library"
    if base.is_dir():
        yield from sorted(p for p in base.rglob("*") if p.is_file())


def _check_links(root: Path, mode: str, errors: list[str]) -> None:
    pattern = re.compile(r"!?\[[^]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
    for path in _markdown_files(root, mode):
        for target in pattern.findall(path.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if not (path.parent / target.split("#", 1)[0]).resolve().is_file():
                errors.append(f"link: {path.relative_to(root)} -> {target}")


def _check_catalog(root: Path, errors: list[str]) -> None:
    catalog = root / "library" / "目録.md"
    if not catalog.exists():
        errors.append("棚: library/目録.md がありません")
        return
    listed = set(re.findall(r"\]\(棚/([^/)]+\.md)\)", catalog.read_text(encoding="utf-8")))
    actual = {p.name for p in (root / "library" / "棚").glob("*.md")}
    if listed != actual:
        errors.append(f"棚: 目録={sorted(listed)} 実棚={sorted(actual)}")


def _check_threshold(root: Path, mode: str, errors: list[str]) -> None:
    values = set()
    pattern = re.compile(r"([0-9]+)\s*件(?:以上|たま)")
    for path in _markdown_files(root, mode):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            values.add(int(match.group(1)))
    if values != {5}:
        errors.append(f"しきい値: 5件で統一されていません ({sorted(values)})")


def _check_library_layout(root: Path, errors: list[str]) -> None:
    """大図書館はMarkdown専用。他形式は秘密情報の抜け道になるので置かせない。"""
    for path in _library_files(root):
        if path.suffix.lower() == ".md" or path.name in LIBRARY_ALLOWED_NON_MARKDOWN:
            continue
        errors.append(f"構成: library/ はMarkdown専用です ({path.relative_to(root)})")


def _scan_targets(root: Path, mode: str):
    """秘密情報を走査する対象。Markdown以外でも、library/配下のテキストは全部見る。"""
    seen = set()
    for path in _markdown_files(root, mode):
        seen.add(path)
        yield path
    for path in _library_files(root):
        if path in seen:
            continue
        yield path


def _check_utf8(root: Path, mode: str, errors: list[str]) -> bool:
    """走査対象はUTF-8テキスト専用。読めない対象を合格扱いにしない。"""
    valid = True
    for path in _scan_targets(root, mode):
        try:
            path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            errors.append(
                f"文字コード: {path.relative_to(root)} をUTF-8として読めません"
            )
            valid = False
    return valid


def _check_secrets(root: Path, mode: str, errors: list[str]) -> None:
    for path in _scan_targets(root, mode):
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            normalized = unicodedata.normalize("NFKC", line)
            for label, pattern in SECRET_PATTERNS:
                if pattern.search(normalized):
                    errors.append(
                        f"秘密情報: {path.relative_to(root)}:{number} に{label}らしき記述"
                    )
                    break


def _check_contract(root: Path, mode: str, errors: list[str]) -> None:
    for relative, blocks in SAFETY_CONTRACT.items():
        if mode == INSTALLED and not relative.startswith("library/"):
            continue
        path = root / relative
        if not path.exists():
            continue
        lines = _visible_lines(path.read_text(encoding="utf-8"))
        for label, block in blocks:
            if not _contains_block(lines, block):
                errors.append(
                    f"安全契約: {relative} の「{label}」が原文どおりに見つかりません"
                )


def _check_snippet_contract(root: Path, errors: list[str]) -> None:
    for relative in ("snippets/claude-md-snippet.md", "INSTALL.md"):
        path = root / relative
        if not path.exists():
            continue
        lines = _fence_lines(path.read_text(encoding="utf-8"))
        for contract_line in SNIPPET_CONTRACT:
            if contract_line not in lines:
                errors.append(
                    f"安全契約: {relative} の貼り付けブロックに"
                    f"「{contract_line[:12]}…」が原文どおりに見つかりません"
                )


def _check_forbidden_statements(root: Path, mode: str, errors: list[str]) -> None:
    for path in _markdown_files(root, mode):
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            for label, pattern in FORBIDDEN_STATEMENTS:
                if pattern.search(line):
                    errors.append(
                        f"安全契約: {path.relative_to(root)}:{number} に{label}があります"
                    )
                    break


def _check_snippet_matches_install(root: Path, errors: list[str]) -> None:
    install = root / "INSTALL.md"
    snippet = root / "snippets" / "claude-md-snippet.md"
    if not (install.exists() and snippet.exists()):
        return
    source = re.search(
        r"```markdown\r?\n(.*?)\r?\n```", snippet.read_text(encoding="utf-8"), re.S
    )
    text = install.read_text(encoding="utf-8")
    start = text.find("追記する内容")
    block = re.search(r"```markdown\r?\n(.*?)\r?\n```", text[start:], re.S) if start >= 0 else None
    if not source or not block or block.group(1).strip() != source.group(1).strip():
        errors.append("スニペット: INSTALL.md と snippets/claude-md-snippet.md が一致しません")


def validate_instructions_file(path: Path | str) -> list[str]:
    """常時読み込みファイル（CLAUDE.md / AGENTS.md 等）に契約2行があるか。"""
    path = Path(path)
    if not path.is_file():
        return [f"常時ファイル: {path} が見つかりません"]
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return [f"常時ファイル: {path.name} をUTF-8として読めません"]
    lines = _visible_lines(text)
    return [
        f"常時ファイル: {path.name} に「{line[:12]}…」が原文どおりに見つかりません"
        for line in SNIPPET_CONTRACT
        if line not in lines
    ]


def validate_library(
    root: Path | str,
    mode: str = TEMPLATE,
    instructions_file: Path | str | None = None,
) -> list[str]:
    root = Path(root)
    if mode not in (TEMPLATE, INSTALLED):
        raise ValueError(f"未知のモード: {mode}")

    errors: list[str] = []
    required = LIBRARY_REQUIRED_FILES
    if mode == TEMPLATE:
        required = TEMPLATE_ONLY_REQUIRED_FILES + LIBRARY_REQUIRED_FILES
    for relative in required:
        if not (root / relative).exists():
            errors.append(f"必須ファイル: {relative}")

    _check_library_layout(root, errors)
    if not _check_utf8(root, mode, errors):
        return errors

    _check_links(root, mode, errors)
    _check_catalog(root, errors)
    _check_threshold(root, mode, errors)
    _check_secrets(root, mode, errors)
    _check_contract(root, mode, errors)
    _check_forbidden_statements(root, mode, errors)

    if mode == TEMPLATE:
        _check_snippet_contract(root, errors)
        _check_snippet_matches_install(root, errors)
        for path in _markdown_files(root, mode):
            if "2026-08-05" in path.read_text(encoding="utf-8"):
                errors.append(
                    f"日付: 固定サンプル日付が残っています ({path.relative_to(root)})"
                )
    if instructions_file is not None:
        errors.extend(validate_instructions_file(instructions_file))
    return errors


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = None
    instructions = None
    for flag, setter in (("--mode", "mode"), ("--instructions-file", "instructions")):
        if flag in argv:
            index = argv.index(flag)
            if index + 1 >= len(argv):
                print(f"{flag} には値を指定してください")
                return 2
            value = argv[index + 1]
            del argv[index : index + 2]
            if setter == "mode":
                mode = value
            else:
                instructions = value
    # モードは推測しない。パス省略=template、パス指定=installed、--mode があれば優先。
    if mode is None:
        mode = TEMPLATE if not argv else INSTALLED
    root = Path(argv[0]) if argv else Path(__file__).resolve().parents[1]
    if not root.is_dir():
        print(f"フォルダが見つかりません: {root}")
        return 2
    try:
        errors = validate_library(root, mode, instructions)
    except ValueError as error:
        print(error)
        return 2
    if errors:
        print("\n".join(errors))
        return 1
    print(f"大図書館の検証に合格しました（{mode}）。")
    if mode == INSTALLED and instructions is None:
        print(INSTALLED_CAVEAT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
