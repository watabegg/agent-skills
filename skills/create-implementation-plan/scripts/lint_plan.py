#!/usr/bin/env python3
"""実装計画書の提出前セルフリント。

usage:
  lint_plan.py [--require-detailed] <plan.md> [todo.md goal-prompt.md ...]
  lint_plan.py --finalize <plan.md>

exit 0 = PASS / exit 1 = FAIL / exit 2 = 引数エラー

- 計画書: 冒頭の `<!-- plan-mode: full|lite|scaled-overview|scaled-phase -->` でモード判定。
- `*-todo.md` は TODO 台帳、`*-goal-prompt.md` は goal prompt として専用検査する。
- --finalize: 計画書ヘッダの参照から todo / goal-prompt / scaled フェーズを役割付きで解決し、
  全ファイル lint → モード適格性 → バンドル相互参照 → HTML ビュー生成までを1コマンドで行う（納品用）。
- テンプレート未記入のプレースホルダ `[...]` は失格。
  実データとしての角括弧はインラインコード `...` で囲めば除外される。
"""
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

sys.dont_write_bytecode = True  # スキルディレクトリに __pycache__ を作らない
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mdtable import split_row  # noqa: E402
from build_plan_html import compute_source_hash  # noqa: E402  stale 判定のハッシュ契約を builder と共有

MODE_RE = re.compile(r"<!--\s*plan-mode:\s*(full|lite|scaled-overview|scaled-phase)\s*-->")
SCHEMA_RE = re.compile(r"<!--\s*plan-schema:\s*(causal-v1)\s*-->")
ANY_SCHEMA_RE = re.compile(r"<!--\s*plan-schema:\s*([^\s]+)\s*-->")
INPUT_TYPE_RE = re.compile(
    r"^-\s*入力タイプ\s*[:：]\s*(corrective-single|corrective-bundle\(\d+\)|feature-gap)\s*$",
    re.M,
)
OUTLINE_RE = re.compile(r"^\s*-\s*状態\s*[:：]\s*outline\s*$")
STATE_RE = re.compile(r"^\s*-\s*状態\s*[:：]\s*(detailed|outline)\s*$")
HUB_RE = re.compile(r"^\s*-\s*設計ハブ\s*[:：]\s*(true|false)\s*$", re.I)

KNOWN_FLAGS = {"--require-detailed", "--finalize"}

REQUIRED_HEADINGS = {
    "full": [
        "目的", "事前調査結果", "既存実装の理解", "メンタルモデル", "設計判断",
        "不変条件", "Hard Gate", "変更対象ファイル", "実装ステップ", "テスト計画",
        "リスクと対策", "Unclear Points", "Resolved Points", "TodoList",
        ("図", "Sequence Diagram"), "Definition of Done",
    ],
    "lite": [
        "目的", "変更対象ファイル", "不変条件", "Unclear Points",
        "TodoList", "Definition of Done",
    ],
    "scaled-overview": [
        "目的", "不変条件", "フェーズ一覧", "Unclear Points",
        "Resolved Points", "TodoList", "Definition of Done",
    ],
    "scaled-phase": [
        "目的", "設計判断", "変更対象ファイル", "実装ステップ", "テスト計画",
        "リスクと対策", "Todo",
    ],
    "scaled-phase-outline": [
        "目的", "変更対象ファイル",
    ],
}

CAUSAL_REQUIRED_HEADINGS = {
    "full": [
        "目的", "事前調査結果", "既存実装の理解", "因果分析", "解決の概念モデル",
        "修復モデル", "論理的変更単位", "同型探索", "解決アプローチ比較", "設計判断",
        "不変条件", "Hard Gate", "変更対象ファイル", "実装ステップ", "テスト計画",
        "リスクと対策", "Unclear Points", "Resolved Points", "TodoList",
        ("図", "Sequence Diagram"), "Definition of Done",
    ],
    "lite": [
        "目的", "問題と解決モデル", "変更対象ファイル", "不変条件", "Unclear Points",
        "TodoList", "Definition of Done",
    ],
    "scaled-overview": [
        "目的", "不変条件", "フェーズ一覧", "Unclear Points",
        "Resolved Points", "TodoList", "Definition of Done",
    ],
    "scaled-phase": [
        "目的", "設計判断", "変更対象ファイル", "実装ステップ", "テスト計画",
        "リスクと対策", "Todo",
    ],
    "scaled-phase-outline": ["目的", "変更対象ファイル"],
}

TODO_REQUIRED = ["運用契約", "正本計画書", "ステータス凡例", "Hard Gate", "完了条件",
                 "計画検証日時", "アクティブタスク", "Unclear Points", "非冪等"]

EXPECT_BANNED = ["確認できる", "意図した", "正しく動作", "適切に", "問題ない", "期待通り", "うまく"]
# Expect が機械判定形に見えるかの緩い認識（外れたら warning。error にはしない）
EXPECT_MACHINE = re.compile(r"(\d|==|~=|exit|件|PASS|FAIL|http|存在|空でない|stdout|一致|以上|以下|未満|なし)")
# 既知の単純 no-op を拒否する（bash -c true 等のラッパーまでは静的検出しない契約）。
# echo / printf はパイプ後段で検証しているケース（echo .. | jq -e）を誤検知しないよう単体のみ。
NOOP_COMMANDS = re.compile(r"^(true|:|exit\s+0|echo\b[^|]*|printf\b[^|]*)$")

SELF_REF_DOD = re.compile(r"(計画書|文書|ドキュメント)(に|が)[^。\n]{0,25}(書かれている|記載されている|含まれている|完成している)")
DESIGN_TWICE_BANNED = re.compile(r"^(特になし|なし|N/?A|-|ー)$", re.I)
ALT_COL_KEYS = ("非採用", "不採用", "代替案", "却下")
RISK_COL_KEYS = ("対応", "Step", "Todo")

PROSCONS_CELLS = {"メリット", "デメリット", "長所", "短所", "利点", "欠点", "良い点", "悪い点", "pros", "cons", "pro", "con"}
PROSCONS_PAIRS = [("メリット", "デメリット"), ("長所", "短所"), ("利点", "欠点"), ("良い点", "悪い点")]
PROSCONS_EN = re.compile(r"\b(pros)\b.*\b(cons)\b|\b(cons)\b.*\b(pros)\b", re.I)
PROSCONS_WORD = re.compile(r"\b(pros|cons)\b", re.I)

PLACEHOLDER_CODE_KEYWORDS = re.compile(r"^(path|command|cmd|expect|topic|name|N)$")
CHECKBOX_TOKENS = {"[ ]", "[x]", "[!]", "[-]"}

LITE_FORBIDDEN = re.compile(
    r"(?<![A-Za-z])(migration|migrate|schema|auth|permission|billing|payment|drop|truncate)(?![A-Za-z])"
    r"|マイグレーション|スキーマ|認証|認可|権限|決済|課金|データ移行|データ削除|公開\s*API", re.I)
ZERO_DISTORTION = re.compile(r"^(なし|ゼロ|特になし|特にない|該当なし|N/?A|-|ー)[。.]?$", re.I)

LONG_LINE_LIMIT = 200
LONG_LINE_FAIL = 6
OVERVIEW_WARN_LINES = 80
OVERVIEW_FAIL_LINES = 120
PLACEHOLDER_LIST_MAX = 5
SCALED_STEP_MIN = 8
SCALED_TODO_MIN = 26
SCALED_FILES_MIN = 31


# ---------- 共通パーサ ----------

def strip_code_blocks(lines):
    """コードフェンス外かつ HTML コメント外の可視行を (行番号, 行) で返す。

    見出し・表・Step・Gate・チェックボックスの全検査はこの可視テキストに対して行う
    （コメントに本文を包んで検査を満たす偽装を防ぐ）。フェンス内の <!-- はコードとして
    扱い、コメント内の ``` はフェンスとして扱わない。
    """
    out, in_fence, in_comment = [], False, False
    for i, line in enumerate(lines, 1):
        if not in_comment and line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        res, j = [], 0
        while j is not None and j <= len(line):
            if in_comment:
                k = line.find("-->", j)
                if k < 0:
                    j = None
                else:
                    j = k + 3
                    in_comment = False
            else:
                k = line.find("<!--", j)
                if k < 0:
                    res.append(line[j:])
                    j = None
                else:
                    res.append(line[j:k])
                    j = k + 4
                    in_comment = True
        out.append((i, "".join(res)))
    return out, in_fence


def section_body(prose_lines, keyword):
    body, level, capture = [], None, False
    for line in prose_lines:
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            if capture and len(m.group(1)) <= level:
                break
            if keyword in m.group(2):
                capture, level = True, len(m.group(1))
                continue
        if capture:
            body.append(line)
    return body


def parse_tables(lines):
    tables, header, rows, in_table = [], None, [], False
    for line in lines + [""]:
        s = line.strip()
        if not s.startswith("|"):
            if header is not None:
                tables.append((header, rows))
            header, rows, in_table = None, [], False
            continue
        if re.match(r"^\|[\s:|-]+\|$", s):
            continue
        cells = split_row(s)
        if not in_table:
            in_table, header = True, cells
            continue
        rows.append(cells)
    return tables


def repo_root(start: Path):
    for d in [start] + list(start.parents):
        if (d / ".git").exists():
            return d
    return None


def find_placeholders(prose, strict_code=False):
    """テンプレート未記入プレースホルダの検出。

    strict_code=True（goal-prompt 用）はインラインコード内の [xxx] を無条件で対象にする。
    """
    hits = []
    for n, line in prose:
        for c in re.findall(r"`([^`]*)`", line):
            c = c.strip()
            m = re.fullmatch(r"\[([^\][]{1,200})\]", c)
            if m and c not in CHECKBOX_TOKENS:
                inner = m.group(1)
                if strict_code or (re.search(r"[぀-ヿ一-鿿]", inner) or "<" in inner
                                   or PLACEHOLDER_CODE_KEYWORDS.match(inner)):
                    hits.append((n, inner))
        s = re.sub(r"`[^`]*`", "", line)
        s = re.sub(r"- \[[ x!\-]\]", "", s)
        for m in re.finditer(r"\[([^\][]{1,200})\]", s):
            if s[m.end():m.end() + 1] == "(":
                continue
            hits.append((n, m.group(1)))
    return hits


def report_placeholders(prose, errors, strict_code=False):
    ph = find_placeholders(prose, strict_code=strict_code)
    if ph:
        sample = "、".join(f"L{n}[{t[:30]}]" for n, t in ph[:PLACEHOLDER_LIST_MAX])
        more = f" 他{len(ph) - PLACEHOLDER_LIST_MAX}件" if len(ph) > PLACEHOLDER_LIST_MAX else ""
        errors.append(f"テンプレート未記入のプレースホルダが残っている: {sample}{more}"
                      "（実データの角括弧はインラインコード `...` で囲む）")


def _strip_literals(text):
    text = re.sub(r"`[^`]*`", "", text)
    text = re.sub(r"「[^」]*」", "", text)
    return text


def gate_targets(prose_lines):
    """Gate 系の (command部, expect部, 文脈) を全形式から収集する。

    形式: `- Command: `cmd`` 行 / `Expect:` 行 / （Gate: ...）括弧 / Command・Gate 列を持つ表。
    expect の strict=False（Gate 括弧の短縮形）は機械判定形の要求から除外する。
    """
    cmds, expects = [], []
    for line in prose_lines:
        m = re.search(r"Command\s*[:：]\s*(.+)$", line)
        if m:
            cmds.append((m.group(1).strip(), line.strip()[:80]))
        m = re.search(r"(Expect|期待値)\s*[:：](.*)$", line)
        if m:
            expects.append((m.group(2).strip(), line.strip()[:80], True))
        for g in re.finditer(r"[（(]Gate\s*[:：]([^）)]*)[）)]", line):
            cmds.append((g.group(1).strip(), line.strip()[:80]))
            expects.append((g.group(1).strip(), line.strip()[:80], False))
    for header, rows in parse_tables(prose_lines):
        cidx = [i for i, c in enumerate(header) if ("Command" in c) or ("Gate" in c)]
        eidx = [i for i, c in enumerate(header) if ("Expect" in c) or ("期待値" in c) or ("Gate" in c)]
        for row in rows:
            for i in cidx:
                if i < len(row) and row[i]:
                    cmds.append((row[i], row[i][:80]))
            for i in eidx:
                if i < len(row) and row[i]:
                    expects.append((row[i], row[i][:80], True))
    return cmds, expects


def scan_gates(prose_lines):
    """Gate の品質検査: 判定不能語 / 機械判定形 / no-op / バッククォート必須。

    機械判定形の検査は引用リテラル除去後のテキストに対して行う
    （「主観文（1回）」のように引用+数字で包む迂回を防ぐ。stdout 一致等の
    正当な引用は「一致」等の判定語が引用外に残るため通る）。
    """
    errors, warnings = [], []
    cmds, expects = gate_targets(prose_lines)
    for text, ctx, strict in expects:
        cleaned = _strip_literals(text)
        for w in EXPECT_BANNED:
            if w in cleaned:
                errors.append(f"Expect に判定不能語「{w}」: {ctx}")
        if strict and text and not EXPECT_MACHINE.search(cleaned):
            errors.append(f"Expect が機械判定形でない（exit code・件数・一致条件等の判定可能な値で書く）: {ctx}")
    for text, ctx in cmds:
        codes = re.findall(r"`([^`]+)`", text)
        if not codes:
            errors.append(f"Gate の Command がバッククォート付きでない: {ctx}")
        for c in codes:
            if NOOP_COMMANDS.fullmatch(c.strip()):
                errors.append(f"Gate が no-op コマンド（{c.strip()[:20]}）: 成果物を検証するコマンドにする")
    return errors, warnings


def scan_proscons(prose_lines):
    errors, warnings = [], []
    for line in prose_lines:
        if re.match(r"^#{1,4}\s", line):
            has_pair = PROSCONS_EN.search(line) or any(a in line and b in line for a, b in PROSCONS_PAIRS)
            if has_pair:
                errors.append(f"Pros/Cons 見出しは禁止（軸を抽出した星取り表で比較する）: {line.strip()[:60]}")
            elif PROSCONS_WORD.search(line) or any(w in line for w in ("メリット", "デメリット")):
                warnings.append(f"Pros/Cons 的な見出し（星取り表を検討）: {line.strip()[:60]}")
    for header, _rows in parse_tables(prose_lines):
        bad = [c for c in header if c.strip().lower() in PROSCONS_CELLS]
        if bad:
            errors.append("Pros/Cons 型の表は禁止（軸を抽出した星取り表で比較する）: | " + " | ".join(header)[:70])
    return errors, warnings


def model_name(header_cell):
    """「モデルN: 名前 — 一文定義」からモデル名を取り出す。

    区切りは em dash（—/－）のみ。ASCII ハイフンは名前の一部（API-first 等）として保持する。
    """
    s = re.sub(r"^モデル\d+\s*[:：]\s*", "", header_cell.strip())
    return unicodedata.normalize("NFKC", re.split(r"\s*[—－]\s*", s)[0].strip())


