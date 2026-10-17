"""Markdown 表のセル分割（lint_plan.py と build_plan_html.py で共有する唯一の部品）。

`\\|` とインラインコード内の `|` をセル区切りとして扱わない。
コードスパンはバッククォートの連続数（run 長）を記録し、同じ長さの run でだけ閉じる
（``echo `x` | wc -l`` のような複数バッククォート形式に対応）。
この意味論が lint と HTML ビューで分岐すると、正当な Gate コマンドの解釈が
食い違うため、両者はこの関数だけを使う。
"""


def split_row(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    cells, buf = [], []
    code_run = 0  # 開いているコードスパンのバッククォート run 長（0 = 外）
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "`":
            run = 1
            while i + run < n and s[i + run] == "`":
                run += 1
            if code_run == 0:
                code_run = run
            elif run == code_run:
                code_run = 0
            buf.append("`" * run)
            i += run
            continue
        if ch == "\\" and i + 1 < n and s[i + 1] == "|" and code_run == 0:
            buf.append("|")
            i += 2
            continue
        if ch == "|" and code_run == 0:
            cells.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    last = "".join(buf).strip()
    if last or not cells:
        cells.append(last)
    return cells
