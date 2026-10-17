#!/usr/bin/env python3
"""実装計画書 Markdown から自己完結 HTML ビューを生成する。

usage: build_plan_html.py <plan.md> [--output <out.html>]

- 正本は Markdown。この HTML は閲覧・レビュー用の派生ビュー。
- 外部依存なし（python3 標準ライブラリのみ）。
- 本文中で参照されている .svg が実在すればインライン埋め込みする。
"""
import argparse
import base64
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

sys.dont_write_bytecode = True  # スキルディレクトリに __pycache__ を作らない
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mdtable import split_row  # noqa: E402  lint_plan.py と表セル分割の意味論を共有する

TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "plan-template.html"


def esc_attr(s: str) -> str:
    """HTML 属性値用（クォートも実体参照化）。"""
    return html.escape(s, quote=True)


def json_for_script(obj) -> str:
    """<script type=application/json> に安全に埋め込める JSON。

    JSON 文字列中の `</script>` が script 要素を閉じてしまう stored XSS を防ぐため、
    < > & と U+2028/U+2029 を Unicode escape する。
    """
    s = json.dumps(obj, ensure_ascii=False)
    return (s.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
             .replace(" ", "\\u2028").replace(" ", "\\u2029"))


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def inline(s: str) -> str:
    """インライン書式: コード・太字・リンク。エスケープ済み文字列に適用する。

    コードスパン内の `**`（glob 等）を bold と誤解釈しないよう先に退避する。
    """
    codes = []

    def stash(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    def linkify(m):
        text, url = m.group(1), m.group(2)
        scheme = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):", url)
        if scheme and scheme.group(1).lower() not in ("http", "https", "mailto"):
            return m.group(0)  # javascript: 等の危険スキームはリンク化しない
        return f'<a href="{url.replace(chr(34), "%22")}">{text}</a>'

    s = re.sub(r"`([^`]+)`", stash, s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", linkify, s)
    s = re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{codes[int(m.group(1))]}</code>", s)
    return s


def slugify(text: str, used: set) -> str:
    text = unicodedata.normalize("NFKC", text)
    base = re.sub(r"[^\w぀-ヿ一-鿿-]+", "-", text).strip("-") or "sec"
    slug, n = base, 2
    while slug in used:
        slug, n = f"{base}-{n}", n + 1
    used.add(slug)
    return slug


def repo_root(start: Path):
    """start から上へ .git を探す。見つからなければ None。"""
    for d in [start] + list(start.parents):
        if (d / ".git").exists():
            return d
    return None


def find_svg(md_dir: Path, text: str):
    """本文中で参照されている実在の .svg を探す（絶対 → MD相対 → repoルート相対 → cwd相対）。"""
    root = repo_root(md_dir)
    for m in re.finditer(r"[`(]([^`()\s]+\.svg)[`)]", text):
        p = Path(m.group(1))
        cands = [p] if p.is_absolute() else [md_dir / p] + ([root / p] if root else []) + [Path.cwd() / p]
        for cand in cands:
            if cand.is_file():
                return cand
    return None


def visible_md_text(md_text: str):
    """HTML コメント（複数行含む）を除いた可視テキスト。フェンス内はコードとして保持。

    サマリ集計・SVG 探索が「本文に表示されない要素」を数えないための共有前処理。
    """
    out, in_fence, in_comment = [], False, False
    for line in md_text.splitlines():
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
    return "\n".join(out)


def compute_source_hash(md_path: Path, md_text: str):
    """HTML に埋める source hash（MD + 参照 SVG のバイト列）。finalize の stale 判定と共有する。"""
    svg = find_svg(md_path.parent, visible_md_text(md_text))
    svg_bytes = b""
    if svg:
        try:
            svg_bytes = svg.read_bytes()
        except OSError:
            svg_bytes = b""
    return hashlib.sha256(md_text.encode("utf-8") + svg_bytes).hexdigest()[:12]


def convert(md_text: str, md_path: Path):
    lines = md_text.splitlines()
    body, toc = [], []
    used_slugs = set()
    title = md_path.stem
    meta_html = ""
    i = 0
    n = len(lines)
    lists = []  # ネストしたリストのスタック: {"kind": "ul"|"ol", "li_open": bool}
    table_buf = []
    para_buf = []
    checkbox_index = 0
    seen_title = False

    def close_lists(to_depth=0):
        # <li>text<ul>…</ul></li> の順で閉じる（子リストは親 li の内側）
        while len(lists) > to_depth:
            top = lists.pop()
            if top["li_open"]:
                body.append("</li>")
            body.append(f"</{top['kind']}>")
            if lists and lists[-1]["li_open"] and len(lists) > to_depth:
                body.append("</li>")
                lists[-1]["li_open"] = False

    def flush_para():
        if para_buf:
            body.append("<p>" + inline(esc(" ".join(para_buf))) + "</p>")
            para_buf.clear()

    def flush_table():
        nonlocal table_buf
        if not table_buf:
            return
        rows = [r for r in table_buf if not re.match(r"^\s*\|[\s:|-]+\|\s*$", r)]
        out = ['<div class="tablewrap"><table>']
        for ri, row in enumerate(rows):
            cells = split_row(row)
            tag = "th" if ri == 0 else "td"
            out.append("<tr>" + "".join(f"<{tag}>{inline(esc(c))}</{tag}>" for c in cells) + "</tr>")
        out.append("</table></div>")
        body.append("\n".join(out))
        table_buf = []

    in_comment = False
    while i < n:
        line = lines[i]

        # コードフェンス（コメント内の ``` はフェンスとして扱わない — lint と同じ意味論）
        m = re.match(r"^```(\w*)\s*$", line)
        if m and not in_comment:
            flush_para(); flush_table(); close_lists()
            lang = m.group(1)
            block = []
            i += 1
            while i < n and not lines[i].startswith("```"):
                block.append(lines[i]); i += 1
            i += 1
            code = esc("\n".join(block))
            if lang == "mermaid":
                body.append('<details open><summary>Mermaid ソース（GitHub / VS Code ではネイティブ描画される）</summary>'
                            f'<pre><code>{code}</code></pre></details>')
            else:
                body.append(f"<pre><code>{code}</code></pre>")
            continue

        # HTML コメント（複数行対応）は非表示テキストとして除去する。
        # 正本 MD で見えない内容を HTML 側だけに描画しない（lint と同じ可視性契約）
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
                    res.append(line[j:]); j = None
                else:
                    res.append(line[j:k]); j, in_comment = k + 4, True
        line = "".join(res)
        if not line.strip() and lines[i].strip():
            # 行全体がコメントだった場合は空行として扱う
            flush_para(); i += 1; continue

        # 生 HTML は details/summary の属性なし形だけを許可（それ以外はエスケープして段落へ）
        dm = re.match(r"^\s*(?:<details( open)?>)?(?:<summary>(.*)</summary>)?(</details>)?\s*$", line)
        if line.strip().startswith(("<details", "</details", "<summary")):
            flush_para(); flush_table(); close_lists()
            if dm and (dm.group(2) is not None or dm.group(3) or line.strip() in ("<details>", "<details open>")):
                parts = []
                if line.strip().startswith("<details"):
                    parts.append("<details open>" if dm.group(1) else "<details>")
                if dm.group(2) is not None:
                    parts.append(f"<summary>{inline(esc(dm.group(2)))}</summary>")
                if dm.group(3):
                    parts.append("</details>")
                body.append("".join(parts))
            else:
                body.append("<p>" + inline(esc(line.strip())) + "</p>")
            i += 1; continue

        # 見出し
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            flush_para(); flush_table(); close_lists()
            level, text = len(m.group(1)), m.group(2).strip()
            if level == 1 and not seen_title:
                title = text; seen_title = True
                i += 1; continue
            slug = slugify(text, used_slugs)
            if level == 2:
                toc.append(f'<li><a href="#{slug}">{esc(text)}</a></li>')
            body.append(f'<h{level} id="{slug}">{inline(esc(text))}</h{level}>')
            i += 1; continue

        # 表
        if line.strip().startswith("|"):
            flush_para(); close_lists()
            table_buf.append(line); i += 1; continue
        flush_table()

        # 区切り線
        if re.match(r"^\s*---+\s*$", line):
            flush_para(); close_lists()
            body.append("<hr>"); i += 1; continue

        # 引用
        if line.startswith(">"):
            flush_para(); close_lists()
            quote = [line.lstrip("> ")]
            while i + 1 < n and lines[i + 1].startswith(">"):
                i += 1; quote.append(lines[i].lstrip("> "))
            body.append("<blockquote>" + inline(esc(" ".join(quote))) + "</blockquote>")
            i += 1; continue

        # リスト（チェックボックス含む・ネスト対応）
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)", line)
        if m:
            flush_para()
            # インデント2文字=1段。既存の深さ+1までにクランプ（段飛ばしを防ぐ）
            depth = min(len(m.group(1)) // 2, len(lists))
            kind = "ol" if m.group(2)[0].isdigit() else "ul"
            # 深い階層を閉じる（閉じたリストを内包する親 li は開いたままにする）
            while len(lists) > depth + 1:
                top = lists.pop()
                if top["li_open"]:
                    body.append("</li>")
                body.append(f"</{top['kind']}>")
            if len(lists) == depth + 1 and lists[-1]["kind"] != kind:
                close_lists(depth)
            if len(lists) == depth:
                # 新しい階層を開く（親 li の内側に開く）
                body.append(f"<{kind}>")
                lists.append({"kind": kind, "li_open": False})
            # 同階層の直前の li を閉じてから新しい li を開く
            if lists[-1]["li_open"]:
                body.append("</li>")
            content = m.group(3)
            cb = re.match(r"^\[([ x!\-])\]\s*(.*)", content)
            if cb:
                state, rest = cb.group(1), inline(esc(cb.group(2)))
                if state == "!":
                    body.append(f'<li class="task blocked"><span class="status-b">BLOCKED</span>{rest}')
                elif state == "-":
                    body.append(f'<li class="task done"><span class="status-c">CANCELLED</span>{rest}')
                else:
                    checked = " checked" if state == "x" else ""
                    body.append(f'<li class="task"><input type="checkbox"{checked} data-i="{checkbox_index}">{rest}')
                    checkbox_index += 1
            else:
                body.append("<li>" + inline(esc(content)))
            lists[-1]["li_open"] = True
            i += 1; continue
        close_lists()

        # 空行
        if not line.strip():
            flush_para(); i += 1; continue

        # 段落
        para_buf.append(line.strip())
        i += 1

    flush_para(); flush_table(); close_lists()

    # タイトル直後のメタ（先頭のリスト）は本文先頭に含まれるのでそのまま。meta_html は未使用でも可。
    return title, meta_html, "\n".join(body), toc


def build_summary(md_text: str, mode_text=None):
    """集計は可視テキスト（md_text）に対して行う。mode 宣言は HTML コメントなので原文（mode_text）から読む。"""
    mode_m = re.search(r"<!--\s*plan-mode:\s*([\w-]+)\s*-->", mode_text or md_text)
    mode = mode_m.group(1) if mode_m else "?"
    steps = len(re.findall(r"^###\s+Step", md_text, re.M))
    todos = re.findall(r"^\s*- \[([ x!\-])\]", md_text, re.M)
    done = sum(1 for t in todos if t in "x-")  # 消し込み済み = 完了 + cancelled
    unclear_sec = re.search(r"Unclear Points(.*?)(?=^## |\Z)", md_text, re.S | re.M)
    utext = unclear_sec.group(1) if unclear_sec else ""
    unclear = "None" if "None" in utext else str(len(re.findall(r"^\s*\d+\.\s*Point", utext, re.M)) or utext.count("Point:"))
    gates = len(re.findall(r"Command\s*[:：]", md_text))
    chips = [
        ("mode", mode, "モード"),
        ("", str(steps), "実装 Step"),
        ("", f"{done} / {len(todos)}", "Todo（MD時点）"),
        ("", unclear, "Unclear Points"),
        ("", str(gates), "Hard Gate"),
    ]
    html_chips = "".join(
        f'<div class="chip {cls}"><div class="n">{esc(v)}</div><div class="l">{l}</div></div>'
        for cls, v, l in chips)
    manifest = {"mode": mode, "steps": steps, "todo_total": len(todos), "todo_done": done,
                "unclear": unclear, "hard_gates": gates}
    return html_chips, manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--output")
    args = ap.parse_args()
    md_path = Path(args.plan).resolve()
    if not md_path.is_file():
        print(f"見つからない: {md_path}", file=sys.stderr); return 2
    md_text = md_path.read_text(encoding="utf-8")

    title, meta_html, body_html, toc = convert(md_text, md_path)
    vis_text = visible_md_text(md_text)
    summary_html, manifest = build_summary(vis_text, mode_text=md_text)

    svg = find_svg(md_path.parent, vis_text)
    svg_bytes = b""
    if svg:
        try:
            # 生SVGのインラインは <script>/<style>/on* が本文に波及するため、
            # 実行されない <img> + data URI で埋め込む
            svg_bytes = svg.read_bytes()
            b64 = base64.b64encode(svg_bytes).decode("ascii")
            body_html += (f'\n<h2 id="inline-svg">図（{esc(svg.name)}）</h2>\n'
                          f'<div class="svgwrap"><img src="data:image/svg+xml;base64,{b64}" '
                          f'alt="{esc(svg.name)}" style="max-width:100%"></div>')
        except OSError:
            svg_bytes = b""

    out = Path(args.output) if args.output else md_path.with_suffix(".html")
    # コミット対象に絶対パス・生成時刻を入れない（再現性: 同一の MD + 参照 SVG からは同一の HTML）
    root = repo_root(md_path.parent)
    src_label = str(md_path.relative_to(root)) if root and md_path.is_relative_to(root) else md_path.name
    src_hash = compute_source_hash(md_path, md_text)
    manifest["source"] = src_label
    manifest["source_hash"] = src_hash
    tpl = TEMPLATE.read_text(encoding="utf-8")
    page = (tpl.replace("__TITLE__", esc(title))
               .replace("__SOURCE_PATH__", esc_attr(src_label))
               .replace("__SOURCE_HASH__", src_hash)
               .replace("__META_HTML__", meta_html)
               .replace("__SUMMARY_HTML__", summary_html)
               .replace("__TOC_HTML__", "<ol>" + "".join(toc) + "</ol>")
               .replace("__BODY_HTML__", body_html)
               .replace("__MANIFEST_JSON__", json_for_script(manifest)))
    if out.is_file() and out.read_text(encoding="utf-8") == page:
        print(f"unchanged: {out}")
        return 0
    # 予測不能な名前 + O_EXCL の一時ファイルに書いてから原子的に置換する
    # （固定名 .tmp への symlink 経由で無関係ファイルを上書きさせない）
    fd, tmpname = tempfile.mkstemp(dir=str(out.parent), prefix=out.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(page)
        os.replace(tmpname, out)
    except BaseException:
        try:
            os.unlink(tmpname)
        except OSError:
            pass
        raise
    print(f"wrote: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