def validate_mental_model(mm_tables):
    """メンタルモデル星取り表の実体検査。戻り値: (errors, 採用モデル名 or None)。"""
    errors = []
    tables = [(h, r) for h, r in mm_tables if r]
    if not tables:
        return ["変更のメンタルモデルの星取り表がない"], None
    if len(tables) > 1:
        errors.append("メンタルモデルセクションに表が複数ある（星取り表はちょうど1つにする）")
    header, rows = tables[0]
    ncand = len(header) - 1
    if ncand < 2:
        errors.append("メンタルモデルの候補が2つ未満（軸 × モデル2〜4個の星取り表にして戦わせる）")
    if ncand > 4:
        errors.append(f"メンタルモデルの候補が{ncand}個（2〜4個に絞る）")
    for r in rows:
        if len(r) != ncand + 1:
            errors.append(f"メンタルモデル表の行のセル数がヘッダと不一致: | {' | '.join(r)[:60]}")
    names = [model_name(c) for c in header[1:]]
    if len(set(names)) != len(names) or any(not n for n in names):
        errors.append("メンタルモデル名が空または重複している（モデルN: を除いた名前で判定）")

    def row(kw):
        matches = [r for r in rows if r and kw in r[0]]
        if len(matches) > 1:
            errors.append(f"「{kw}」を含む行が複数ある（矛盾の温床。1行にまとめる）")
        return matches[0] if matches else None

    adopted_idx, adopted_name = -1, None
    jrow = row("判定")
    if not jrow:
        errors.append("メンタルモデル表に「判定」行がない")
    else:
        cells = [c.strip() for c in jrow[1:1 + ncand]]
        adopted = [i for i, c in enumerate(cells) if c == "採用"]
        if len(adopted) != 1:
            errors.append(f"判定行の「採用」はちょうど1列にする（現在{len(adopted)}列。表記は「採用」と完全一致。採用（仮）等は不可）")
        else:
            adopted_idx = adopted[0]
            adopted_name = names[adopted_idx] if adopted_idx < len(names) else None
        if len(cells) < ncand or any((not c) or (c != "採用" and c != "不採用") for c in cells):
            errors.append("判定は全候補について「採用」または「不採用」と完全一致で書く（空セル不可）")
    qrow = row("要求")
    if not qrow:
        errors.append("「要求・不変条件・安全性を満たすか」行がない（複雑性比較は要求適合が前提）")
    else:
        for i, c in enumerate(qrow[1:1 + ncand]):
            if not re.match(r"^(PASS|FAIL)\b", c.strip()):
                errors.append(f"要求適合セルは PASS/FAIL + 根拠 で書く: 「{c.strip()[:30]}」")
            elif i == adopted_idx and c.strip().startswith("FAIL"):
                errors.append("要求適合が FAIL のモデルを採用している（FAIL 候補は星の数によらず不採用）")
    for kw, label in (("歪み", "歪み"), ("勝つ", "このモデルが勝つ現在条件")):
        r = row(kw)
        if not r:
            errors.append(f"メンタルモデル表に「{label}」行がない")
        else:
            for c in r[1:1 + ncand]:
                if not c.strip() or (kw == "歪み" and ZERO_DISTORTION.match(c.strip())):
                    errors.append(f"「{label}」は全候補に非空で書く（ゼロ申告・空セルは検討不足）")
                    break
    nonscored = ("判定", "歪み", "勝つ", "要求", "一文")
    scored = [r for r in rows if r and not any(k in r[0] for k in nonscored)]
    ok = 0
    for r in scored:
        if len(r) >= ncand + 1 and all(c.strip() and re.match(r"^[◎○△×]", c.strip()) for c in r[1:1 + ncand]):
            ok += 1
        else:
            errors.append(f"採点行「{r[0][:20]}」に空セルまたは記号なしセルがある（全候補を ◎○△× で採点する）")
    if ok < 2:
        errors.append("◎○△× で全候補が採点された軸が2行未満（空セル・記号なしは採点と認めない）")
    return errors, adopted_name


EMPTY_CONTRACT = re.compile(r"^(なし|特になし|該当なし|N/?A|-|ー)[。.]?$", re.I)
GENERIC_CAUSE = re.compile(
    r"^(状態管理|所有権|整合性|境界|設計|実装)(の)?(問題|不備|ミス)$|^(考慮漏れ|実装ミス|設計が悪い)$"
)


def _norm_cell(value):
    return unicodedata.normalize("NFKC", value).strip().lower().replace(" ", "")


def _column(header, *aliases):
    aliases = tuple(_norm_cell(a) for a in aliases)
    for i, cell in enumerate(header):
        c = _norm_cell(cell)
        if any(a in c for a in aliases):
            return i
    return -1


def _table_with_columns(lines, required):
    """required の各 alias 群を満たす表を一つだけ返す。"""
    matches = []
    for header, rows in parse_tables(lines):
        if all(_column(header, *aliases) >= 0 for aliases in required):
            matches.append((header, rows))
    return matches


def _parse_ids(cell, pattern):
    """裸IDのカンマ区切りだけを受理する。戻り値は (ids, valid_syntax)。"""
    raw = re.sub(r"`", "", cell).strip()
    parts = [p.strip() for p in re.split(r"[,、]", raw) if p.strip()]
    if not parts or any(not re.fullmatch(pattern, p) for p in parts):
        return [], False
    return parts, True


def _contract_blocks(lines, kind, id_pattern):
    """`#### Kind ID: name` ブロックを [(id, heading, body)] で返す。"""
    blocks = []
    i = 0
    rx = re.compile(rf"^(#{{3,6}})\s+{re.escape(kind)}\s+({id_pattern})\s*[:：]\s*(.+)$", re.I)
    while i < len(lines):
        m = rx.match(lines[i])
        if not m:
            i += 1
            continue
        level = len(m.group(1))
        body, j = [], i + 1
        while j < len(lines):
            hm = re.match(r"^(#{1,6})\s+", lines[j])
            if hm and len(hm.group(1)) <= level:
                break
            body.append(lines[j])
            j += 1
        blocks.append((m.group(2).upper(), m.group(3).strip(), body))
        i = j
    return blocks


def _fields(body):
    out = {}
    for line in body:
        m = re.match(r"^\s*-\s*([^:：]+)\s*[:：]\s*(.*)$", line)
        if m:
            out.setdefault(_norm_cell(m.group(1)), []).append(m.group(2).strip())
    return out


def _field(fields, *aliases):
    hits = []
    for key, values in fields.items():
        if any(_norm_cell(a) == key for a in aliases):
            hits.extend(values)
    return hits


def _require_block_fields(kind, ident, body, spec, non_applicable_forbidden=()):
    """spec は [(表示名, alias...)]。各ラベルをちょうど1回かつ非空で要求する。"""
    errors, values = [], {}
    fs = _fields(body)
    for item in spec:
        label, aliases = item[0], item[1:]
        hits = _field(fs, *aliases)
        if len(hits) != 1:
            errors.append(f"{kind} {ident} の「{label}」はちょうど1行必要（現在{len(hits)}行）")
            continue
        value = hits[0].strip()
        values[label] = value
        if not value or EMPTY_CONTRACT.match(value):
            errors.append(f"{kind} {ident} の「{label}」が空または理由のない非該当")
        if value.startswith("対象外") and "理由" not in value and "ため" not in value:
            errors.append(f"{kind} {ident} の「{label}」を対象外にする場合は理由を書く")
        if label in non_applicable_forbidden and value.startswith("対象外"):
            errors.append(f"{kind} {ident} の「{label}」は対象外にできない")
    return errors, values


def validate_approach_comparison(lines, known_lcus=None):
    """現実的な複数案が残る判断だけを比較する。比較は0件または複数件を許す。"""
    errors = []
    tables = [(h, r) for h, r in parse_tables(lines) if h and "軸" in h[0] and r]
    no_compare = [l for l in lines if re.match(r"^\s*-?\s*比較(?:不要|なし)\s*[:：]", l)]
    if tables and no_compare:
        errors.append("解決アプローチ比較に比較表と「比較不要/比較なし」が同居している")
    if not tables:
        if len(no_compare) != 1:
            errors.append("解決アプローチ比較を省略する場合は「比較不要: 具体的な理由」をちょうど1行書く")
        else:
            reason = re.split(r"[:：]", no_compare[0], maxsplit=1)[-1].strip()
            if len(reason) < 12 or EMPTY_CONTRACT.match(reason):
                errors.append("「比較不要」の理由が抽象的すぎる（要求・既存契約・可逆性など、この案件の根拠を書く）")
        return errors
    targets = [l for l in lines if re.match(r"^\s*-?\s*比較対象\s*[:：]", l)]
    associated_targets = []
    for i, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        cells = split_row(line)
        next_is_separator = i + 1 < len(lines) and bool(
            re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip())
        )
        if not cells or _norm_cell(cells[0]) != _norm_cell("軸") or not next_is_separator:
            continue
        j = i - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        if j < 0 or not re.match(r"^\s*-?\s*比較対象\s*[:：]\s*\S", lines[j]):
            errors.append("各アプローチ比較表の直前に「比較対象: LCU-ID または判断名」を書く")
        else:
            associated_targets.append(lines[j])
    if len(targets) != len(tables) or len(associated_targets) != len(tables):
        errors.append("アプローチ比較は比較対象1行と表1件を交互に対応させる")
    if known_lcus is not None:
        for line in targets:
            refs = re.findall(r"LCU-\d+", line)
            for ref in refs:
                if ref not in known_lcus:
                    errors.append(f"アプローチ比較が未知の LCU {ref} を比較対象にしている")
    for i, table in enumerate(tables, 1):
        terr, _ = validate_mental_model([table])
        errors.extend(f"比較表{i}: {e.replace('メンタルモデル', 'アプローチ')}" for e in terr)
    return errors


def validate_lite_causal(prose_lines):
    errors = []
    body = section_body(prose_lines, "問題と解決モデル")
    required = ("観測事象", "原因", "違反した不変条件", "証拠", "反証条件", "解決方針", "同型探索")
    fs = _fields(body)
    for label in required:
        hits = _field(fs, label)
        if len(hits) != 1 or not hits[0].strip() or EMPTY_CONTRACT.match(hits[0].strip()):
            errors.append(f"lite の問題と解決モデルに「{label}: 具体値」がちょうど1行必要")
    ev = _field(fs, "証拠")
    if ev and "`" not in ev[0]:
        errors.append("lite の証拠はコード位置・コマンド・テスト名をバッククォート付きで書く")
    search = _field(fs, "同型探索")
    if search and "`" not in search[0]:
        errors.append("lite の同型探索は実行した検索コマンドをバッククォート付きで書く")
    return errors


def validate_causal_contract(prose_lines, mode, known_phases=None):
    """corrective plan の因果分析から LCU・Step までを一つの参照グラフとして検査する。"""
    errors, warnings = [], []
    analysis = {"findings": set(), "causes": set(), "models": set(), "repairs": set(), "lcus": set()}
    input_match = INPUT_TYPE_RE.search("\n".join(prose_lines))
    input_type = input_match.group(1) if input_match else ""
    if mode == "scaled-phase" and not input_type:
        errors.append("設計ハブ phase に入力タイプがない")
    if input_type == "corrective-single" or input_type == "feature-gap":
        expected_input_count = 1
    else:
        bundle_match = re.fullmatch(r"corrective-bundle\((\d+)\)", input_type)
        expected_input_count = int(bundle_match.group(1)) if bundle_match else 0
        if bundle_match and expected_input_count < 2:
            errors.append("corrective-bundle(N) の N は2以上にする。1件は corrective-single を使う")

    initial_findings = set()
    causal_lines = section_body(prose_lines, "因果分析")
    finding_tables = _table_with_columns(
        causal_lines,
        (("Finding",), ("Input Ref", "入力 Ref"), ("観測", "failure"), ("期待動作",), ("出典", "証拠")),
    )
    if len(finding_tables) != 1:
        errors.append(f"因果分析に Finding 台帳がちょうど1つ必要（現在{len(finding_tables)}個）")
        candidates = _table_with_columns(causal_lines, (("Finding",), ("観測", "failure")))
        if len(candidates) == 1:
            header = candidates[0][0]
            missing = []
            for label, aliases in (
                ("Input Ref", ("Input Ref", "入力 Ref")),
                ("期待動作", ("期待動作",)),
                ("出典・再現証拠", ("出典", "証拠")),
            ):
                if _column(header, *aliases) < 0:
                    missing.append(label)
            if missing:
                errors.append(f"Finding 台帳の必須列が不足している: {missing}")
        return errors, warnings, analysis
    else:
        header, rows = finding_tables[0]
        fidx = _column(header, "Finding")
        input_idx = _column(header, "Input Ref", "入力 Ref")
        required_idx = [
            fidx,
            input_idx,
            _column(header, "観測", "failure"),
            _column(header, "期待動作"),
            _column(header, "出典", "証拠"),
        ]
        findings = set()
        initial_input_refs, pending_variant_sources = set(), []
        if not rows:
            errors.append("Finding 台帳にデータ行がない")
        for row in rows:
            if len(row) != len(header):
                errors.append("Finding 台帳の行のセル数がヘッダと不一致")
                continue
            fid = row[fidx].strip().upper()
            if not re.fullmatch(r"F-\d+", fid):
                errors.append(f"Finding ID は F-01 形式で書く: 「{fid}」")
                continue
            if fid in findings:
                errors.append(f"Finding ID が重複している: {fid}")
            findings.add(fid)
            if any(i < 0 or i >= len(row) or not row[i].strip() for i in required_idx):
                errors.append(f"Finding {fid} の観測failureまたは出典が空")
            source = row[input_idx].strip() if 0 <= input_idx < len(row) else ""
            vm = re.fullmatch(r"variant\s*[:：]\s*(V-\d+)", source, re.I)
            if vm:
                pending_variant_sources.append((fid, vm.group(1).upper()))
            else:
                initial_findings.add(fid)
                refs, ok = _parse_ids(source, r"I-\d+")
                if not ok:
                    errors.append(f"Finding {fid} の Input Ref は I-01 のカンマ区切りか `variant: V-01` で書く")
                for ref in refs:
                    if ref in initial_input_refs:
                        errors.append(f"入力 Ref {ref} が複数 Finding に重複している")
                    initial_input_refs.add(ref)
        expected_refs = {f"I-{i:02d}" for i in range(1, expected_input_count + 1)}
        if expected_input_count and initial_input_refs != expected_refs:
            errors.append(
                f"入力タイプ {input_type} と Finding 台帳の初期 Input Ref が不一致: "
                f"expected={sorted(expected_refs)} actual={sorted(initial_input_refs)}"
            )
        analysis["findings"] = findings
        analysis["initial_findings"] = initial_findings
    cause_spec = (
        ("関連 Finding", "関連finding"),
        ("error state / 発生機構", "errorstate/発生機構"),
        ("近接原因", "近接原因"),
        ("違反した不変条件", "違反した不変条件"),
        ("設計原因・寄与条件", "設計原因・寄与条件"),
        ("証拠", "証拠"),
        ("falsifier", "falsifier", "反証条件"),
        ("判定", "判定"),
    )
    cause_blocks = _contract_blocks(causal_lines, "Cause", r"C-\d+")
    if not cause_blocks:
        errors.append("因果分析に `#### Cause C-01: 名前` ブロックがない")
    adopted_causes, cause_findings, finding_adopted = set(), {}, {f: 0 for f in findings}
    seen_causes = set()
    for cid, _name, body in cause_blocks:
        if cid in seen_causes:
            errors.append(f"Cause ID が重複している: {cid}")
        seen_causes.add(cid)
        e, vals = _require_block_fields("Cause", cid, body, cause_spec,
                                        ("違反した不変条件", "設計原因・寄与条件", "証拠", "falsifier"))
        errors.extend(e)
        fids, ok = _parse_ids(vals.get("関連 Finding", ""), r"F-\d+")
        if not ok:
            errors.append(f"Cause {cid} の関連 Finding は裸IDのカンマ区切りで書く")
        for fid in fids:
            if fid not in findings:
                errors.append(f"Cause {cid} が未知の Finding {fid} を参照している")
        verdict = vals.get("判定", "")
        if verdict not in ("採用", "棄却", "未確定"):
            errors.append(f"Cause {cid} の判定は 採用 / 棄却 / 未確定 と完全一致で書く")
        if verdict == "採用":
            adopted_causes.add(cid)
            cause_findings[cid] = set(fids)
            for fid in fids:
                if fid in finding_adopted:
                    finding_adopted[fid] += 1
        if verdict == "未確定":
            unclear = "\n".join(section_body(prose_lines, "Unclear Points"))
            if cid not in unclear:
                errors.append(f"未確定 Cause {cid} は Unclear Points に明示する")
        design_cause = vals.get("設計原因・寄与条件", "")
        if GENERIC_CAUSE.fullmatch(design_cause.strip()):
            warnings.append(f"Cause {cid} の設計原因が傘語だけに見える: 「{design_cause}」")
        evidence = vals.get("証拠", "")
        if evidence and "`" not in evidence:
            errors.append(f"Cause {cid} の証拠はコード位置・コマンド・テスト名をバッククォート付きで書く")
        falsifier = vals.get("falsifier", "")
        if falsifier and EMPTY_CONTRACT.match(falsifier):
            errors.append(f"Cause {cid} の falsifier に「なし」は使えない")
    for fid, count in finding_adopted.items():
        if count == 0:
            errors.append(f"Finding {fid} に採用 Cause が関連付いていない")
    analysis["causes"] = adopted_causes

    model_lines = section_body(prose_lines, "解決の概念モデル")
    model_spec = (
        ("対象 Cause", "対象cause"),
        ("authoritative source", "authoritativesource"),
        ("derived projection", "derivedprojection"),
        ("責務の owner", "責務のowner"),
        ("identity / boundary", "identity/boundary"),
        ("state / time semantics", "state/timesemantics"),
        ("成立させる不変条件", "成立させる不変条件"),
    )
    models, seen_models = {}, set()
    model_blocks = _contract_blocks(model_lines, "Model", r"M-\d+")
    if not model_blocks:
        errors.append("解決の概念モデルに `#### Model M-01: 名前` ブロックがない")
    for mid, _name, body in model_blocks:
        if mid in seen_models:
            errors.append(f"Model ID が重複している: {mid}")
        seen_models.add(mid)
        e, vals = _require_block_fields("Model", mid, body, model_spec,
                                        ("authoritative source", "責務の owner", "成立させる不変条件"))
        errors.extend(e)
        cids, ok = _parse_ids(vals.get("対象 Cause", ""), r"C-\d+")
        if not ok:
            errors.append(f"Model {mid} の対象 Cause は裸IDのカンマ区切りで書く")
        for cid in cids:
            if cid not in adopted_causes:
                errors.append(f"Model {mid} が未知または非採用の Cause {cid} を参照している")
        models[mid] = set(cids)
    modeled_causes = set().union(*models.values()) if models else set()
    for cid in adopted_causes - modeled_causes:
        errors.append(f"採用 Cause {cid} を対象にする Model がない")
    analysis["models"] = set(models)

    repair_lines = section_body(prose_lines, "修復モデル")
    repair_spec = (
        ("対象 Model", "対象model"),
        ("authoritative / observed state", "authoritative/observedstate"),
        ("mechanism", "mechanism"),
        ("trigger", "trigger"),
        ("enforcement point", "enforcementpoint"),
        ("transaction boundary", "transactionboundary"),
        ("postcondition", "postcondition"),
        ("idempotency", "idempotency"),
        ("concurrency control", "concurrencycontrol"),
        ("cleanup / migration", "cleanup/migration"),
        ("retired paths", "retiredpaths"),
    )
    repairs, seen_repairs = {}, set()
    repair_blocks = _contract_blocks(repair_lines, "Repair", r"R-\d+")
    if not repair_blocks:
        errors.append("修復モデルに `#### Repair R-01: 名前` ブロックがない")
    for rid, _name, body in repair_blocks:
        if rid in seen_repairs:
            errors.append(f"Repair ID が重複している: {rid}")
        seen_repairs.add(rid)
        e, vals = _require_block_fields(
            "Repair", rid, body, repair_spec,
            ("authoritative / observed state", "mechanism", "trigger", "enforcement point", "postcondition"),
        )
        errors.extend(e)
        mids, ok = _parse_ids(vals.get("対象 Model", ""), r"M-\d+")
        if not ok or len(mids) != 1:
            errors.append(f"Repair {rid} の対象 Model は既知IDを1件だけ書く")
        elif mids[0] not in models:
            errors.append(f"Repair {rid} が未知の Model {mids[0]} を参照している")
        repairs[rid] = mids[0] if len(mids) == 1 else None
    repaired_models = {mid for mid in repairs.values() if mid}
    for mid in set(models) - repaired_models:
        errors.append(f"Model {mid} を実現する Repair がない")
    analysis["repairs"] = set(repairs)

    lcu_lines = section_body(prose_lines, "論理的変更単位")
    lcu_tables = _table_with_columns(
        lcu_lines,
        (("LCU",), ("Primary Finding",), ("Cause",), ("Target Model",), ("Repair",), ("実装先",)),
    )
    lcus, primary_counts = {}, {f: 0 for f in findings}
    used_models, used_repairs = set(), set()
    if len(lcu_tables) != 1:
        errors.append(f"論理的変更単位に LCU 表がちょうど1つ必要（現在{len(lcu_tables)}個）")
    else:
        header, rows = lcu_tables[0]
        idx = {
            "id": _column(header, "LCU"), "finding": _column(header, "Primary Finding"),
            "cause": _column(header, "Cause"), "model": _column(header, "Target Model"),
            "repair": _column(header, "Repair"), "reason": _column(header, "併合・分離の根拠"),
            "target": _column(header, "実装先"),
        }
        if idx["reason"] < 0:
            errors.append("LCU 表に「併合・分離の根拠」列がない")
        for row in rows:
            if len(row) != len(header):
                errors.append("LCU 表の行のセル数がヘッダと不一致")
                continue
            lid = row[idx["id"]].strip().upper()
            if not re.fullmatch(r"LCU-\d+", lid):
                errors.append(f"LCU ID は LCU-01 形式で書く: 「{lid}」")
                continue
            if lid in lcus:
                errors.append(f"LCU ID が重複している: {lid}")
            fids, fok = _parse_ids(row[idx["finding"]], r"F-\d+")
            cids, cok = _parse_ids(row[idx["cause"]], r"C-\d+")
            mids, mok = _parse_ids(row[idx["model"]], r"M-\d+")
            rids, rok = _parse_ids(row[idx["repair"]], r"R-\d+")
            if not fok:
                errors.append(f"LCU {lid} の Primary Finding は裸IDのカンマ区切りで書く")
            if not cok or any(c not in adopted_causes for c in cids):
                errors.append(f"LCU {lid} が未知または非採用の Cause を参照している")
            if not mok or len(mids) != 1 or mids[0] not in models:
                errors.append(f"LCU {lid} の Target Model は既知IDを1件だけ書く")
            if not rok or len(rids) != 1 or rids[0] not in repairs:
                errors.append(f"LCU {lid} の Repair は既知IDを1件だけ書く")
            if len(mids) == 1 and mids[0] in models:
                used_models.add(mids[0])
                if any(cid not in models[mids[0]] for cid in cids):
                    errors.append(f"LCU {lid} の Cause が Target Model {mids[0]} の対象 Cause と不一致")
            if len(rids) == 1 and rids[0] in repairs:
                used_repairs.add(rids[0])
                if len(mids) == 1 and repairs[rids[0]] != mids[0]:
                    errors.append(f"LCU {lid} の Repair {rids[0]} が Target Model {mids[0]} を実現しない")
            for fid in fids:
                if fid not in primary_counts:
                    errors.append(f"LCU {lid} が未知の Finding {fid} を Primary にしている")
                else:
                    primary_counts[fid] += 1
                if cids and not any(fid in cause_findings.get(cid, set()) for cid in cids):
                    errors.append(f"LCU {lid} の Primary Finding {fid} と Cause の関連が因果分析にない")
            reason = row[idx["reason"]].strip() if idx["reason"] >= 0 else ""
            if len(fids) > 1:
                expected_prefix = "併合:"
            elif len(findings) == 1:
                expected_prefix = "単独:"
            else:
                expected_prefix = "分離:"
            if not reason.startswith(expected_prefix):
                errors.append(f"LCU {lid} の根拠は「{expected_prefix}」で始める")
            target = row[idx["target"]].strip()
            target_kind = "Step" if mode == "full" else "Phase"
            target_parts = [p.strip() for p in re.split(r"[,、]", target) if p.strip()]
            if not target_parts or any(not re.fullmatch(rf"{target_kind}\s+\d+", p) for p in target_parts):
                errors.append(f"LCU {lid} の実装先は `{target_kind} N` のカンマ区切りで書く")
                target_parts = []
            target_ids = set(target_parts)
            if known_phases is not None:
                for part in target_ids:
                    pnum = int(part.split()[1])
                    if pnum not in known_phases:
                        errors.append(f"LCU {lid} が存在しない Phase {pnum} を実装先にしている")
            lcus[lid] = {"findings": set(fids), "target": target, "target_ids": target_ids}
    for fid, count in primary_counts.items():
        if count != 1:
            errors.append(f"Finding {fid} の Primary LCU 所属はちょうど1つにする（現在{count}）")
    for mid in set(models) - used_models:
        errors.append(f"Model {mid} を参照する LCU がない")
    for rid in set(repairs) - used_repairs:
        errors.append(f"Repair {rid} を参照する LCU がない")
    analysis["lcus"] = set(lcus)
    analysis["lcu_meta"] = lcus

    if mode == "scaled-phase":
        split_blocks = _contract_blocks(lcu_lines, "LCU Split", r"LCU-\d+")
        split_by_lcu = {}
        split_gates = {}
        for lid, _name, body in split_blocks:
            if lid in split_by_lcu:
                errors.append(f"LCU Split {lid} が重複している")
            split_by_lcu[lid] = body
        for lid in split_by_lcu:
            if lid not in lcus:
                errors.append(f"LCU Split が未知の LCU {lid} を参照している")
            elif len(lcus[lid].get("target_ids", set())) <= 1:
                errors.append(f"単一 phase の {lid} に LCU Split を置けない")
        for lid, meta in lcus.items():
            if len(meta.get("target_ids", set())) <= 1:
                continue
            if lid not in split_by_lcu:
                errors.append(f"複数 phase にまたがる {lid} に `#### LCU Split {lid}: ...` 契約がない")
                continue
            spec = (
                ("分割理由", "分割理由"),
                ("中間 postcondition", "中間postcondition"),
                ("最終 Gate", "最終gate"),
            )
            e, vals = _require_block_fields(
                "LCU Split", lid, split_by_lcu[lid], spec,
                ("分割理由", "中間 postcondition", "最終 Gate"),
            )
            errors.extend(e)
            intermediate = vals.get("中間 postcondition", "")
            phases = sorted(int(t.split()[1]) for t in meta["target_ids"])
            expected_intermediate = set(phases[:-1])
            actual_intermediate = {
                int(n) for n in re.findall(r"(?<![A-Za-z0-9_])Phase\s+(\d+)\s*=", intermediate)
            }
            if actual_intermediate != expected_intermediate:
                errors.append(
                    f"LCU Split {lid} の中間 postcondition は中間 phase を `Phase N=` で過不足なく書く: "
                    f"expected={sorted(expected_intermediate)} actual={sorted(actual_intermediate)}"
                )
            final_gate = vals.get("最終 Gate", "")
            gate_match = re.search(r"`(P\d+-S\d+)`", final_gate)
            if not gate_match:
                errors.append(f"LCU Split {lid} の最終 Gate はバッククォート付き `PN-SN` で書く")
            else:
                split_gates[lid] = gate_match.group(1)
        analysis["split_gates"] = split_gates

    variant_lines = section_body(prose_lines, "同型探索")
    variant_spec = (
        ("Seed", "seed"), ("探索軸", "探索軸"), ("Query / scope", "query/scope"),
        ("結果・証拠", "結果・証拠"), ("扱い", "扱い"), ("LCU", "lcu"),
    )
    variants = _contract_blocks(variant_lines, "Variant", r"V-\d+")
    if not variants:
        errors.append("同型探索に `#### Variant V-01: 名前` ブロックがない")
    covered_lcus, seen_variants, variant_includes = set(), set(), {}
    for vid, _name, body in variants:
        if vid in seen_variants:
            errors.append(f"Variant ID が重複している: {vid}")
        seen_variants.add(vid)
        e, vals = _require_block_fields("Variant", vid, body, variant_spec, ("Query / scope", "結果・証拠", "扱い"))
        errors.extend(e)
        seed = vals.get("Seed", "").strip().upper()
        known_seeds = findings | adopted_causes | set(models) | set(repairs) | set(lcus)
        if not re.fullmatch(r"(?:F|C|M|R)-\d+|LCU-\d+", seed) or seed not in known_seeds:
            errors.append(
                f"Variant {vid} の Seed は既知の Finding / Cause / Model / Repair / LCU IDを1件だけ書く"
            )
        else:
            rooted_causes = {
                cid for cid, fids in cause_findings.items() if fids & initial_findings
            }
            rooted_models = {
                mid for mid, cids in models.items() if cids & rooted_causes
            }
            rooted_repairs = {
                rid for rid, mid in repairs.items() if mid in rooted_models
            }
            rooted_lcus = {
                lid for lid, meta in lcus.items() if meta.get("findings", set()) & initial_findings
            }
            rooted_seeds = initial_findings | rooted_causes | rooted_models | rooted_repairs | rooted_lcus
            if seed not in rooted_seeds:
                errors.append(f"Variant {vid} の Seed {seed} が初期 Input Ref を持つ Finding へ遡れない")
        lids, ok = _parse_ids(vals.get("LCU", ""), r"LCU-\d+")
        if not ok or len(lids) != 1 or lids[0] not in lcus:
            errors.append(f"Variant {vid} の LCU は既知IDを1件だけ書く")
        else:
            covered_lcus.add(lids[0])
        query = vals.get("Query / scope", "")
        if query and "`" not in query:
            errors.append(f"Variant {vid} の Query / scope は実行した検索をバッククォート付きで書く")
        handling = vals.get("扱い", "")
        hm = re.match(r"^(include|exclude)\s*[:：]\s*(.+)$", handling)
        if not hm:
            errors.append(f"Variant {vid} の扱いは `include: ...` または `exclude: 理由` で書く")
        elif hm.group(1) == "include":
            inc_ids = re.findall(r"F-\d+", hm.group(2))
            variant_includes[vid] = set(inc_ids)
            if not inc_ids:
                errors.append(f"Variant {vid} の include は追加・既存 Finding ID を明示する")
            elif any(fid not in findings for fid in inc_ids):
                errors.append(f"Variant {vid} が台帳外 Finding を include している")
            elif lids and any(fid not in lcus.get(lids[0], {}).get("findings", set()) for fid in inc_ids):
                errors.append(f"Variant {vid} の include Finding が参照 LCU の Primary Finding にない")
    variant_ids = {vid for vid, _name, _body in variants}
    for fid, vid in pending_variant_sources:
        if vid not in variant_ids:
            errors.append(f"Finding {fid} の Input Ref が未知の Variant {vid} を参照している")
        elif fid not in variant_includes.get(vid, set()):
            errors.append(f"Finding {fid} は {vid} 起源だが、Variant の扱いがその Finding を include していない")
    for lid in lcus:
        if lid not in covered_lcus:
            errors.append(f"LCU {lid} に同型探索がない")

    step_bodies = re.split(r"^###\s+Step\b", "\n".join(prose_lines), flags=re.M)[1:]
    primary_step_coverage = {lid: 0 for lid in lcus}
    primary_steps = {lid: set() for lid in lcus}
    step_primary_tags = {}
    for i, sb in enumerate(step_bodies, 1):
        own = re.split(r"^##\s+", sb, flags=re.M)[0]
        pm = re.findall(r"^\s*-\s*Primary LCU\s*[:：]\s*([^\n]+)$", own, re.M)
        if len(pm) != 1:
            errors.append(f"Step {i} に Primary LCU 行がちょうど1つ必要")
            continue
        primary = pm[0].strip()
        step_primary_tags[i] = primary
        if primary == "plan-maintenance":
            if mode != "scaled-phase":
                errors.append("Primary LCU: plan-maintenance は scaled-phase の次フェーズ詳細化 Step だけで使える")
            elif "詳細化" not in own:
                errors.append(f"Step {i} は詳細化Stepでないため Primary LCU: plan-maintenance を使えない")
        elif primary not in lcus:
            errors.append(f"Step {i} が未知の Primary LCU {primary} を参照している")
        else:
            primary_step_coverage[primary] += 1
            primary_steps[primary].add(f"Step {i}")
            if mode == "full" and f"Step {i}" not in lcus[primary]["target_ids"]:
                errors.append(
                    f"Step {i} の Primary LCU {primary} が LCU 表の実装先（{lcus[primary]['target']}）と不一致"
                )
        sm = re.findall(r"^\s*-\s*Shared LCU\s*[:：]\s*([^\n]+)$", own, re.M)
        if len(sm) > 1:
            errors.append(f"Step {i} の Shared LCU 行が複数ある")
        elif sm:
            raw = sm[0]
            if "—" not in raw and "－" not in raw:
                errors.append(f"Step {i} の Shared LCU には `— 共有理由` が必要")
                refs, ok = [], False
            else:
                ids_part, reason = re.split(r"\s*[—－]\s*", raw, maxsplit=1)
                refs, ok = _parse_ids(ids_part, r"LCU-\d+")
                if not reason.strip():
                    errors.append(f"Step {i} の Shared LCU の共有理由が空")
            if not ok or any(r not in lcus for r in refs):
                errors.append(f"Step {i} の Shared LCU に未知IDまたは不正書式がある")
            if primary in refs:
                errors.append(f"Step {i} の Primary LCU と Shared LCU が重複している")
    if mode == "full":
        for lid, count in primary_step_coverage.items():
            if count == 0:
                errors.append(f"LCU {lid} を Primary とする Step がない")
            elif primary_steps[lid] != lcus[lid]["target_ids"]:
                errors.append(
                    f"LCU {lid} の実装先（{sorted(lcus[lid]['target_ids'])}）が Primary Step（{sorted(primary_steps[lid])}）と不一致"
                )

    todo_keyword = "TodoList" if mode == "full" else "フェーズ Todo"
    plan_todo = section_body(prose_lines, todo_keyword)
    todo_tags = {}
    for line in plan_todo:
        tm = re.match(r"^\s*- \[[ x!\-]\]\s*Step\s+(\d+)\s+\(([^)]+)\)\s*[:：]", line)
        if not tm:
            continue
        step_num = int(tm.group(1))
        if step_num in todo_tags:
            errors.append(f"{todo_keyword} に Step {step_num} のタスク行が重複している")
        todo_tags[step_num] = tm.group(2).strip()
    for step_num, primary in step_primary_tags.items():
        actual = todo_tags.get(step_num)
        if actual != primary:
            errors.append(
                f"{todo_keyword} の Step {step_num} Primary LCU タグは {primary} と完全一致させる"
                f"（現在: {actual or 'なし'}）"
            )
    for step_num in set(todo_tags) - set(step_primary_tags):
        errors.append(f"{todo_keyword} に存在しない Step {step_num} のタスク行がある")

    decision_lines = section_body(prose_lines, "設計判断")
    decision_tables = _table_with_columns(decision_lines, (("判断",), ("採用",), ("根拠",), ("LCU",)))
    if len(decision_tables) != 1 or not decision_tables[0][1]:
        errors.append("設計判断に `判断 | 採用 | 根拠 | LCU` のデータ行付き表がちょうど1つ必要")
    else:
        header, rows = decision_tables[0]
        didx = [_column(header, "判断"), _column(header, "採用"), _column(header, "根拠"), _column(header, "LCU")]
        for row in rows:
            if len(row) != len(header) or any(not row[i].strip() for i in didx):
                errors.append("設計判断表に空セルまたはセル数不一致がある")
                continue
            refs = re.findall(r"LCU-\d+", row[didx[3]])
            if row[didx[3]].strip() != "全体" and (not refs or any(r not in lcus for r in refs)):
                errors.append(f"設計判断が未知の LCU を参照している: {row[didx[3]]}")

    errors.extend(validate_approach_comparison(section_body(prose_lines, "解決アプローチ比較"), set(lcus)))
    return errors, warnings, analysis


def clean_refs_text(text):
    """参照抽出用の可視テキスト（フェンス・HTML コメント除去は strip_code_blocks と共有）。"""
    lines, _ = strip_code_blocks(text.splitlines())
    return "\n".join(l for _, l in lines)


def extract_ref(text, label, scope="header"):
    """`- <label>: `path`` の参照を読む（フェンス・コメント内は無視）。

    scope="header" は最初の h2 より前だけを対象にする。
    戻り値: (値 or None, 出現数)。同一ラベルが複数あれば呼び出し側で失格にする。
    """
    cleaned = clean_refs_text(text)
    if scope == "header":
        cleaned = re.split(r"^##\s", cleaned, maxsplit=1, flags=re.M)[0]
    ms = list(re.finditer(rf"^-\s*{label}\s*[:：]\s*`?([^`\n（）]+)", cleaned, re.M))
    if not ms:
        return None, 0
    val = ms[0].group(1).strip().rstrip("`")
    if not val or val.startswith("なし") or val in ("-", "ー"):
        return None, len(ms)
    return val, len(ms)


# ---------- ファイル種別 lint ----------

def lint_todo(path: Path, text: str):
    errors, warnings = [], []
    lines = text.splitlines()
    prose, unclosed = strip_code_blocks(lines)
    if unclosed:
        errors.append("コードフェンス（```）が閉じられていない")
    prose_lines = [l for _, l in prose]
    vis_text = "\n".join(prose_lines)
    for kw in TODO_REQUIRED:
        if kw not in vis_text:
            errors.append(f"todo.md に必須要素がない（コメント内は不可・可視テキストに書く）: {kw}")
    if not re.search(r"^\s*- \[[ x!\-]\]", vis_text, re.M):
        errors.append("todo.md にチェックボックスが1つもない")
    unres = [l for l in prose_lines if "未解決" in l and re.search(r"[:：]", l)]
    if len(unres) != 1:
        errors.append(f"todo.md の「未解決:」要約行はちょうど1行必要（現在{len(unres)}行。着手可否判定の正）")
    report_placeholders(prose, errors)
    ge, gw = scan_gates(prose_lines)
    errors.extend(ge)
    warnings.extend(gw)
    gate_tables = [(h, r) for h, r in parse_tables(prose_lines) if any("Command" in c for c in h)]
    if not gate_tables or not any(r for _, r in gate_tables):
        errors.append("todo.md に Hard Gate 表（Command 列 + データ行）がない")
    seen_ids = set()
    for header, rows in gate_tables:
        ridx = next((i for i, c in enumerate(header) if "結果" in c), -1)
        sidx = next((i for i, c in enumerate(header) if "Step" in c), 0)
        cidx = next((i for i, c in enumerate(header) if "Command" in c), -1)
        eidx = next((i for i, c in enumerate(header) if ("Expect" in c) or ("期待値" in c)), -1)
        if ridx < 0:
            errors.append("Hard Gate 表に「結果」列がない")
            continue
        for row in rows:
            gid = row[sidx].strip() if sidx < len(row) else ""
            if gid in seen_ids:
                errors.append(f"Hard Gate 表に同じ ID の行が複数ある: {gid}")
            seen_ids.add(gid)
            if cidx >= 0 and (cidx >= len(row) or "`" not in row[cidx]):
                errors.append(f"Hard Gate 表の Command が空またはバッククォートなし（ID: {gid}）")
            if eidx >= 0 and (eidx >= len(row) or not row[eidx].strip()):
                errors.append(f"Hard Gate 表の Expect が空（ID: {gid}）")
            val = row[ridx].strip() if ridx < len(row) else ""
            if not re.fullmatch(r"(PASS|FAIL|未実行)", val):
                errors.append(f"Hard Gate の結果値は PASS / FAIL / 未実行 と完全一致で書く: 「{val[:30]}」")
    for n, line in prose:
        if re.match(r"^\s*- \[[!\-]\]", line):
            body = re.sub(r"[（(]\s*Gate\s*[:：][^）)]*[）)]", "", line)
            if not re.search(r"[（(][^）)]+[）)]", body):
                errors.append(f"L{n}: blocked/cancelled に理由（括弧書き）がない")
            if re.match(r"^\s*- \[\-\]", line) and not re.search(r"(ユーザー指示|計画改訂)", line):
                errors.append(f"L{n}: cancelled は「ユーザー指示」か「計画改訂」への紐づけが必要")
    return errors, warnings


def visible_lines_keep_fences(lines):
    """HTML コメントを除去し、コードフェンスは中身ごと保持した可視行を返す。

    コメントアウトされた旧フェンスを検査対象にしないための前処理
    （フェンス内の <!-- はコード、コメント内の ``` はフェンスでない）。
    """
    out, in_fence, in_comment = [], False, False
    for line in lines:
        if not in_comment and line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        res, j = [], 0
        while j is not None and j <= len(line):
            if in_comment:
                k = line.find("-->", j)
                if k < 0:
                    j = None
                else:
                    j, in_comment = k + 3, False
            else:
                k = line.find("<!--", j)
                if k < 0:
                    res.append(line[j:])
                    j = None
                else:
                    res.append(line[j:k])
                    j, in_comment = k + 4, True
        out.append("".join(res))
    return out


def goal_sections(lines):
    """h2 見出し（フェンス・コメント外）ごとに本文行を返す: [(見出し, [行])]（出現順・同名も別要素）。"""
    prose, unclosed = strip_code_blocks(lines)
    secs, cur = [], None
    for _, l in prose:
        m = re.match(r"^##\s+(.*)", l)
        if m:
            cur = [m.group(1), []]
            secs.append(cur)
        elif cur is not None:
            cur[1].append(l)
    return [(h, b) for h, b in secs], unclosed


def fences_after_heading(lines, keyword):
    """keyword を含む可視見出しの直後から次の h2 までにある全フェンスの中身を返す。

    コメントアウトされたフェンス・見出しは候補にしない。
    """
    vlines = visible_lines_keep_fences(lines)
    in_scope, in_fence, buf, fences = False, False, [], []
    for l in vlines:
        if re.match(r"^#{1,3}\s", l) and not in_fence:
            if in_scope and re.match(r"^##\s", l):
                break
            in_scope = in_scope or (keyword in l)
            continue
        if not in_scope:
            continue
        if l.lstrip().startswith("```"):
            if in_fence:
                fences.append("\n".join(buf))
                buf = []
            in_fence = not in_fence
            continue
        if in_fence:
            buf.append(l)
    return fences


def fence_after_heading(lines, keyword):
    fences = fences_after_heading(lines, keyword)
    return fences[0] if fences else None


UNIVERSAL_GLOBS = {"**", "*", "**/*", "*/**"}

# ちょうど1つでなければならない正本セクション（decoy 対策。h3 に部分一致する語は入れない）
UNIQUE_HEADINGS = ("Unclear Points", "不変条件", "Hard Gate", "テスト計画", "TodoList",
                   "Definition of Done", "事前調査結果", "リスクと対策", "Resolved Points",
                   "フェーズ一覧", "変更対象", "因果分析", "解決の概念モデル", "修復モデル",
                   "論理的変更単位", "同型探索", "解決アプローチ比較")


def allowed_paths_from(line):
    """許可パスリスト行から、説明部（— 以降）を除いたパス集合を返す。"""
    left = re.split(r"\s*[—－]\s*", line)[0]
    return {p.strip() for p in re.findall(r"`([^`]+)`", left) if p.strip()}


def glob_covers(pattern, path):
    """許可パターンが対象パスを包含するか（** は任意、* はセグメント内任意）。"""
    rx = re.escape(pattern).replace(r"\*\*", "\x00").replace(r"\*", "[^/]*").replace("\x00", ".*")
    return re.fullmatch(rx, path) is not None or pattern == path


def step_gates_from(vis_text, prefix=""):
    """可視テキストの Step 本文から {ID: (Command, Expect)} を抽出する。"""
    gates = {}
    for body in re.split(r"^###\s+Step\b", vis_text, flags=re.M)[1:]:
        idm = re.match(r"\s*(\d+)\s*[:：]", body)
        if not idm:
            continue
        own = re.split(r"^##\s+", body, flags=re.M)[0]
        cm = re.search(r"Command\s*[:：]\s*`([^`]+)`", own)
        em = re.search(r"(?:Expect|期待値)\s*[:：]\s*(.+)$", own, re.M)
        gates[prefix + idm.group(1)] = (cm.group(1).strip() if cm else "",
                                        em.group(1).strip() if em else "")
    return gates


def step_primary_lcus_from(vis_text, prefix=""):
    """可視テキストの Step 本文から {Step ID: Primary LCU} を抽出する。"""
    out = {}
    for body in re.split(r"^###\s+Step\b", vis_text, flags=re.M)[1:]:
        idm = re.match(r"\s*(\d+)\s*[:：]", body)
        if not idm:
            continue
        own = re.split(r"^##\s+", body, flags=re.M)[0]
        pm = re.search(r"^\s*-\s*Primary LCU\s*[:：]\s*([^\n]+)$", own, re.M)
        if pm:
            out[prefix + idm.group(1)] = pm.group(1).strip()
    return out


def todo_gates_from(todo_vis_lines):
    """Hard Gate 表から ({ID: (Command, Expect)}, 重複ID) を抽出する。"""
    gates, dups = {}, []
    for h, rows in parse_tables(todo_vis_lines):
        if not any("Command" in c for c in h):
            continue
        sidx = next((i for i, c in enumerate(h) if "Step" in c), 0)
        cidx = next(i for i, c in enumerate(h) if "Command" in c)
        eidx = next((i for i, c in enumerate(h) if ("Expect" in c) or ("期待値" in c)), -1)
        for r in rows:
            gid = r[sidx].strip() if sidx < len(r) else ""
            if gid in gates:
                dups.append(gid)
            cmds = re.findall(r"`([^`]+)`", r[cidx]) if cidx < len(r) else []
            exp = r[eidx].strip() if 0 <= eidx < len(r) else ""
            gates[gid] = (cmds[0].strip() if cmds else "", exp)
    return gates, dups


def paren_gate_cmds(lines):
    """チェックボックス行の `（Gate: `cmd`）` からコマンド集合を抽出する（lite の Gate 正本）。"""
    cmds = set()
    for l in lines:
        if not re.match(r"^\s*- \[[ x!\-]\]", l):
            continue
        for g in re.finditer(r"[（(]Gate\s*[:：][^）)]*`([^`]+)`", l):
            cmds.add(g.group(1).strip())
    return cmds


def lint_goal(path: Path, text: str):
    errors, warnings = [], []
    lines = text.splitlines()
    secs, unclosed = goal_sections(lines)
    if unclosed:
        errors.append("コードフェンス（```）が閉じられていない")
    for kw in ("/goal", "正本参照", "事前回答", "再開手順", "実装後フロー"):
        if not any(kw in h for h, _ in secs):
            errors.append(f"goal-prompt に必須見出し（h2・フェンス外）がない: {kw}")
    for kw in ("/goal", "正本参照", "事前回答", "再開手順", "実装後フロー"):
        if sum(1 for h, _ in secs if kw in h) > 1:
            errors.append(f"{kw} 見出しが複数ある（ちょうど1つにする。古い版はコメントでなく削除する）")
    ap_lines = [l for l in clean_refs_text(text).splitlines() if "許可パスリスト" in l]
    if not ap_lines:
        errors.append("goal-prompt に許可パスリストがない")
    elif not allowed_paths_from(ap_lines[0]):
        errors.append("goal-prompt の許可パスリストに具体的なパス/glob（バッククォート付き）が1件もない")
    # プレースホルダはフェンス内も含めて全行走査（プロンプト文だから）
    report_placeholders(list(enumerate(lines, 1)), errors, strict_code=True)
    # goal はコードスパンの内部に埋まった [xxx]（`docs/[topic]/plan.md` 等）も失格
    for n, l in enumerate(lines, 1):
        for span in re.findall(r"`([^`]*)`", l):
            for m in re.finditer(r"\[([^\][]{1,60})\]", span):
                if f"[{m.group(1)}]" != span.strip() and m.group(1) not in (" ", "x", "!", "-"):
                    errors.append(f"L{n}: コードスパン内にプレースホルダが埋まっている: `{span[:40]}`")
    # preflight: 「事前回答」セクション内で各行ちょうど1件・行全体の完全一致
    pre_lines = next((body for h, body in secs if "事前回答" in h), [])
    for label, pat in (("gap-check", r"^\s*-\s*gap-check\s*回数\s*[:：]\s*(0|1|2|未回答)\s*$"),
                       ("レビューループ上限", r"^\s*-\s*レビューループ上限\s*[:：]\s*(skip|3|5|10|未回答)\s*$"),
                       ("QA 環境", r"^\s*-\s*QA\s*環境\s*[:：]\s*(local|staging|PR[ /]?preview|未回答)\s*$")):
        cand = [l for l in pre_lines if label in l and re.search(r"[:：]", l)]
        if not cand:
            errors.append(f"preflight の {label} 行が「事前回答」セクションにない（未回答の場合も「未回答」と明記する）")
        elif len(cand) > 1:
            errors.append(f"preflight の {label} 行が複数ある（ちょうど1行にする）")
        elif not re.match(pat, cand[0]):
            errors.append(f"preflight の {label} が許容値でない（行全体で完全一致させる）: {cand[0].strip()[:60]}")
    if "24時間" not in clean_refs_text(text) or "base branch" not in clean_refs_text(text):
        errors.append("鮮度チェックの2条件（24時間超 / base branch 進行）が可視テキストに揃っていない")
    # /goal 見出し配下の可視フェンスを検査（先頭 decoy・コメント内の旧フェンスでは代替できない）
    goal_fences = fences_after_heading(lines, "/goal")
    if len(goal_fences) > 1:
        errors.append(f"/goal 見出し配下にフェンスが{len(goal_fences)}個ある（貼り付け用はちょうど1つにする）")
    f = goal_fences[0] if goal_fences else None
    if f is None:
        errors.append("/goal 見出し配下に貼り付け用フェンスがない")
    else:
        for kw, label in (("codex-impl", "$codex-impl の使用指示"), ("完了条件", "完了条件"), ("逸脱", "逸脱の扱い"),
                          ("作業の正", "作業の正（計画書パス）"), ("進捗台帳", "進捗台帳（todo パス）")):
            if kw not in f:
                errors.append(f"/goal 貼り付け用フェンスに{label}がない")
    return errors, warnings


def lint_plan(path: Path, text: str, require_detailed=False):
    errors, warnings = [], []
    lines = text.splitlines()
    prose, unclosed_fence = strip_code_blocks(lines)
    prose_lines = [l for _, l in prose]
    head_text = "\n".join(lines[:5])
    m = MODE_RE.search(head_text)
    if not m:
        errors.append("モード宣言がない: 冒頭5行以内に <!-- plan-mode: full|lite|scaled-overview|scaled-phase --> が必要"
                      "（plan-mode: scaled という宣言は存在しない）")
        return errors, warnings
    mode = m.group(1)
    schema_match = SCHEMA_RE.search(head_text)
    schema = schema_match.group(1) if schema_match else None
    any_schema = ANY_SCHEMA_RE.search(head_text)
    if any_schema and schema is None:
        errors.append(f"未知の plan-schema: {any_schema.group(1)}")
    if schema == "causal-v1" and mode in ("full", "lite", "scaled-overview"):
        header_block = re.split(r"^##\s", clean_refs_text(text), maxsplit=1, flags=re.M)[0]
        input_types = INPUT_TYPE_RE.findall(header_block)
        if len(input_types) != 1:
            errors.append(
                "causal-v1 はヘッダに「- 入力タイプ: corrective-single / corrective-bundle(N) / feature-gap」"
                f"をちょうど1行書く（現在{len(input_types)}行）"
            )
        elif mode == "lite" and input_types[0].startswith("corrective-bundle"):
            errors.append("corrective-bundle は入力→Finding→LCU の被覆が必要なため lite 禁止（full にする）")

    if mode == "scaled-phase":
        head = prose_lines[:10]
        if not any(STATE_RE.search(l) for l in head):
            if any(STATE_RE.search(l) for l in lines[10:]):
                errors.append("「- 状態: detailed / outline」行は冒頭10行以内に置く（現在は11行目以降にある）")
            else:
                errors.append("scaled-phase には冒頭10行以内に「- 状態: detailed」または「- 状態: outline」行が必要")
        if any(OUTLINE_RE.search(l) for l in head):
            if require_detailed:
                errors.append("状態が outline のまま（--require-detailed: Step 分解を完了し、"
                              "冒頭の「- 状態: outline」を「- 状態: detailed」に書き換えてから再実行する）")
                return errors, warnings
            mode = "scaled-phase-outline"

    if unclosed_fence:
        errors.append("コードフェンス（```）が閉じられていない。以降の検査は不正確になる")

    report_placeholders(prose, errors)

    headings = [re.sub(r"^#{1,3}\s+", "", l) for l in prose_lines if re.match(r"^#{1,3}\s+", l)]
    required_headings = CAUSAL_REQUIRED_HEADINGS[mode] if schema == "causal-v1" else REQUIRED_HEADINGS[mode]
    for kw in required_headings:
        alts = kw if isinstance(kw, tuple) else (kw,)
        if not any(a in h for a in alts for h in headings):
            errors.append(f"必須セクションがない: {alts[0]}")
    # 正本セクションの一意性（先頭に decoy を置いて後続の実体を無視させる迂回を防ぐ）
    for kw in UNIQUE_HEADINGS:
        n = sum(1 for h in headings if kw in h)
        if n > 1:
            errors.append(f"見出し「{kw}」が{n}個ある（ちょうど1つにする。旧版はコメント化でなく削除する）")

    if mode != "scaled-phase-outline":
        if not any(re.match(r"^\s*- \[[ x!\-]\]", l) for l in prose_lines):
            errors.append("チェックボックス（- [ ]）が1つもない")

    # Step の構造化（数・番号・本文）を1回で抽出し、全検査で共有する
    step_headings = [l for l in prose_lines if re.match(r"^###\s+Step\b", l)]
    step_nums = []
    for h in step_headings:
        nm = re.match(r"^###\s+Step\s+(\d+)\s*[:：]", h)
        if not nm:
            errors.append(f"Step 見出しは「### Step <番号>: 名前」形式で書く: {h.strip()[:50]}")
        else:
            step_nums.append(int(nm.group(1)))
    if step_nums and step_nums != list(range(1, len(step_nums) + 1)):
        errors.append(f"Step 番号は文書順に 1..N の連番にする（現在: {step_nums}）")
    if mode == "scaled-phase-outline":
        if step_headings:
            errors.append("outline phase に Step 見出しを置けない。Step と Hard Gate を確定したら状態を detailed にする")
        detailed_only = ("設計判断", "実装ステップ", "テスト計画", "リスクと対策", "フェーズ Todo")
        copied = [kw for kw in detailed_only if any(kw in h for h in headings)]
        if copied:
            errors.append(f"outline phase に detailed 専用セクションを置けない: {copied}")
        if gate_targets(prose_lines)[0]:
            errors.append("outline phase に Step Hard Gate を置けない。overview の完了 Gate だけを使う")

    files_rows = [r for _h, rows in parse_tables(section_body(prose_lines, "変更対象")) for r in rows]

    if mode == "lite":
        if "lite 判定根拠" not in text:
            errors.append("lite には「lite 判定根拠」行が必要（変更ファイル数と diff 一文）")
        if not files_rows:
            errors.append("lite の変更対象表にデータ行がない")
        npaths = 0
        for r in files_rows:
            cell = r[0] if r else ""
            paths = re.findall(r"`([^`]+)`", cell)
            npaths += max(1, len(paths))
            for p in paths or [cell]:
                if re.search(r"[*?\[\]{}]", p) or p.rstrip("`").endswith("/"):
                    errors.append(f"lite の変更対象に glob / ディレクトリ指定は不可（水増し防止）: {p[:40]}")
        if npaths > 3:
            errors.append(f"lite の変更対象が{npaths}パス（3以内。超えるなら full にする）")
        dm = re.search(r"変更\s*(\d+)\s*ファイル", text)
        if dm and int(dm.group(1)) != npaths:
            errors.append(f"lite 判定根拠の宣言（{dm.group(1)}ファイル）と変更対象表（{npaths}パス）が不一致")
        scope_lines = [l for l in section_body(prose_lines, "目的")
                       if "Out of scope" not in l and "禁止領域" not in l]
        scope_lines += [c for r in files_rows for c in r]
        scope_lines += section_body(prose_lines, "TodoList")
        fm = LITE_FORBIDDEN.search("\n".join(scope_lines))
        if fm:
            errors.append(f"lite 禁止領域に該当（{fm.group(0)}）: schema/認証/権限/決済/移行/データ削除/公開APIは full にする")
    if mode in ("full", "scaled-phase"):
        for r in files_rows:
            if r and len(re.findall(r"`[^`]+`", r[0])) > 1:
                errors.append(f"変更対象表の1行に複数パスを詰め込まない（1行=1パス）: {r[0][:50]}")
    if mode == "full":
        todo_boxes = len([l for l in section_body(prose_lines, "TodoList") if re.match(r"^\s*- \[[ x!\-]\]", l)])
        npaths = sum(len(re.findall(r"`[^`]+`", r[0])) or 1 for r in files_rows if r)
        if len(step_headings) >= SCALED_STEP_MIN:
            errors.append(f"Step が {len(step_headings)} 個（{SCALED_STEP_MIN}以上は scaled モードにする）")
        if todo_boxes >= SCALED_TODO_MIN:
            errors.append(f"TodoList が {todo_boxes} 件（{SCALED_TODO_MIN}件以上は scaled モードにする）")
        if npaths >= SCALED_FILES_MIN:
            errors.append(f"変更対象が {npaths} パス（{SCALED_FILES_MIN}件以上は scaled モードにする）")
    if len(step_nums) != len(set(step_nums)):
        errors.append("Step 番号が重複している")

    if mode in ("full", "scaled-phase"):
        step_bodies = re.split(r"^###\s+Step\b", "\n".join(prose_lines), flags=re.M)[1:]
        if not step_bodies:
            errors.append("### Step 見出しが1つもない")
        for i, sb in enumerate(step_bodies, 1):
            sb_own = re.split(r"^##\s+", sb, flags=re.M)[0]
            for label in ("変更ファイル", "実装内容", "完了条件", "実行コマンド"):
                if label not in sb_own:
                    errors.append(f"Step {i} に「{label}」がない（5点セット）")
            dod_m = re.search(r"完了条件[^\n]*", sb_own)
            if dod_m and "`" not in dod_m.group(0):
                errors.append(f"Step {i} の完了条件に実行コマンド（バッククォート付き）がない")
            if "Hard Gate" not in sb_own:
                errors.append(f"Step {i} に Hard Gate がない")
                continue
            if not re.search(r"Command\s*[:：]\s*\S", sb_own):
                errors.append(f"Step {i} の Hard Gate に Command がない")
            if not re.search(r"(Expect|期待値)\s*[:：]\s*\S", sb_own):
                errors.append(f"Step {i} の Hard Gate に Expect がない")

    if mode in ("full", "scaled-phase", "scaled-overview"):
        cmds, _ = gate_targets(prose_lines)
        if not cmds:
            errors.append("Hard Gate の Command が見つからない"
                          "（`- Command: `cmd`` 行、または Command 列を持つ表で書く。コードフェンス内は検出対象外）")
    if mode != "scaled-phase-outline":
        ge, gw = scan_gates(prose_lines)
        errors.extend(ge)
        warnings.extend(gw)
        pe, pw = scan_proscons(prose_lines)
        errors.extend(pe)
        warnings.extend(pw)

    if mode == "full" and schema != "causal-v1":
        mm_errors, _ = validate_mental_model(parse_tables(section_body(prose_lines, "メンタルモデル")))
        errors.extend(mm_errors)

    if schema == "causal-v1" and mode == "lite":
        errors.extend(validate_lite_causal(prose_lines))

    design_hub = False
    if schema == "causal-v1" and mode in ("scaled-phase", "scaled-phase-outline"):
        hub_lines = [l for l in prose_lines[:12] if re.match(r"^\s*-\s*設計ハブ\s*[:：]", l)]
        if len(hub_lines) != 1:
            errors.append(f"causal-v1 の scaled-phase は冒頭12行以内に「- 設計ハブ: true/false」をちょうど1行書く（現在{len(hub_lines)}行）")
        else:
            hub_match = HUB_RE.fullmatch(hub_lines[0])
            if hub_match:
                design_hub = hub_match.group(1).lower() == "true"
            else:
                errors.append("設計ハブは true / false と完全一致で書く")
        if mode == "scaled-phase-outline" and design_hub:
            errors.append("outline phase は設計ハブにできない")
        if not design_hub:
            master_lines = [l for l in prose_lines[:12] if re.search(r"診断・モデル正本\s*[:：]", l)]
            if len(master_lines) != 1:
                errors.append("設計ハブでない causal-v1 phase は冒頭12行以内に「- 診断・モデル正本: `path`」を1行書く")

    if schema == "causal-v1" and (mode == "full" or (mode == "scaled-phase" and design_hub)):
        ce, cw, _analysis = validate_causal_contract(prose_lines, mode)
        errors.extend(ce)
        warnings.extend(cw)

    if mode in ("full", "scaled-phase") and schema != "causal-v1":
        dj_tables = parse_tables(section_body(prose_lines, "設計判断"))
        # 負の除外条件では Finding 等の新しい表が判断表を代用できる。正規列で選ぶ。
        dj_tables = [
            (h, r) for h, r in dj_tables
            if _column(h, "判断項目") >= 0 and _column(h, "採用") >= 0
            and _column(h, "非採用案", "不採用案", "代替案") >= 0 and _column(h, "理由") >= 0
        ]
        if not any(rows for _, rows in dj_tables):
            errors.append("設計判断セクションに表のデータ行がない")
        else:
            found_alt = False
            for header, rows in dj_tables:
                col = next((i for i, c in enumerate(header) if any(k in c for k in ALT_COL_KEYS)), -1)
                if col < 0:
                    continue
                found_alt = True
                for row in rows:
                    cell = row[col].strip() if col < len(row) else ""
                    if not cell or DESIGN_TWICE_BANNED.match(cell):
                        errors.append(f"設計判断の非採用案が空または「{cell}」（Design It Twice: 重要判断は実案を2つ出してから選ぶ）")
            if not found_alt:
                warnings.append("設計判断表に非採用案の列がない（Design It Twice を検査できない）")

    if mode in ("full", "scaled-phase"):
        risk_tables = parse_tables(section_body(prose_lines, "リスクと対策"))
        if not any(rows for _, rows in risk_tables):
            errors.append("リスクと対策セクションに表のデータ行がない（対策は Step か Todo に実体を持たせる）")
        else:
            found_col = False
            snums = set(step_nums)
            for header, rows in risk_tables:
                col = next((i for i, c in enumerate(header) if any(k in c for k in RISK_COL_KEYS)), -1)
                if col < 0:
                    continue
                found_col = True
                for row in rows:
                    if col >= len(row) or not row[col]:
                        errors.append("リスクと対策の対応列が空（対策は Step か Todo に実体を持たせる）")
                    else:
                        for sn in re.findall(r"Step\s*(\d+)", row[col]):
                            if int(sn) not in snums:
                                errors.append(f"リスク対応が存在しない Step {sn} を参照している")
            if not found_col:
                warnings.append("リスクと対策の表に「対応する Step / Todo」列がない")

    ub = section_body(prose_lines, "Unclear Points")
    body_nonempty = [l.strip() for l in ub if l.strip()]
    utext = "\n".join(ub)
    is_none_decl = bool(body_nonempty) and bool(re.match(r"^[`\-\s]*(Unclear Points\s*[:：]\s*)?None\b", body_nonempty[0]))
    has_five = all(kw in utext for kw in ("Point", "Why", "Impact", "Need", "Fallback"))
    if is_none_decl and re.search(r"Point\s*[:：]", utext):
        errors.append("Unclear Points に None 宣言と Point が同居している（None を消し忘れたまま Point を追記しない。XOR にする）")
    if is_none_decl and not has_five:
        if "除外した論点" not in utext:
            errors.append("Unclear Points: None には「実装外として除外した論点」の明記が必要")
    elif body_nonempty:
        for kw in ("Point", "Why", "Impact", "Need", "Fallback", "推奨"):
            if kw not in utext:
                errors.append(f"Unclear Points に {kw} がない（Point/Why/Impact/Need/Fallback/推奨 の形式を守る）")

    if mode not in ("scaled-phase", "scaled-phase-outline"):
        inv = section_body(prose_lines, "不変条件")
        if not any(l.strip().startswith(("-", "|")) for l in inv):
            errors.append("不変条件・逸脱禁止セクションに項目がない")
        ap_lines = [l for l in inv if "許可パスリスト" in l]
        if len(ap_lines) != 1:
            errors.append("不変条件に「許可パスリスト」行がちょうど1行必要（スコープ照合の正）")
        else:
            paths = allowed_paths_from(ap_lines[0])
            if not paths:
                errors.append("許可パスリストに具体的なパス/glob（バッククォート付き）が1件もない")
            elif any(p.lstrip("./") in UNIVERSAL_GLOBS for p in paths):
                errors.append("許可パスリストに全許可 glob（**/* 等）が含まれている（混在も不可。スコープ制御になっていない）")

    dod = section_body(prose_lines, "Definition of Done") + section_body(prose_lines, "完了条件")
    for l in dod:
        if SELF_REF_DOD.search(l):
            errors.append(f"自己言及 DoD（文書の完成は完了条件にならない）: {l.strip()[:80]}")
    if mode in ("full", "lite", "scaled-overview"):
        if not any("`" in l for l in dod):
            errors.append("Definition of Done に実行コマンド系の条件（バッククォート付き）が1つもない")

    if mode in ("full", "scaled-phase"):
        tp_lines = section_body(prose_lines, "テスト計画")
        tp = "\n".join(tp_lines)
        if not tp.strip():
            errors.append("テスト計画セクションが空")
        elif "実環境" not in tp:
            errors.append("テスト計画に実環境動作確認がない（テストが通ることと動くことは別）")
        else:
            env_lines = [l for l in tp_lines if "実環境" in l]
            if not any("`" in l and not re.search(r"[:：]\s*(なし|不要|N/?A)\s*$", l) for l in env_lines):
                errors.append("実環境動作確認に実体がない（「なし/不要」は不可。バッククォート付きコマンドか具体的な確認手順を書く）")

    if mode == "full":
        if "```mermaid" not in text and "図の省略理由" not in text:
            errors.append("mermaid ブロックのインライン掲載か「図の省略理由:」のどちらかが必要")

    if mode == "scaled-overview":
        if "scaled 判定根拠" not in text:
            errors.append("scaled には「scaled 判定根拠」行が必要")
        if len(lines) > OVERVIEW_FAIL_LINES:
            errors.append(f"overview が {len(lines)} 行（{OVERVIEW_FAIL_LINES} 行超で失格。詳細はフェーズファイルへ）")
        elif len(lines) > OVERVIEW_WARN_LINES:
            warnings.append(f"overview が {len(lines)} 行（{OVERVIEW_WARN_LINES} 行以内推奨）")

    long_lines = [(n, l) for n, l in prose if not l.strip().startswith("|") and len(l) > LONG_LINE_LIMIT]
    if len(long_lines) >= LONG_LINE_FAIL:
        errors.append(f"{LONG_LINE_LIMIT}字超の行が{len(long_lines)}本（{LONG_LINE_FAIL}本以上で失格。表かサブリストに分解する）")
    elif long_lines:
        warnings.append(f"{LONG_LINE_LIMIT}字超の行が{len(long_lines)}本: L" + ", L".join(str(n) for n, _ in long_lines))

    return errors, warnings


def lint_file(path: Path, require_detailed=False, role=None):
    """role を明示しない場合はファイル名で振り分ける（plan / todo / goal）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return [f"読み込み失敗: {e}"], []
    if role is None:
        if path.name.endswith("-todo.md") or path.name == "todo-template.md":
            role = "todo"
        elif path.name.endswith("-goal-prompt.md") or path.name == "goal-prompt-template.md":
            role = "goal"
        else:
            role = "plan"
    if role == "todo":
        return lint_todo(path, text)
    if role == "goal":
        return lint_goal(path, text)
    return lint_plan(path, text, require_detailed=require_detailed)


def lint_bundle(paths):
    errors = []
    todos = [p for p in paths if p.name.endswith("-todo.md")]
    plans = [p for p in paths if not p.name.endswith("-todo.md")
             and not p.name.endswith("-goal-prompt.md") and p.suffix == ".md"]
    if not todos or not plans:
        return errors
    if len(todos) > 1:
        errors.append(f"todo が複数渡された（{', '.join(p.name for p in todos)}）: バンドル検査は1計画バンドルずつ実行する")
        return errors
    todo = todos[0]
    try:
        todo_text = todo.read_text(encoding="utf-8")
        plan_texts = {p: p.read_text(encoding="utf-8") for p in plans}
    except (OSError, UnicodeDecodeError):
        return errors
    if not any(p.name in todo_text for p in plans):
        errors.append(f"todo.md の「正本計画書」が渡された計画書 ({', '.join(p.name for p in plans)}) を参照していない")
    if not any(todo.name in t for t in plan_texts.values()):
        errors.append(f"計画書の「進捗台帳」が {todo.name} を参照していない")
    return errors


# ---------- finalize（納品トランザクション） ----------

def resolve_ref(plan: Path, val):
    """参照解決: 絶対パス / 単一名は plan と同じディレクトリ / 相対パスは plan 基点 → repo 基点。
    basename フォールバックと cwd 相対は行わない（別ファイルへの誤解決を防ぐ）。"""
    if not val:
        return None
    p = Path(val)
    if p.is_absolute():
        return p if p.is_file() else None
    cands = [plan.parent / p]
    root = repo_root(plan.parent)
    if len(p.parts) > 1 and root:
        cands.append(root / p)
    existing = {c.resolve(): c for c in cands if c.is_file()}
    if len(existing) > 1:
        return "AMBIGUOUS"
    return next(iter(existing.values()), None)


def _stale_html_note(plan: Path, text: str):
    html = plan.with_suffix(".html")
    if html.is_file():
        hm = re.search(r"source hash: ([0-9a-f]{12})", html.read_text(encoding="utf-8", errors="replace"))
        cur = compute_source_hash(plan, text)
        if hm and hm.group(1) != cur:
            print(f"⚠ STALE HTML: {html.name} は古い MD から生成されたまま（finalize が PASS するまで更新されない）")


def finalize(plan: Path):
    failed = False

    def fail(msg):
        nonlocal failed
        print(f"FAIL: bundle\n  ✗ {msg}")
        failed = True

    def run(path, role):
        nonlocal failed
        errors, warnings = lint_file(path, role=role)
        print(("FAIL: " if errors else "PASS: ") + f"{path}（{role}）")
        for e in errors:
            print(f"  ✗ {e}")
        for w in warnings:
            print(f"  ⚠ {w}")
        if errors:
            failed = True

    try:
        text = plan.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"FAIL: {plan}\n  ✗ 読み込み失敗: {e}")
        return 1
    m = MODE_RE.search("\n".join(text.splitlines()[:5]))
    if not m or m.group(1) not in ("full", "lite", "scaled-overview"):
        print(f"FAIL: {plan}\n  ✗ --finalize は full / lite / scaled-overview にのみ使用できる"
              "（scaled-phase 単体は overview 経由で検査される）")
        return 1
    mode = m.group(1)
    schema = "causal-v1" if SCHEMA_RE.search("\n".join(text.splitlines()[:5])) else None
    run(plan, "plan")

    def get_ref(src_path, src_text, label, suffix, required, scope="header"):
        val, count = extract_ref(src_text, label, scope=scope)
        if count > 1:
            fail(f"{src_path.name} の「{label}」参照が複数ある（ちょうど1行にする）")
        if val is None:
            if required:
                fail(f"{src_path.name} に {label} の参照がない")
            return None
        if suffix and not val.endswith(suffix):
            fail(f"{label} の参照先は {suffix} で終わるファイルにする: {val}")
            return None
        p = resolve_ref(src_path, val)
        if p == "AMBIGUOUS":
            fail(f"{label} の参照が曖昧（相対の解決先が複数実在）: {val}")
            return None
        if p is None:
            fail(f"{label} の参照先が見つからない: {val}")
            return None
        return p

    todo = get_ref(plan, text, "進捗台帳", "-todo.md", True)
    goal = get_ref(plan, text, "goal prompt", "-goal-prompt.md", mode in ("full", "scaled-overview"))
    if todo is not None:
        run(todo, "todo")
    if goal is not None:
        run(goal, "goal")

    # 相互参照: 本文中の文字列出現でなく、各ファイルの正規形参照を解決して実体比較する
    def same(a, b):
        return a is not None and b is not None and a.resolve() == b.resolve()

    todo_text = todo.read_text(encoding="utf-8") if todo else ""
    goal_text = goal.read_text(encoding="utf-8") if goal else ""
    analysis_master = None
    if schema == "causal-v1":
        forbidden_analysis_headings = (
            "因果分析", "解決の概念モデル", "修復モデル", "論理的変更単位",
            "同型探索", "解決アプローチ比較",
        )
        secondary_docs = []
        if todo is not None:
            secondary_docs.append((todo.name, todo_text))
        if goal is not None:
            secondary_docs.append((goal.name, goal_text))
        if mode == "scaled-overview":
            secondary_docs.append((plan.name, text))
        for doc_name, raw in secondary_docs:
            visible = clean_refs_text(raw)
            headings = [
                re.sub(r"^#{1,6}\s+", "", line)
                for line in visible.splitlines() if re.match(r"^#{1,6}\s+", line)
            ]
            copied = [kw for kw in forbidden_analysis_headings if any(kw in h for h in headings)]
            blocks = re.findall(
                r"^#{3,6}\s+(Cause|Model|Repair|Variant|LCU Split)\s+[A-Z]+-?\d+\b",
                visible,
                re.M,
            )
            if copied or blocks:
                fail(f"{doc_name} に診断・モデル正本の分析本文を複写できない: {sorted(set(copied + blocks))}")
        analysis_master = get_ref(plan, text, "診断・モデル正本", "", True)
        if mode in ("full", "lite") and analysis_master is not None and not same(analysis_master, plan):
            fail(f"{plan.name} の診断・モデル正本は計画書自身を指す必要がある")
        if todo is not None:
            tm = get_ref(todo, todo_text, "診断・モデル正本", "", True)
            if tm is not None and analysis_master is not None and not same(tm, analysis_master):
                fail(f"todo.md の診断・モデル正本（{tm.name}）が plan の正本と不一致")
        if goal is not None:
            gm = get_ref(goal, goal_text, "診断・モデル正本", "", True, scope="prose")
            if gm is not None and analysis_master is not None and not same(gm, analysis_master):
                fail(f"goal-prompt の診断・モデル正本（{gm.name}）が plan の正本と不一致")
    if todo is not None:
        back = get_ref(todo, todo_text, "正本計画書", "", True)
        if back is not None and not same(back, plan):
            fail(f"todo.md の正本計画書が別ファイルを指している: {back.name}")
        if goal is not None:
            tg = get_ref(todo, todo_text, "goal prompt", "-goal-prompt.md", True)
            if tg is not None and not same(tg, goal):
                fail(f"todo.md の goal prompt が別ファイルを指している: {tg.name}")
    if goal is not None:
        gp = get_ref(goal, goal_text, "計画書", "", True, scope="prose")
        if gp is not None and not same(gp, plan):
            fail(f"goal-prompt の計画書参照が別ファイルを指している: {gp.name}")
        gt = get_ref(goal, goal_text, "進捗台帳", "-todo.md", True, scope="prose")
        if gt is not None and todo is not None and not same(gt, todo):
            fail(f"goal-prompt の進捗台帳参照が別ファイルを指している: {gt.name}")
        # 実行時に貼り付けられるのは /goal フェンスの中身。正本参照ブロックと独立に照合する
        gf = fence_after_heading(goal_text.splitlines(), "/goal")
        if gf is not None:
            def fence_ref(label, target):
                ms = re.findall(rf"^-\s*{label}\s*[:：]\s*`?([^`\n（）\s]+)", gf, re.M)
                if len(ms) != 1:
                    fail(f"/goal フェンスの「{label}」行はちょうど1行必要（現在{len(ms)}行）")
                    return
                p = resolve_ref(goal, ms[0].strip())
                if p is None or p == "AMBIGUOUS" or not same(p, target):
                    fail(f"/goal フェンスの{label}（{ms[0]}）が正本（{target.name}）と一致しない"
                         "（実装エージェントの入口が別バンドルに向く）")
            fence_ref("作業の正", plan)
            if todo is not None:
                fence_ref("進捗台帳", todo)

    # 許可パスの集合一致（plan ↔ goal）: 正本セクションからだけ読む（本文の decoy 行は使わない）
    plan_vis = clean_refs_text(text)
    plan_vis_lines = plan_vis.splitlines()
    plan_ap = next((l for l in section_body(plan_vis_lines, "不変条件") if "許可パスリスト" in l), None)
    allowed = allowed_paths_from(plan_ap) if plan_ap else set()
    if goal is not None:
        gsecs, _ = goal_sections(goal_text.splitlines())
        gref_lines = next((body for h, body in gsecs if "正本参照" in h), [])
        goal_ap = next((l for l in gref_lines if "許可パスリスト" in l), None)
        if plan_ap and goal_ap:
            ps, gs = allowed_paths_from(plan_ap), allowed_paths_from(goal_ap)
            if ps and gs and ps != gs:
                fail(f"許可パスリストが plan と goal で不一致: plan={sorted(ps)} goal={sorted(gs)}")

    def check_cover(paths, src):
        for t in paths:
            if t.endswith(".md"):
                continue  # 計画成果物（次フェーズファイル等）は照合対象外
            if t.startswith("/") or "\\" in t or ".." in t.split("/"):
                fail(f"変更対象 `{t}` は不正なパス（絶対パス・.. ・バックスラッシュは不可。リポジトリ相対の正規形で書く）")
                continue
            if allowed and not any(glob_covers(a, t) for a in allowed):
                fail(f"変更対象 `{t}` が許可パスリストに包含されていない（{src}。スコープ境界が実装対象を許可していない）")

    def step_file_targets(vis_text):
        return [p for m in re.finditer(r"変更ファイル\s*[:：]([^\n]*)", vis_text)
                for p in re.findall(r"`([^`]+)`", m.group(1))]

    if mode in ("full", "lite"):
        targets = [p for _h, rows in parse_tables(section_body(plan_vis_lines, "変更対象"))
                   for r in rows if r for p in re.findall(r"`([^`]+)`", r[0])]
        targets += step_file_targets(plan_vis)
        check_cover(targets, plan.name)

    plan_gates = {}
    plan_step_lcus = {}
    if mode == "full":
        plan_gates = step_gates_from(plan_vis)
        if schema == "causal-v1":
            plan_step_lcus = step_primary_lcus_from(plan_vis)
        # §Hard Gate 一覧と Step 詳細の照合（片方だけの更新漏れ・表の削除・重複 ID を検出）
        g6, g6_dups = todo_gates_from(section_body(plan_vis_lines, "Hard Gate"))
        if g6_dups:
            fail(f"§Hard Gate 一覧に同じ ID の行が複数ある: {g6_dups}")
        if not g6:
            fail("§Hard Gate 一覧に表のデータ行がない（Step 詳細との照合ができない）")
        elif set(g6) != set(plan_gates):
            fail(f"§Hard Gate 一覧の Step 集合（{sorted(g6)}）が Step 詳細（{sorted(plan_gates)}）と不一致")
        else:
            for gid, (c6, e6) in g6.items():
                c, e = plan_gates[gid]
                if c6 != c:
                    fail(f"§Hard Gate 一覧の Step {gid} Command が Step 詳細と不一致: 一覧=`{c6}` 詳細=`{c}`")
                if " ".join(e6.split()) != " ".join(e.split()):
                    fail(f"§Hard Gate 一覧の Step {gid} Expect が Step 詳細と不一致: 一覧「{e6}」 詳細「{e}」")

    if mode == "scaled-overview":
        prose, _ = strip_code_blocks(text.splitlines())
        prose_lines = [l for _, l in prose]
        capsule_line = next((l for l in prose_lines if "採用メンタルモデル" in l), None)
        purpose_lines = section_body(prose_lines, "目的")
        model_lines = re.findall(
            r"^[ \t]*-[ \t]*解決モデル[ \t]*[:：][ \t]*([^\n]*)$", "\n".join(purpose_lines), re.M
        )
        model_line = model_lines[0].strip() if len(model_lines) == 1 else None
        if schema == "causal-v1":
            if len(model_lines) != 1:
                fail(f"causal-v1 overview の目的に「- 解決モデル: ...」がちょうど1行必要（現在{len(model_lines)}行）")
            elif len(model_line) < 20 or EMPTY_CONTRACT.match(model_line):
                fail("causal-v1 overview の解決モデルが空または抽象的すぎる")
        elif capsule_line is None:
            fail("overview に「採用メンタルモデル」カプセル行がない（採用モデル名と正準語彙を1行で書く）")
        phases = []  # (num, declared_state, path)
        seen_resolved = set()
        for header, rows in parse_tables(section_body(prose_lines, "フェーズ一覧")):
            fidx = next((i for i, c in enumerate(header) if "ファイル" in c), -1)
            sidx = next((i for i, c in enumerate(header) if "状態" in c), -1)
            nidx = next((i for i, c in enumerate(header) if c.strip() == "#"), -1)
            gidx = next((i for i, c in enumerate(header) if "Gate" in c), -1)
            if fidx < 0:
                continue
            if nidx < 0:
                fail("フェーズ一覧に # 列がない")
            if gidx < 0:
                fail("フェーズ一覧に完了 Gate 列がない")
            for row in rows:
                if len(row) != len(header):
                    fail(f"フェーズ一覧の行のセル数がヘッダと不一致: | {' | '.join(row)[:60]}")
                    continue
                if gidx >= 0:
                    gcell = row[gidx].strip()
                    if "`" not in gcell:
                        fail(f"フェーズ一覧の完了 Gate が空またはバッククォートなし（outline 段階の唯一の完了契約）: 行 {row[nidx] if nidx >= 0 else '?'}")
                num_cell = row[nidx].strip() if nidx >= 0 else ""
                if not num_cell.isdigit():
                    fail(f"フェーズ番号は正の整数で書く: 「{num_cell}」")
                    continue
                num = int(num_cell)
                declared_state = row[sidx].strip() if sidx >= 0 else ""
                if declared_state not in ("detailed", "outline"):
                    fail(f"フェーズ一覧の状態は detailed / outline と完全一致で書く: 「{declared_state}」")
                name = row[fidx].strip().strip("`")
                p = resolve_ref(plan, name)
                if p is None or p == "AMBIGUOUS":
                    fail(f"フェーズファイルが見つからない/曖昧: {name}")
                    continue
                rp = p.resolve()
                if rp in seen_resolved:
                    fail(f"フェーズ一覧が同じファイルを複数回参照している: {name}")
                    continue
                seen_resolved.add(rp)
                phases.append((num, declared_state, p))
        nums = [n for n, _, _ in phases]
        if nums != list(range(1, len(nums) + 1)):
            fail(f"フェーズ番号は 1..N の連番にする（現在: {nums}）")
        state_seq = [s for _, s, _ in phases]
        if "outline" in state_seq and "detailed" in state_seq[state_seq.index("outline"):]:
            fail("outline の後に detailed フェーズがある（詳細化は先頭から順に行う）")
        first_detailed = None
        todo_vis = "\n".join(l for _, l in strip_code_blocks(todo_text.splitlines())[0]) if todo else ""
        todo_gate_rows = [" | ".join(r) for h, rows in parse_tables(todo_vis.splitlines())
                          if any("Command" in c for c in h) for r in rows]
        for i, (num, declared_state, p) in enumerate(phases):
            ptext = p.read_text(encoding="utf-8")
            pvisible_lines = [l for _, l in strip_code_blocks(ptext.splitlines())[0]]
            head = pvisible_lines[:10]
            pm = MODE_RE.search("\n".join(ptext.splitlines()[:5]))
            if not pm or pm.group(1) != "scaled-phase":
                fail(f"{p.name} は plan-mode: scaled-phase でない")
            phase_schema = bool(SCHEMA_RE.search("\n".join(ptext.splitlines()[:5])))
            if schema == "causal-v1" and not phase_schema:
                fail(f"{p.name} に plan-schema: causal-v1 がない（overview と schema が不一致）")
            if schema != "causal-v1" and phase_schema:
                fail(f"{p.name} だけが causal-v1（overview と schema が不一致）")
            states_in_head = {s for l in head for s in STATE_RE.findall(l)}
            if len(states_in_head) > 1:
                fail(f"{p.name} の冒頭に detailed と outline の両方の状態行がある")
            actual = "outline" if any(OUTLINE_RE.search(l) for l in head) else "detailed"
            if declared_state in ("detailed", "outline") and actual != declared_state:
                fail(f"{p.name} の状態（{actual}）が overview の宣言（{declared_state}）と不一致")
            pvis = "\n".join(pvisible_lines)
            h1s = re.findall(r"^#\s+Phase\s+(\d+)\s*[:：]", pvis, re.M)
            if len(h1s) != 1:
                fail(f"{p.name} に「# Phase N:」見出し（コロン付き・可視）がちょうど1つ必要（現在{len(h1s)}個）")
            elif int(h1s[0]) != num:
                fail(f"{p.name} の見出し（Phase {h1s[0]}）が overview の番号（{num}）と不一致")
            oref, ocount = extract_ref(ptext, "Overview", scope="header")
            if ocount != 1 or oref is None:
                fail(f"{p.name} の Overview 参照はちょうど1行必要")
            else:
                op = resolve_ref(p, oref)
                if op is None or op == "AMBIGUOUS" or not same(op, plan):
                    fail(f"{p.name} の Overview 参照（{oref}）が finalize 対象の overview と一致しない")
            if actual == "detailed":
                if first_detailed is None:
                    first_detailed = p
                plan_gates.update(step_gates_from(pvis, prefix=f"P{num}-S"))
                if schema == "causal-v1":
                    plan_step_lcus.update(step_primary_lcus_from(pvis, prefix=f"P{num}-S"))
                check_cover([t for _h, rows in parse_tables(section_body(pvis.splitlines(), "変更対象"))
                             for r in rows if r for t in re.findall(r"`([^`]+)`", r[0])]
                            + step_file_targets(pvis), p.name)
                if i + 1 < len(phases):
                    nxt = phases[i + 1][2].name
                    # 語句の存在でなく、Step の Hard Gate Command として構造化されていることを要求
                    sbodies = re.split(r"^###\s+Step\b", pvis, flags=re.M)[1:]
                    chain_ok = any(
                        re.search(rf"Command\s*[:：][^\n]*`[^`\n]*--require-detailed[^`\n]*{re.escape(nxt)}[^`\n]*`", sb)
                        for sb in sbodies)
                    if not chain_ok:
                        fail(f"{p.name} に次フェーズ（{nxt}）を対象とする --require-detailed の Hard Gate Step がない")
                    if todo is not None and not any(
                            "--require-detailed" in r and nxt in r for r in todo_gate_rows):
                        fail(f"todo.md の Hard Gate 表に {nxt} の詳細化 Gate 行がない")
            else:
                if todo is not None and not re.search(
                    rf"^\s*- \[[ x!\-]\]\s*Phase {num}(?:\s+\([^)]+\))?\s*[:：]", todo_vis, re.M
                ):
                    fail(f"todo.md に outline フェーズのチェックボックス行（「- [ ] Phase {num}: ...」形式）がない"
                         "（前フェーズの詳細化タスクでは代用できない）")
            run(p, "plan")
        if first_detailed is None:
            fail("detailed フェーズが1つもない（着手可能なフェーズがない）")
        elif schema == "causal-v1":
            master = analysis_master
            if master is not None and not same(master, first_detailed):
                fail(f"overview の診断・モデル正本（{master.name}）が最初の detailed phase（{first_detailed.name}）と一致しない")
            first_text = first_detailed.read_text(encoding="utf-8")
            overview_input = INPUT_TYPE_RE.findall(re.split(r"^##\s", clean_refs_text(text), maxsplit=1, flags=re.M)[0])
            phase_input = INPUT_TYPE_RE.findall(re.split(r"^##\s", clean_refs_text(first_text), maxsplit=1, flags=re.M)[0])
            if len(phase_input) != 1 or phase_input != overview_input:
                fail("最初の detailed phase の入力タイプが overview と一致しない")
            first_visible_lines = [l for _, l in strip_code_blocks(first_text.splitlines())[0]]
            hub_lines = [l for l in first_visible_lines[:12] if re.match(r"^\s*-\s*設計ハブ\s*[:：]", l)]
            if len(hub_lines) != 1 or not HUB_RE.fullmatch(hub_lines[0]) or HUB_RE.fullmatch(hub_lines[0]).group(1).lower() != "true":
                fail(f"最初の detailed phase {first_detailed.name} は「設計ハブ: true」でなければならない")
            fprose, _ = strip_code_blocks(first_text.splitlines())
            ce, cw, analysis = validate_causal_contract(
                [l for _, l in fprose], "scaled-phase", known_phases=set(nums)
            )
            for e in ce:
                fail(f"{first_detailed.name} の診断・モデル正本: {e}")
            for w in cw:
                print(f"WARN: bundle\n  ⚠ {first_detailed.name} の診断・モデル正本: {w}")
            phase_states = {num: declared_state for num, declared_state, _p in phases}
            for lid, gate_id in analysis.get("split_gates", {}).items():
                targets = analysis.get("lcu_meta", {}).get(lid, {}).get("target_ids", set())
                last_phase = max((int(t.split()[1]) for t in targets), default=0)
                if not gate_id.startswith(f"P{last_phase}-S"):
                    fail(f"LCU Split {lid} の最終 Gate {gate_id} が最終実装先 Phase {last_phase} にない")
                # 最終 phase が outline の間は Step 自体が未詳細化である。
                # Gate ID の phase 所属だけを検査し、実在と Primary LCU の照合は detailed 化後に行う。
                if phase_states.get(last_phase) != "detailed":
                    continue
                if gate_id not in plan_gates:
                    fail(f"LCU Split {lid} の最終 Gate {gate_id} が detailed phase に存在しない")
                    continue
                if plan_step_lcus.get(gate_id) != lid:
                    fail(f"LCU Split {lid} の最終 Gate {gate_id} が同じ Primary LCU の Step に属していない")

            count_line = next((l for l in prose_lines if re.search(r"-\s*LCU\s*[:：]", l)), None)
            count_match = re.search(r"(\d+)\s*findings?\s*[→>-]+\s*(\d+)\s*LCU", count_line or "", re.I)
            if not count_match:
                fail("overview に `- LCU: N findings → M LCU` の数値カプセルがない")
            else:
                if int(count_match.group(1)) != len(analysis["findings"]):
                    fail("overview の findings 数が診断・モデル正本の Finding 数と不一致")
                if int(count_match.group(2)) != len(analysis["lcus"]):
                    fail("overview の LCU 数が診断・モデル正本の LCU 数と不一致")

            # overview の phase 対象 LCU と、正本 LCU 表の実装先を相互照合する。
            phase_declared = {}
            for header, rows in parse_tables(section_body(prose_lines, "フェーズ一覧")):
                nidx = _column(header, "#")
                lidx = _column(header, "対象 LCU")
                if lidx < 0:
                    fail("causal-v1 のフェーズ一覧に「対象 LCU」列がない")
                    continue
                for row in rows:
                    if nidx < 0 or nidx >= len(row) or lidx >= len(row) or not row[nidx].strip().isdigit():
                        continue
                    lids, ok = _parse_ids(row[lidx], r"LCU-\d+")
                    if not ok or any(lid not in analysis["lcus"] for lid in lids):
                        fail(f"Phase {row[nidx].strip()} の対象 LCU に未知IDまたは不正書式がある")
                    phase_declared[int(row[nidx].strip())] = set(lids)
            for lid, meta in analysis.get("lcu_meta", {}).items():
                for target in meta.get("target_ids", set()):
                    pnum = int(target.split()[1])
                    if lid not in phase_declared.get(pnum, set()):
                        fail(f"LCU {lid} の実装先 Phase {pnum} が overview の対象 LCU にない")
            for pnum, lids in phase_declared.items():
                for lid in lids:
                    targets = analysis.get("lcu_meta", {}).get(lid, {}).get("target_ids", set())
                    if f"Phase {pnum}" not in targets:
                        fail(f"overview の Phase {pnum} は LCU {lid} を対象にするが、正本 LCU 表の実装先にない")

            overview_phase_tags = {}
            for line in section_body(prose_lines, "TodoList"):
                tm = re.match(
                    r"^\s*- \[[ x!\-]\]\s*Phase\s+(\d+)\s+\(([^)]+)\)\s*[:：]", line
                )
                if not tm:
                    continue
                pnum = int(tm.group(1))
                if pnum in overview_phase_tags:
                    fail(f"overview TodoList に Phase {pnum} の行が重複している")
                tagged, ok = _parse_ids(tm.group(2), r"LCU-\d+")
                overview_phase_tags[pnum] = set(tagged)
                if not ok:
                    fail(f"overview TodoList の Phase {pnum} LCUタグが不正書式")
            for pnum, expected_lcus in phase_declared.items():
                actual_lcus = overview_phase_tags.get(pnum)
                if actual_lcus != expected_lcus:
                    fail(
                        f"overview TodoList の Phase {pnum} LCUタグ（{sorted(actual_lcus or set())}）が "
                        f"フェーズ一覧（{sorted(expected_lcus)}）と不一致"
                    )
            for pnum in set(overview_phase_tags) - set(phase_declared):
                fail(f"overview TodoList に存在しない Phase {pnum} の行がある")

            # 全 phase の正本参照・対象 LCU と、detailed Step の Primary/Shared LCU を双方向照合する。
            for num, declared_state, p in phases:
                ptext = p.read_text(encoding="utf-8")
                pvis = clean_refs_text(ptext)
                phase_hubs = [l for l in pvis.splitlines()[:12] if re.match(r"^\s*-\s*設計ハブ\s*[:：]", l)]
                expected_hub = p == first_detailed
                hub_match = HUB_RE.fullmatch(phase_hubs[0]) if len(phase_hubs) == 1 else None
                hub_ok = hub_match is not None and (hub_match.group(1).lower() == "true") == expected_hub
                if not hub_ok:
                    expected = "true" if expected_hub else "false"
                    fail(f"{p.name} の設計ハブは {expected} と完全一致でなければならない")
                if not expected_hub:
                    pheadings = [
                        re.sub(r"^#{1,6}\s+", "", line)
                        for line in pvis.splitlines() if re.match(r"^#{1,6}\s+", line)
                    ]
                    forbidden = (
                        "因果分析", "解決の概念モデル", "修復モデル", "論理的変更単位",
                        "同型探索", "解決アプローチ比較",
                    )
                    copied = [kw for kw in forbidden if any(kw in h for h in pheadings)]
                    if copied:
                        fail(f"{p.name} は設計ハブでないため分析正本セクションを複写できない: {copied}")
                dref, dcount = extract_ref(ptext, "診断・モデル正本", scope="header")
                if dcount != 1 or dref is None:
                    fail(f"{p.name} の診断・モデル正本参照はちょうど1行必要")
                else:
                    dp = resolve_ref(p, dref)
                    if dp is None or dp == "AMBIGUOUS" or not same(dp, first_detailed):
                        fail(f"{p.name} の診断・モデル正本（{dref}）が最初の detailed phase と一致しない")

                target_lines = re.findall(r"^\s*-\s*対象 LCU\s*[:：]\s*([^\n]+)$", pvis, re.M)
                if len(target_lines) != 1:
                    fail(f"{p.name} の「対象 LCU」行はちょうど1行必要（現在{len(target_lines)}行）")
                    file_lcus = set()
                else:
                    parsed, ok = _parse_ids(target_lines[0], r"LCU-\d+")
                    file_lcus = set(parsed)
                    if not ok or file_lcus != phase_declared.get(num, set()):
                        fail(
                            f"{p.name} の対象 LCU（{sorted(file_lcus)}）が overview Phase {num} "
                            f"（{sorted(phase_declared.get(num, set()))}）と不一致"
                        )

                if declared_state != "detailed":
                    if todo is not None:
                        tm = re.search(
                            rf"^\s*- \[[ x!\-]\]\s*Phase {num}\s+\(([^)]+)\)\s*[:：][^\n]*",
                            todo_vis,
                            re.M,
                        )
                        if not tm:
                            fail(f"todo.md の outline Phase {num} に `(LCU-xx)` タグがない")
                        else:
                            tagged, ok = _parse_ids(tm.group(1), r"LCU-\d+")
                            if not ok or set(tagged) != phase_declared.get(num, set()):
                                fail(
                                    f"todo.md の outline Phase {num} LCUタグ（{sorted(set(tagged))}）が "
                                    f"overview（{sorted(phase_declared.get(num, set()))}）と不一致"
                                )
                    continue
                phase_primary_lcus = set()
                phase_step_primary = {}
                for i, sb in enumerate(re.split(r"^###\s+Step\b", pvis, flags=re.M)[1:], 1):
                    own = re.split(r"^##\s+", sb, flags=re.M)[0]
                    pmatches = re.findall(r"^\s*-\s*Primary LCU\s*[:：]\s*([^\n]+)$", own, re.M)
                    if len(pmatches) != 1:
                        fail(f"{p.name} Step {i} に Primary LCU がちょうど1行必要")
                        primary = None
                    else:
                        primary = pmatches[0].strip()
                        phase_step_primary[i] = primary
                        if primary == "plan-maintenance":
                            if "詳細化" not in own:
                                fail(f"{p.name} Step {i} は詳細化 Step でないため plan-maintenance を使えない")
                        elif primary not in analysis["lcus"]:
                            fail(f"{p.name} Step {i} が未知の Primary LCU {primary} を参照している")
                        else:
                            phase_primary_lcus.add(primary)
                            if primary not in phase_declared.get(num, set()):
                                fail(f"{p.name} Step {i} の Primary LCU {primary} が overview の Phase {num} 対象外")
                            targets = analysis["lcu_meta"][primary].get("target_ids", set())
                            if f"Phase {num}" not in targets:
                                fail(f"{p.name} Step {i} の Primary LCU {primary} は正本 LCU 表で Phase {num} の実装先でない")

                    smatches = re.findall(r"^\s*-\s*Shared LCU\s*[:：]\s*([^\n]+)$", own, re.M)
                    if len(smatches) > 1:
                        fail(f"{p.name} Step {i} の Shared LCU 行が複数ある")
                    elif smatches:
                        raw = smatches[0]
                        if "—" not in raw and "－" not in raw:
                            fail(f"{p.name} Step {i} の Shared LCU に共有理由がない")
                            shared, ok = [], False
                        else:
                            ids_part, reason = re.split(r"\s*[—－]\s*", raw, maxsplit=1)
                            shared, ok = _parse_ids(ids_part, r"LCU-\d+")
                            if not reason.strip():
                                fail(f"{p.name} Step {i} の Shared LCU の共有理由が空")
                        if not ok or any(lid not in analysis["lcus"] for lid in shared):
                            fail(f"{p.name} Step {i} の Shared LCU に未知IDまたは不正書式がある")
                        for lid in shared:
                            if lid == primary:
                                fail(f"{p.name} Step {i} の Primary LCU と Shared LCU が重複している")
                            if lid not in phase_declared.get(num, set()):
                                fail(f"{p.name} Step {i} の Shared LCU {lid} が overview の Phase {num} 対象外")
                            targets = analysis["lcu_meta"].get(lid, {}).get("target_ids", set())
                            if f"Phase {num}" not in targets:
                                fail(f"{p.name} Step {i} の Shared LCU {lid} は正本 LCU 表で Phase {num} の実装先でない")
                phase_todo_tags = {}
                for line in section_body(pvis.splitlines(), "フェーズ Todo"):
                    tm = re.match(
                        r"^\s*- \[[ x!\-]\]\s*Step\s+(\d+)\s+\(([^)]+)\)\s*[:：]", line
                    )
                    if not tm:
                        continue
                    step_num = int(tm.group(1))
                    if step_num in phase_todo_tags:
                        fail(f"{p.name} のフェーズ Todo に Step {step_num} が重複している")
                    phase_todo_tags[step_num] = tm.group(2).strip()
                for step_num, primary in phase_step_primary.items():
                    actual_tag = phase_todo_tags.get(step_num)
                    if actual_tag != primary:
                        fail(
                            f"{p.name} のフェーズ Todo Step {step_num} LCUタグは {primary} と完全一致させる"
                            f"（現在: {actual_tag or 'なし'}）"
                        )
                for step_num in set(phase_todo_tags) - set(phase_step_primary):
                    fail(f"{p.name} のフェーズ Todo に存在しない Step {step_num} がある")
                missing_primary = phase_declared.get(num, set()) - phase_primary_lcus
                if missing_primary:
                    fail(f"{p.name} の対象 LCU に Primary Step がない: {sorted(missing_primary)}")
        else:
            ptext = first_detailed.read_text(encoding="utf-8")
            pprose, _ = strip_code_blocks(ptext.splitlines())
            mm_errors, adopted_name = validate_mental_model(
                parse_tables(section_body([l for _, l in pprose], "メンタルモデル")))
            for e in mm_errors:
                print(f"FAIL: {first_detailed.name}\n  ✗ [最初の detailed フェーズ] {e}")
            if mm_errors:
                failed = True
            if adopted_name and capsule_line:
                cm = re.search(r"採用メンタルモデル\s*[:：]\s*([^—－（(]+)", capsule_line)
                capsule_name = unicodedata.normalize("NFKC", cm.group(1).strip()) if cm else ""
                if capsule_name != adopted_name:
                    fail(f"overview の採用メンタルモデル（{capsule_name}）とフェーズの採用列（{adopted_name}）が不一致")

    # plan ↔ todo の契約照合: Step / Gate / Unclear が両正本で一致すること
    if todo is not None:
        todo_vis = clean_refs_text(todo_text)
        tgates, tdups = todo_gates_from(todo_vis.splitlines())
        if tdups:
            fail(f"todo.md の Hard Gate 表に同じ ID の行が複数ある: {tdups}")
        if mode == "lite":
            # lite の Gate 正本は計画 TodoList の（Gate: ...）。todo 表のコマンド集合と一致させる
            plan_cmds = paren_gate_cmds(section_body(plan_vis_lines, "TodoList"))
            todo_cmds = {c for c, _e in tgates.values() if c}
            if plan_cmds != todo_cmds:
                fail(f"lite の Gate コマンドが plan と todo で不一致: plan={sorted(plan_cmds)} todo={sorted(todo_cmds)}")
        else:
            for gid, (cmd, exp) in plan_gates.items():
                if gid not in tgates:
                    fail(f"todo.md の Hard Gate 表に {gid} の行がない（計画の Step が台帳から欠落）")
                    continue
                tcmd, texp = tgates[gid]
                if cmd != tcmd:
                    fail(f"{gid} の Gate Command が plan と todo で不一致: plan=`{cmd}` todo=`{tcmd}`")
                if " ".join(exp.split()) != " ".join(texp.split()):
                    fail(f"{gid} の Expect が plan と todo で不一致: plan「{exp}」 todo「{texp}」")
                label = f"Step {gid}" if gid.isdigit() else gid
                tm = re.search(rf"^\s*- \[[ x!\-]\][^\n]*{re.escape(label)}\b[^\n]*", todo_vis, re.M)
                if not tm:
                    fail(f"todo.md に {label} のタスク行（チェックボックス）がない")
                else:
                    if schema == "causal-v1" and gid in plan_step_lcus:
                        expected_lcu = plan_step_lcus[gid]
                        tag = re.search(
                            rf"{re.escape(label)}\s+\(([^)]+)\)\s*[:：]", tm.group(0)
                        )
                        if expected_lcu == "plan-maintenance":
                            if not tag or tag.group(1).strip() != "plan-maintenance":
                                fail(f"todo.md の {label} に Primary LCU `plan-maintenance` が転記されていない")
                        else:
                            if not tag or tag.group(1).strip() != expected_lcu:
                                actual = tag.group(1).strip() if tag else "なし"
                                fail(
                                    f"todo.md の {label} Primary LCU タグは {expected_lcu} と完全一致させる"
                                    f"（現在: {actual}）"
                                )
                    gm = re.search(r"[（(]Gate\s*[:：][^）)]*`([^`]+)`", tm.group(0))
                    if gm and gm.group(1).strip() != cmd:
                        fail(f"todo.md の {label} タスク行の Gate（`{gm.group(1).strip()}`）が Hard Gate 表（`{cmd}`）と不一致")
            for gid in tgates:
                if gid and plan_gates and gid not in plan_gates:
                    fail(f"todo.md の Hard Gate 表に計画に存在しない {gid} がある")
        # full の scaled 閾値は正本 todo.md の実数でも判定する
        if mode == "full":
            n_boxes = len(re.findall(r"^\s*- \[[ x!\-]\]", todo_vis, re.M))
            if n_boxes >= SCALED_TODO_MIN:
                fail(f"todo.md のタスクが {n_boxes} 件（{SCALED_TODO_MIN}件以上は scaled モードにする）")
        # Unclear の整合（codex-impl の fail-closed 判定は todo の要約を見る）
        usec = "\n".join(section_body(plan_vis_lines, "Unclear Points"))
        plan_unclear_none = ("None" in usec) and not re.search(r"Point\s*[:：]", usec)
        unres_lines = [l for l in todo_vis.splitlines() if "未解決" in l]
        if not unres_lines:
            fail("todo.md に「未解決:」要約行がない（着手可否判定の正）")
        else:
            todo_unres = unres_lines[0]
            if plan_unclear_none and "None" not in todo_unres:
                fail("plan の Unclear Points は None だが todo の未解決要約が None でない")
            if not plan_unclear_none and "None" in todo_unres:
                fail("plan に未解決の Unclear Points があるのに todo の未解決要約が None（着手可否判定が無効化される）")

    if failed:
        _stale_html_note(plan, text)
        print("finalize: FAIL（HTML は生成しない）")
        return 1
    builder = Path(__file__).resolve().parent / "build_plan_html.py"
    r = subprocess.run([sys.executable, str(builder), str(plan)], capture_output=True, text=True)
    print(r.stdout.strip())
    if r.returncode != 0:
        _stale_html_note(plan, text)
        print(f"finalize: HTML 生成失敗\n{r.stderr.strip()[:300]}")
        return 1
    print("finalize: PASS（バンドル検査済み・HTML 生成済み）")
    return 0


def main(argv):
    flags = [a for a in argv[1:] if a.startswith("--")]
    unknown = [f for f in flags if f not in KNOWN_FLAGS]
    if unknown:
        print(f"不明なオプション: {', '.join(unknown)}（利用可能: {', '.join(sorted(KNOWN_FLAGS))}）")
        return 2
    args = [a for a in argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    if "--finalize" in flags:
        if "--require-detailed" in flags:
            print("--finalize と --require-detailed は同時に指定できない（詳細化 Gate は個別 lint で実行する）")
            return 2
        if len(args) != 1:
            print("--finalize の引数は計画書（または 00-overview.md）ちょうど1つ")
            return 2
        return finalize(Path(args[0]))
    require_detailed = "--require-detailed" in flags
    failed = False
    paths = [Path(a) for a in args]
    for path in paths:
        errors, warnings = lint_file(path, require_detailed=require_detailed)
        if errors:
            failed = True
            print(f"FAIL: {path}")
            for e in errors:
                print(f"  ✗ {e}")
        else:
            print(f"PASS: {path}")
        for w in warnings:
            print(f"  ⚠ {w}")
    for e in lint_bundle(paths):
        failed = True
        print(f"BUNDLE FAIL: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
