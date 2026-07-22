#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""crv2 renderer: validated review.json → 日本語 Markdown + 導出 artifact。

schema-first フローの最終段。写像の正本は references/output-format.md で、本スクリプトが
その機械実装。モデル(エージェント)は Action・P・verdict・件数・OK 行を一切書かず、
finding の agent fields(impact / exposure.trigger_class / exposure.operational_rate /
mitigation / confidence / defect_class / uncertainty / sentinel / context_effect)から
本スクリプトが決定論的に導出する(oracle 第2レビュー §1.1 / §3.1-6 / §3.1-14)。

Action 導出(derive_action。validate_review.py も同じ関数を import して使う):
  base = impact × trigger_class:
      critical: routine→block / conditional→fix-before-merge / adversarial→follow-up
      high:     routine→fix-before-merge / conditional→fix-before-merge / adversarial→follow-up
      medium:   routine→fix-before-merge / conditional→follow-up / adversarial→follow-up
      low:      routine→follow-up / conditional→follow-up / adversarial→omit
  調整(番号順):
    1. operational_rate=rare → 1 段降格。条件: 有効な context_effect(basis が /facts/ 配下 +
       降格適格 source_type + 期限内 as_of + causal_link + counterfactual)があり、かつ
       defect_class が非降格クラス外。impact が critical/high は follow-up 未満に下げない。
    2. operational_rate=frequent → 1 段昇格(block へ上げられるのは impact: critical のみ)。
    3. unknown / occasional → 調整なし(情報が無いことを理由に下げない = fail-closed)。
    4. mitigation=easy-recovery → 非降格クラス外でのみ 1 段降格。
    5. uncertainty.kind=behavior → Action を clarify-spec に上書き(残留リスクへ質問として)。
    6. confidence=low(evidence uncertainty)→ 導出 Action を保持したまま残留リスクへ移す。
  P 写像: block→P0 / fix-before-merge→P1 / follow-up→P2 / clarify-spec→P なし / omit→非表示。

context_effect の profile 照合(フィールド実在・値・source/as_of の一致)は validator の
責務で、renderer は構文・鮮度の壊れた context_effect を「適用しない」(降格せずに描画する)。

KPI 用 artifact(--derivation-out): 各 finding の action_without_context(base)と
action_with_context(調整 1-4 適用後)を保存し、Context Action Flip Rate の分子を数えられる
ようにする。sentinel: true の高 Impact 候補が調整 1・4 で降格された場合は監査フラグを立て、
表示は調整後のみ・artifact からは消さない(oracle §1.1 / §4.3)。

profile_gaps は review.json に全件保存されている前提で、人間向け表示は 3 件まで
(action_changes_if_answered: true を優先)、--gaps-out で全件を JSON に書き出す。

exit code: 0 = 出力成功 / 2 = 構造不備で描画不能 / 3 = parse 不能。

使い方:
  python3 render_review.py review.json
  python3 render_review.py --mode audit --reviewed-by "Correctness (sentinel): 差分全体" review.json
  python3 render_review.py --today 2026-07-12 --non-demotable payroll-corruption \
      --gaps-out profile_gaps.json --derivation-out action_derivation.json -o review.md review.json
  python3 render_review.py --unattested review.json   # agent-native 直実行の印
  python3 render_review.py --selftest
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile

EXIT_OK = 0
EXIT_STRUCTURE = 2
EXIT_PARSE = 3

_HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(_HERE, "fixtures")

# ---------------------------------------------------------------------------
# Action 導出表(正本: references/output-format.md)
# ---------------------------------------------------------------------------

LADDER = ["block", "fix-before-merge", "follow-up", "omit"]

BASE_ACTION = {
    ("critical", "routine"): "block",
    ("critical", "conditional"): "fix-before-merge",
    ("critical", "adversarial"): "follow-up",
    ("high", "routine"): "fix-before-merge",
    ("high", "conditional"): "fix-before-merge",
    ("high", "adversarial"): "follow-up",
    ("medium", "routine"): "fix-before-merge",
    ("medium", "conditional"): "follow-up",
    ("medium", "adversarial"): "follow-up",
    ("low", "routine"): "follow-up",
    ("low", "conditional"): "follow-up",
    ("low", "adversarial"): "omit",
}

P_MAP = {"block": "P0", "fix-before-merge": "P1", "follow-up": "P2"}

# 組織必須の非降格クラス(profile はここへ追加のみ可、削除不可)
ORG_NON_DEMOTABLE = (
    "authz-boundary",
    "destructive-data-loss",
    "irreversible-external-effect",
    "production-credential",
)

# 降格根拠に使える source_type(shared-default / code-scan / document は降格不可)
DEMOTION_SOURCE_TYPES = ("telemetry", "user-confirmed", "incident-review")
FRESHNESS_DAYS = 90  # as_of の鮮度ウィンドウ(超過した事実は降格根拠にならない)

ACTION_ORDER = {"block": 0, "fix-before-merge": 1, "follow-up": 2, "clarify-spec": 3, "omit": 4}
IMPACT_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
# 検証を通過した指摘は表示段で切らずに全件フル表示する(ノイズ抑制は生成側で行う。
# 旧 FOLLOWUP_MAX / RESIDUAL_MAX の表示上限は撤廃した)。
GAPS_DISPLAY_MAX = 3  # Profile gaps の人間向け表示上限(artifact には全件)

EVIDENCE_TYPE_JA = {
    "reproduced": "再現済み",
    "code-trace": "コードトレース確定",
    "inference": "推測",
}
INVARIANT_TYPE_JA = {
    "domain-invariant": "ドメイン不変条件",
    "local-contract": "局所契約",
    "unknown": "不明",
}
ADJUSTMENT_JA = {
    "rare-demotion": "rare 降格 1 段",
    "frequent-promotion": "frequent 昇格 1 段",
    "easy-recovery-demotion": "easy-recovery 降格 1 段",
}

HEADINGS = {
    "review": {
        "findings": "## 新規または拡大した指摘",
        "verdict": "## Patch verdict",
    },
    "audit": {
        "findings": "## 優先して直すべきコードベース指摘",
        "verdict": "## Codebase audit verdict",
    },
}


def _parse_date(value):
    """YYYY-MM-DD 文字列または date を date へ。parse 不能は None。"""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def context_effect_usable(finding, today):
    """context_effect が降格根拠として構文・鮮度上有効か(renderer 段の検査)。

    profile との照合(フィールド実在・値・source/as_of 一致)は validate_review.py が行う。
    attested 実行では invalid な context_effect は validator が error にするため、ここに
    到達するのは有効なもののみ。agent-native 直実行でもゴム印降格を適用しないための防波堤。
    """
    ce = finding.get("context_effect")
    if not isinstance(ce, dict):
        return False
    basis = ce.get("basis")
    if not isinstance(basis, list) or not basis:
        return False
    for b in basis:
        if not isinstance(b, dict):
            return False
        if not str(b.get("profile_field", "")).startswith("/facts/"):
            return False  # 降格根拠に使えるのは facts(運用事実)のみ
        if b.get("source_type") not in DEMOTION_SOURCE_TYPES:
            return False
        as_of = _parse_date(b.get("as_of"))
        if as_of is None or as_of > today or (today - as_of).days > FRESHNESS_DAYS:
            return False  # 未来日・期限切れは降格根拠にならない
    if not str(ce.get("causal_link") or "").strip():
        return False
    if not str(ce.get("counterfactual") or "").strip():
        return False
    return True


def derive_action(finding, non_demotable=ORG_NON_DEMOTABLE, today=None):
    """finding の agent fields から Action / P / 表示先を決定論導出する。

    戻り値 dict:
      base_action / action_without_context(= base。文脈・復旧性調整前)
      action_with_context(調整 1-4 適用後)/ final_action(調整 5 適用後)
      p / bucket(main | residual | omit)/ adjustments / audit_flag
    導出不能(impact / exposure が構造不備)なら None。
    """
    today = today or datetime.date.today()
    exposure = finding.get("exposure")
    if not isinstance(exposure, dict):
        return None
    base = BASE_ACTION.get((finding.get("impact"), exposure.get("trigger_class")))
    if base is None:
        return None

    rate = exposure.get("operational_rate")
    demotable = finding.get("defect_class") not in set(non_demotable)
    action = base
    adjustments = []

    # 調整 1: rare 降格(有効な context_effect + 非降格クラス外のみ。高 Impact は follow-up 床)
    if rate == "rare" and demotable and context_effect_usable(finding, today):
        cand = LADDER[min(LADDER.index(action) + 1, len(LADDER) - 1)]
        if finding.get("impact") in ("critical", "high") and \
                LADDER.index(cand) > LADDER.index("follow-up"):
            cand = "follow-up"
        if cand != action:
            action = cand
            adjustments.append("rare-demotion")
    # 調整 2: frequent 昇格(block へ上げられるのは critical のみ)
    elif rate == "frequent":
        cand = LADDER[max(LADDER.index(action) - 1, 0)]
        if cand == "block" and finding.get("impact") != "critical":
            cand = "fix-before-merge"
        if cand != action:
            action = cand
            adjustments.append("frequent-promotion")
    # 調整 3: unknown / occasional → 調整なし(fail-closed)

    # 調整 4: easy-recovery 降格(非降格クラス外のみ)
    if finding.get("mitigation") == "easy-recovery" and demotable:
        cand = LADDER[min(LADDER.index(action) + 1, len(LADDER) - 1)]
        if cand != action:
            action = cand
            adjustments.append("easy-recovery-demotion")

    # 調整 5: behavior uncertainty → clarify-spec 上書き(質問として残留リスクへ)
    uncertainty = finding.get("uncertainty") or {}
    final = "clarify-spec" if uncertainty.get("kind") == "behavior" else action

    # 調整 6: confidence=low(evidence uncertainty)→ Action 保持のまま残留リスクへ
    if final == "clarify-spec":
        bucket = "residual"
    elif final == "omit":
        bucket = "omit"
    elif finding.get("confidence") == "low":
        bucket = "residual"
    else:
        bucket = "main"

    audit_flag = bool(finding.get("sentinel")) and \
        finding.get("impact") in ("critical", "high") and \
        any(a in ("rare-demotion", "easy-recovery-demotion") for a in adjustments)

    return {
        "base_action": base,
        "action_without_context": base,
        "action_with_context": action,
        "final_action": final,
        "p": P_MAP.get(final),
        "bucket": bucket,
        "adjustments": adjustments,
        "audit_flag": audit_flag,
    }


def derivation_record(finding, deriv):
    """KPI 用 artifact(action_derivation.json)の 1 レコード。"""
    exposure = finding.get("exposure") or {}
    uncertainty = finding.get("uncertainty") or {}
    return {
        "id": finding.get("id"),
        "sentinel": bool(finding.get("sentinel")),
        "impact": finding.get("impact"),
        "trigger_class": exposure.get("trigger_class"),
        "operational_rate": exposure.get("operational_rate"),
        "mitigation": finding.get("mitigation"),
        "defect_class": finding.get("defect_class"),
        "uncertainty_kind": uncertainty.get("kind"),
        "confidence": finding.get("confidence"),
        "has_context_effect": isinstance(finding.get("context_effect"), dict),
        "base_action": deriv["base_action"],
        "action_without_context": deriv["action_without_context"],
        "action_with_context": deriv["action_with_context"],
        "final_action": deriv["final_action"],
        "p": deriv["p"],
        "bucket": deriv["bucket"],
        "adjustments": deriv["adjustments"],
        "audit_flag": deriv["audit_flag"],
    }


# ---------------------------------------------------------------------------
# Markdown 描画
# ---------------------------------------------------------------------------

def _field(f, key, default="(未記載)"):
    v = f.get(key)
    return v if isinstance(v, str) and v else default


def _sort_key(item):
    idx, f, deriv = item
    return (
        ACTION_ORDER.get(deriv["final_action"], 9),
        IMPACT_ORDER.get(f.get("impact"), 9),
        idx,  # 同順位は入力順を保持
    )


def render_finding(f, deriv):
    lines = []
    title = _field(f, "title")
    children = f.get("children") or []
    if children and "【原因】" not in title:
        title = "【原因】" + title
    if f.get("sentinel"):
        title += "(sentinel)"
    lines.append("### [%s] %s" % (deriv["p"] or "P?", title))

    soc = f.get("symptom_or_cause")
    lines.append("- 症状/原因: %s" % ("原因そのもの" if soc == "cause" else "症状"))

    exposure = f.get("exposure") or {}
    lines.append("- Impact / Exposure / Mitigation: %s / %s + %s / %s"
                 % (_field(f, "impact"),
                    exposure.get("trigger_class", "(未記載)"),
                    exposure.get("operational_rate", "(未記載)"),
                    _field(f, "mitigation")))

    uncertainty = f.get("uncertainty") or {}
    unc = uncertainty.get("kind", "(未記載)")
    if uncertainty.get("note"):
        unc += "(%s)" % uncertainty["note"]
    lines.append("- defect_class / uncertainty: %s / %s" % (_field(f, "defect_class"), unc))

    ev_type = EVIDENCE_TYPE_JA.get(f.get("confidence_evidence_type"), "(未記載)")
    lines.append("- Confidence: %s(%s)" % (_field(f, "confidence"), ev_type))

    trigger = _field(f, "trigger")
    sec = f.get("security_reachability")
    if isinstance(sec, dict):
        trigger += "(caller capability: %s / reachability: %s)" % (
            sec.get("caller_capability", "(未記載)"), sec.get("reachability", "(未記載)"))
    lines.append("- どんなときに起きるか: %s" % trigger)

    inv = f.get("invariant") or {}
    inv_type = INVARIANT_TYPE_JA.get(inv.get("type"), "(未記載)")
    lines.append("- 壊れる不変条件(%s): %s" % (inv_type, inv.get("text", "(未記載)")))

    ce = f.get("counterevidence_checked") or {}
    lines.append("- 反証チェック: %s — 確認先: %s"
                 % (ce.get("hypothesis", "(未記載)"), ce.get("where_checked", "(未記載)")))

    ctx = f.get("context_effect")
    if isinstance(ctx, dict):
        basis_txt = "; ".join(
            "%s(%s, %s)" % (b.get("profile_field", "?"), b.get("source_type", "?"),
                            b.get("as_of", "?"))
            for b in (ctx.get("basis") or []) if isinstance(b, dict))
        lines.append("- context_effect: %s — %s。反証条件: %s"
                     % (basis_txt or "(basis 未記載)",
                        ctx.get("causal_link", "(未記載)"),
                        ctx.get("counterfactual", "(未記載)")))
        if "rare-demotion" in deriv["adjustments"]:
            lines.append("  適用: %s → %s(文脈適用前後の両方を action_derivation.json に保存)"
                         % (deriv["action_without_context"], deriv["action_with_context"]))
        else:
            lines.append("  適用なし(降格根拠を満たさない、または非降格クラス)")

    lines.append("- 根拠:")
    for ev in f.get("evidence") or []:
        if isinstance(ev, dict):
            lines.append("  %s:%s" % (ev.get("path", "(未記載)"), ev.get("line", "?")))

    adj = "、".join(ADJUSTMENT_JA.get(a, a) for a in deriv["adjustments"])
    detail = "base %s" % deriv["base_action"] + ("、" + adj if adj else "")
    lines.append("- Action: %s(renderer 導出: %s。P は固定写像)" % (deriv["final_action"], detail))

    if children:
        lines.append("- この原因による症状(子 finding の id / Impact / trigger を保持):")
        for i, child in enumerate(children, start=1):
            if isinstance(child, dict):
                lines.append("  %d. %s (%s): %s"
                             % (i, child.get("id", "?"), child.get("impact", "?"),
                                child.get("trigger", "(未記載)")))
    return lines


def render_residual_item(f, deriv):
    """clarify-spec / confidence=low の指摘を質問形式で描画(導出 Action は併記して保持)。"""
    if deriv["final_action"] == "clarify-spec":
        tag = "要仕様確認"
    else:
        tag = "低confidence・導出 Action: %s" % deriv["final_action"]
    ce = f.get("counterevidence_checked") or {}
    line1 = "- [%s] %s: %s — %s" % (tag, f.get("id", "?"), _field(f, "title"), _field(f, "trigger"))
    line2 = "  確認すること: %s(確認先: %s)" % (
        ce.get("hypothesis", "(未記載)"), ce.get("where_checked", "(未記載)"))
    return [line1, line2]


def derive_verdict(main_items, residual_items, mode):
    """verdict の機械導出。P0/P1 が存在する限り correct 系は出さない(固定規則)。"""
    actions = {deriv["final_action"] for _, _, deriv in main_items}
    has_fixnow = bool(actions & {"block", "fix-before-merge"})
    has_followup = "follow-up" in actions
    has_residual = bool(residual_items)

    if mode == "audit":
        if has_fixnow:
            overall = "risky"
        elif has_residual:
            overall = "cannot judge without deeper runtime evidence"
        elif has_followup:
            overall = "mostly healthy"
        else:
            overall = "healthy"
    else:
        if has_fixnow:
            overall = "incorrect"
        elif has_residual:
            overall = "cannot judge without spec"
        elif has_followup:
            overall = "mostly correct"
        else:
            overall = "correct"

    p0 = [f.get("id", "?") for _, f, d in main_items if d["final_action"] == "block"]
    p1 = [f.get("id", "?") for _, f, d in main_items if d["final_action"] == "fix-before-merge"]
    if has_fixnow:
        parts = []
        if p0:
            parts.append("P0 %d件(%s)" % (len(p0), ", ".join(p0)))
        if p1:
            parts.append("P1 %d件(%s)" % (len(p1), ", ".join(p1)))
        reason = ("%s が未解消のため correct 系の判定は付けられない(固定規則)。"
                  "判断は新規・拡大指摘のみに基づき、既存の無関係な問題は含めていない。"
                  % "・".join(parts))
    elif has_residual:
        reason = ("未確認の仕様・ポリシー、または低 confidence の論点が %d 件残っており、"
                  "その成否で評価が変わるため。" % len(residual_items))
    elif has_followup:
        reason = "マージを止める欠陥は見つからず、follow-up(P2)のみが残るため。"
    else:
        reason = "新規・拡大した欠陥は見つからなかったため。"
    return overall, reason


def render(review, mode="review", reviewed_by=None, summary=None, unattested=False,
           non_demotable=ORG_NON_DEMOTABLE, today=None):
    """review.json → (Markdown 文字列, artifacts dict)。

    artifacts = {"derivations": [...], "profile_gaps": {...},
                 "context_action_flip_count": int, "audit_flagged_ids": [...]}
    """
    today = today or datetime.date.today()
    findings = [f for f in (review.get("findings") or []) if isinstance(f, dict)]
    heads = HEADINGS[mode]

    items = []       # (idx, finding, deriv)
    derivations = []
    broken = []
    for i, f in enumerate(findings):
        deriv = derive_action(f, non_demotable, today)
        if deriv is None:
            broken.append(f.get("id", "findings[%d]" % i))
            continue
        items.append((i, f, deriv))
        derivations.append(derivation_record(f, deriv))

    main_items = sorted([it for it in items if it[2]["bucket"] == "main"], key=_sort_key)
    residual_items = sorted([it for it in items if it[2]["bucket"] == "residual"], key=_sort_key)
    omit_items = [it for it in items if it[2]["bucket"] == "omit"]
    audit_flagged = [f.get("id", "?") for _, f, d in items if d["audit_flag"]]
    flip_count = sum(1 for d in derivations
                     if d["action_without_context"] != d["action_with_context"])

    # 検証を通過した主要指摘は Action の重い順(_sort_key: block → fix-before-merge →
    # follow-up)にフル表示する。表示上限による割愛はしない(ノイズ抑制は生成側の責務)。
    displayed = list(main_items)

    out = []

    # ## Reviewed by
    out.append("## Reviewed by")
    target = review.get("target") or {}
    if target.get("repo"):
        t = "- 対象: %s" % target["repo"]
        if target.get("pr") is not None:
            t += "(PR #%s)" % target["pr"]
        if target.get("head_sha"):
            t += " @ %s" % target["head_sha"]
        out.append(t)
    if reviewed_by:
        for line in reviewed_by:
            out.append("- %s" % line)
    else:
        out.append("- (レビュアーレーン構成は review.json に含まれないため、coordinator の実行記録を参照)")
    env_notes = review.get("review_env_notes") or []
    if env_notes:
        out.append("- レビュー環境の注意:")
        for note in env_notes:
            out.append("  - %s" % note)
    if broken:
        out.append("- 導出不能の指摘(impact / exposure の構造不備): %s" % ", ".join(broken))
    out.append("")

    # ## 新規または拡大した指摘(audit: 優先して直すべきコードベース指摘)
    out.append(heads["findings"])
    if displayed:
        for idx, f, deriv in displayed:
            out.extend(render_finding(f, deriv))
            out.append("")
        if out[-1] == "":
            out.pop()
    else:
        out.append("- なし")
    out.append("")

    # ## 総評(件数・帰着数・監査フラグは機械集計。散文は --summary で coordinator が注入)
    out.append("## 総評")
    if summary:
        for line in summary:
            out.append("- %s" % line)
    counts = {"P0": 0, "P1": 0, "P2": 0}
    for _, f, deriv in main_items:
        if deriv["p"]:
            counts[deriv["p"]] += 1
    out.append("- 件数(機械集計): P0 %d件 / P1 %d件 / P2 %d件。残留リスク / 未確認点へ %d件を自動配置した。"
               % (counts["P0"], counts["P1"], counts["P2"],
                  len(residual_items) + len(review.get("residual_risks") or [])))
    clusters = [(f.get("cluster_id"), len(f.get("children") or []), f.get("id", "?"))
                for _, f, _d in main_items if f.get("children")]
    if clusters:
        cause_units = len({cid for cid, _, _ in clusters})
        child_total = sum(n for _, n, _ in clusters)
        non_cluster = len([1 for _, f, _d in main_items if not f.get("children")])
        detail = "、".join("%s(親 %s・子 %d件)" % (cid, fid, n) for cid, n, fid in clusters)
        out.append("- 原因は実質 %d 個(クラスタ %s に子 %d件が帰着。クラスタ外 %d件)。"
                   % (cause_units + non_cluster, detail, child_total, non_cluster))
    if audit_flagged:
        out.append("- 監査フラグ: %s(sentinel 由来の高 Impact 候補が文脈適用で降格。"
                   "action_derivation.json に適用前後を保存)" % ", ".join(audit_flagged))
    out.append("- Action・P・verdict・件数は render_review.py が固定写像で機械導出した(モデルの直書きではない)。")
    out.append("")

    # ## 既存の無関係な問題(スキーマに区分が無いため常に「なし」)
    out.append("## 既存の無関係な問題")
    out.append("- なし(本レビューの review.json には diff 起因の指摘のみを含める運用)")
    out.append("")

    # audit のみ: 導出 omit をバックログとして 1 行表示(通常レビューでは非表示・artifact 保存)
    if mode == "audit" and omit_items:
        out.append("## バックログ(導出 omit・codebase audit のみ)")
        for _, f, _d in omit_items:
            out.append("- %s: %s" % (f.get("id", "?"), _field(f, "title")))
        out.append("")

    # ## 残留リスク / 未確認点(clarify-spec 質問・低 confidence 指摘・assurance gap を全件フル表示)
    out.append("## 残留リスク / 未確認点")
    risks = review.get("residual_risks") or []
    residual_blocks = [render_residual_item(f, d) for _, f, d in residual_items]
    for r in risks:
        if isinstance(r, dict):
            residual_blocks.append(["- %s — 確認手順: %s"
                                    % (r.get("title", "(未記載)"),
                                       r.get("check_procedure", "(未記載)"))])
    if residual_blocks:
        for block in residual_blocks:
            out.extend(block)
    else:
        out.append("- なし")
    out.append("")

    # ## Profile gaps(全件は artifact、人間向けは 3 件まで。true を優先)
    out.append("## Profile gaps")
    gaps = [g for g in (review.get("profile_gaps") or []) if isinstance(g, dict)]
    gaps_sorted = sorted(enumerate(gaps),
                         key=lambda ig: (not ig[1].get("action_changes_if_answered"), ig[0]))
    displayed_gaps = [g for _, g in gaps_sorted[:GAPS_DISPLAY_MAX]]
    if displayed_gaps:
        for g in displayed_gaps:
            line = "- %s — %s" % (g.get("field", "?"), g.get("proposed_question", "(質問未記載)"))
            rel = g.get("related_finding_ids") or []
            if rel:
                line += "(関連: %s)" % ", ".join(rel)
            if not g.get("action_changes_if_answered"):
                line += "(回答で Action は変わらない: 記録のみ)"
            out.append(line)
        if len(gaps) > GAPS_DISPLAY_MAX:
            out.append("- 他 %d 件は profile_gaps.json に保存(表示上限 %d)"
                       % (len(gaps) - GAPS_DISPLAY_MAX, GAPS_DISPLAY_MAX))
    else:
        out.append("- なし")
    out.append("")

    # ## Patch verdict(audit: Codebase audit verdict)
    overall, reason = derive_verdict(main_items, residual_items, mode)
    out.append(heads["verdict"])
    out.append("- overall: %s" % overall)
    out.append("- reason: %s" % reason)

    if unattested:
        out.append("")
        out.append("UNATTESTED")

    artifacts = {
        "derivations": derivations,
        "context_action_flip_count": flip_count,
        "audit_flagged_ids": audit_flagged,
        "profile_gaps": {
            "all": gaps,
            "displayed_fields": [g.get("field") for g in displayed_gaps],
            "display_limit": GAPS_DISPLAY_MAX,
        },
    }
    return "\n".join(out) + "\n", artifacts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run(argv):
    parser = argparse.ArgumentParser(
        description="crv2 renderer: validated review.json → 日本語 Markdown + 導出 artifact"
                    "(exit 0=成功 / 2=構造不備 / 3=parse不能)")
    parser.add_argument("review", nargs="?", help="validated review.json のパス")
    parser.add_argument("--mode", choices=["review", "audit"], default="review",
                        help="review=通常レビュー(既定) / audit=codebase audit(見出し・verdict 語彙を置換)")
    parser.add_argument("--reviewed-by", action="append", default=None, metavar="LINE",
                        help="Reviewed by 節の行(複数指定可)。coordinator が実際のレーン構成を注入する")
    parser.add_argument("--summary", action="append", default=None, metavar="LINE",
                        help="総評の散文行(複数指定可)。Action・P・verdict・件数は注入不可で常に機械導出")
    parser.add_argument("--non-demotable", action="append", default=None, metavar="CLASS",
                        help="profile が追加する非降格 defect_class(組織必須 4 クラスは常に含まれる)")
    parser.add_argument("--today", default=None, metavar="YYYY-MM-DD",
                        help="as_of 鮮度判定の基準日(selftest / 再現用。既定は実行日)")
    parser.add_argument("--gaps-out", default=None, metavar="PATH",
                        help="profile_gaps 全件の書き出し先 JSON(人間向け表示は 3 件まで)")
    parser.add_argument("--derivation-out", default=None, metavar="PATH",
                        help="Action 導出 artifact(without/with context・監査フラグ)の書き出し先 JSON")
    parser.add_argument("--unattested", action="store_true",
                        help="agent-native 直実行(best-effort)の印として最終行に UNATTESTED を付ける")
    parser.add_argument("-o", "--output", help="出力先ファイル(省略時は標準出力)")
    parser.add_argument("--selftest", action="store_true", help="fixtures と組込みケースで自己検証する")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    if not args.review:
        print("error: review.json のパスを指定する(または --selftest)", file=sys.stderr)
        return EXIT_PARSE

    today = None
    if args.today:
        today = _parse_date(args.today)
        if today is None:
            print("error: --today は YYYY-MM-DD 形式で指定する", file=sys.stderr)
            return EXIT_PARSE

    try:
        with open(args.review, "r", encoding="utf-8") as fh:
            review = json.load(fh)
    except (OSError, ValueError) as exc:
        print("error: review.json を parse できない: %s" % exc, file=sys.stderr)
        return EXIT_PARSE

    if not isinstance(review, dict) or not isinstance(review.get("findings"), list):
        print("error: review.json の構造が不正(findings 配列が無い)。"
              "先に validate_review.py を通すこと", file=sys.stderr)
        return EXIT_STRUCTURE

    non_demotable = tuple(dict.fromkeys(list(ORG_NON_DEMOTABLE) + (args.non_demotable or [])))
    text, artifacts = render(review, mode=args.mode, reviewed_by=args.reviewed_by,
                             summary=args.summary, unattested=args.unattested,
                             non_demotable=non_demotable, today=today)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    if args.gaps_out:
        with open(args.gaps_out, "w", encoding="utf-8") as fh:
            json.dump(artifacts["profile_gaps"], fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    if args.derivation_out:
        with open(args.derivation_out, "w", encoding="utf-8") as fh:
            json.dump({
                "findings": artifacts["derivations"],
                "context_action_flip_count": artifacts["context_action_flip_count"],
                "audit_flagged_ids": artifacts["audit_flagged_ids"],
            }, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    return EXIT_OK


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

FIXED_TODAY = datetime.date(2026, 7, 12)  # fixture の as_of(2026-07-01)が期限内になる基準日

VALID_CE = {
    "effect": "lower-operational-rate-one-step",
    "basis": [{"profile_field": "/facts/concurrency/human_same_record_overlap",
               "source_type": "telemetry", "as_of": "2026-07-01", "relation": "trigger-rate"}],
    "causal_link": "競合には同一レコードへの保存の重なりが必要で、その観測率が rare",
    "counterfactual": "月1件以上の重複保存が観測されたら降格しない",
}


def _base_finding(fid, impact="medium", trigger_class="routine", rate="unknown",
                  mitigation="none", confidence="high", defect_class="correctness",
                  uncertainty_kind="none", sentinel=False, **over):
    f = {
        "id": fid,
        "title": "テスト指摘 %s" % fid,
        "impact": impact,
        "exposure": {"trigger_class": trigger_class, "operational_rate": rate},
        "mitigation": mitigation,
        "confidence": confidence,
        "confidence_evidence_type": "code-trace",
        "defect_class": defect_class,
        "uncertainty": {"kind": uncertainty_kind},
        "sentinel": sentinel,
        "invariant": {"type": "local-contract", "text": "テスト用の不変条件"},
        "symptom_or_cause": "cause",
        "trigger": "テスト操作をすると発生する",
        "evidence": [{"path": "src/app/main.go", "line": 1}],
        "counterevidence_checked": {"hypothesis": "ガードがあるはず", "where_checked": "src/app/main.go:1-10"},
    }
    f.update(over)
    return f


def _derive(f, non_demotable=ORG_NON_DEMOTABLE):
    return derive_action(f, non_demotable, FIXED_TODAY)


def selftest():
    failures = []

    def expect(name, cond, extra=""):
        print("[%s] %s%s" % ("ok" if cond else "NG", name, (" — " + extra) if (extra and not cond) else ""))
        if not cond:
            failures.append(name)

    # 1) Action 導出表(base 写像の代表マス)
    expect("base: critical×routine → block",
           _derive(_base_finding("T", impact="critical"))["final_action"] == "block")
    expect("base: high×conditional → fix-before-merge",
           _derive(_base_finding("T", impact="high", trigger_class="conditional"))["final_action"] == "fix-before-merge")
    expect("base: medium×conditional → follow-up",
           _derive(_base_finding("T", trigger_class="conditional"))["final_action"] == "follow-up")
    expect("base: low×adversarial → omit(bucket=omit)",
           _derive(_base_finding("T", impact="low", trigger_class="adversarial"))["bucket"] == "omit")

    # 2) rare 降格は有効な context_effect + 非降格クラス外のみ
    d = _derive(_base_finding("T", rate="rare", context_effect=VALID_CE))
    expect("rare+有効CE → 1 段降格(fix-before-merge→follow-up)",
           d["action_with_context"] == "follow-up" and "rare-demotion" in d["adjustments"], str(d))
    d = _derive(_base_finding("T", rate="rare"))
    expect("rare で CE 無し → 降格しない(fail-closed)",
           d["action_with_context"] == "fix-before-merge" and not d["adjustments"], str(d))
    d = _derive(_base_finding("T", rate="rare", defect_class="destructive-data-loss",
                              context_effect=VALID_CE))
    expect("rare+CE でも非降格クラスは降格しない",
           d["action_with_context"] == "fix-before-merge" and not d["adjustments"], str(d))
    d = _derive(_base_finding("T", impact="high", trigger_class="adversarial",
                              rate="rare", context_effect=VALID_CE))
    expect("impact=high の rare 降格は follow-up 床で止まる(omit へ落とさない)",
           d["action_with_context"] == "follow-up", str(d))
    stale_ce = json.loads(json.dumps(VALID_CE))
    stale_ce["basis"][0]["as_of"] = "2026-01-01"
    d = _derive(_base_finding("T", rate="rare", context_effect=stale_ce))
    expect("as_of 90 日超過の CE は無効(降格しない)", not d["adjustments"], str(d))
    future_ce = json.loads(json.dumps(VALID_CE))
    future_ce["basis"][0]["as_of"] = "2027-01-01"
    d = _derive(_base_finding("T", rate="rare", context_effect=future_ce))
    expect("未来日 as_of の CE は無効(降格しない)", not d["adjustments"], str(d))
    shared_ce = json.loads(json.dumps(VALID_CE))
    shared_ce["basis"][0]["source_type"] = "shared-default"
    d = _derive(_base_finding("T", rate="rare", context_effect=shared_ce))
    expect("shared-default source の CE は無効(降格しない)", not d["adjustments"], str(d))

    # 3) frequent 昇格(block へは critical のみ)/ unknown 無調整
    d = _derive(_base_finding("T", trigger_class="conditional", rate="frequent"))
    expect("frequent → 1 段昇格(follow-up→fix-before-merge)",
           d["action_with_context"] == "fix-before-merge", str(d))
    d = _derive(_base_finding("T", impact="high", rate="frequent"))
    expect("frequent でも block へ上がるのは critical のみ(high は fix-before-merge 止まり)",
           d["action_with_context"] == "fix-before-merge", str(d))
    d = _derive(_base_finding("T", impact="critical", trigger_class="conditional", rate="frequent"))
    expect("critical×conditional+frequent → block",
           d["action_with_context"] == "block", str(d))
    d = _derive(_base_finding("T", rate="unknown"))
    expect("unknown は無調整(fix-before-merge のまま)",
           d["action_with_context"] == "fix-before-merge" and not d["adjustments"], str(d))

    # 4) easy-recovery 降格(非降格クラス外のみ)
    d = _derive(_base_finding("T", mitigation="easy-recovery"))
    expect("easy-recovery → 1 段降格(fix-before-merge→follow-up)",
           d["action_with_context"] == "follow-up" and "easy-recovery-demotion" in d["adjustments"], str(d))
    d = _derive(_base_finding("T", mitigation="easy-recovery", defect_class="irreversible-external-effect"))
    expect("非降格クラスは easy-recovery でも降格しない",
           d["action_with_context"] == "fix-before-merge" and not d["adjustments"], str(d))

    # 5) uncertainty ルーティング
    d = _derive(_base_finding("T", uncertainty_kind="behavior"))
    expect("behavior → clarify-spec 上書き + residual",
           d["final_action"] == "clarify-spec" and d["bucket"] == "residual", str(d))
    d = _derive(_base_finding("T", confidence="low", uncertainty_kind="evidence"))
    expect("confidence=low → residual(導出 Action は保持)",
           d["bucket"] == "residual" and d["final_action"] == "fix-before-merge", str(d))
    d = _derive(_base_finding("T", uncertainty_kind="operational"))
    expect("operational は通常導出のまま(clarify-spec にしない)",
           d["final_action"] == "fix-before-merge" and d["bucket"] == "main", str(d))

    # 6) 監査フラグ + flip count
    d = _derive(_base_finding("T", impact="high", rate="rare", sentinel=True, context_effect=VALID_CE))
    expect("sentinel 高 Impact の文脈降格 → audit_flag",
           d["audit_flag"] and d["action_without_context"] != d["action_with_context"], str(d))

    # 7) good fixture の全体描画(固定 today で決定論)
    good = os.path.join(FIXTURES_DIR, "review_good.json")
    with open(good, "r", encoding="utf-8") as fh:
        review = json.load(fh)
    text, artifacts = render(review, today=FIXED_TODAY)

    order = ["## Reviewed by", "## 新規または拡大した指摘", "## 総評",
             "## 既存の無関係な問題", "## 残留リスク / 未確認点", "## Profile gaps", "## Patch verdict"]
    positions = [text.find(h) for h in order]
    expect("セクション順が output-format.md に一致",
           all(p >= 0 for p in positions) and positions == sorted(positions), str(positions))
    expect("F-02(critical×routine)→ [P0]", "### [P0] " in text)
    expect("F-01/F-06(high×routine)→ [P1]", "### [P1] " in text)
    expect("F-03(rare 降格)→ [P2] + 適用前後を表示",
           "### [P2] " in text and "適用: fix-before-merge → follow-up" in text)
    expect("[P0] が [P1] より先に並ぶ", text.find("### [P0]") < text.find("### [P1]"))
    expect("sentinel 由来 finding に (sentinel) マーク", "(sentinel)" in text)

    main_section = text.split("## 総評")[0]
    residual_section = text.split("## 残留リスク / 未確認点")[1].split("## Profile gaps")[0]
    expect("behavior(F-04)は主要節に出ず残留リスク節へ質問として",
           "F-04" not in main_section and "F-04" in residual_section)
    expect("confidence=low(F-05)は残留リスク節へ・導出 Action を併記",
           "F-05" not in main_section and "F-05" in residual_section
           and "導出 Action: fix-before-merge" in residual_section)
    expect("クラスタ親が子 finding(F-01a)を構造化保持", "F-01a (high):" in main_section)
    expect("security_reachability を どんなときに起きるか に記録",
           "caller capability: ordinary-authenticated" in main_section)
    expect("Profile gaps を JSON Pointer + 質問で表示",
           "/facts/concurrency/duplicate_ui_submit" in text)
    expect("P0 存在時に correct 系 verdict を出さない(→incorrect)",
           "- overall: incorrect" in text)
    expect("既定では UNATTESTED を付けない", "UNATTESTED" not in text)
    expect("--unattested 相当で最終行に UNATTESTED",
           render(review, unattested=True, today=FIXED_TODAY)[0].rstrip().endswith("UNATTESTED"))
    flips = artifacts["context_action_flip_count"]
    expect("flip count = 1(F-03 の rare 降格のみ)", flips == 1, str(artifacts["derivations"]))
    recs = {r["id"]: r for r in artifacts["derivations"]}
    expect("derivation artifact に without/with の両方を保存",
           recs["F-03"]["action_without_context"] == "fix-before-merge"
           and recs["F-03"]["action_with_context"] == "follow-up")

    # 8) 指摘なし → correct
    text, _ = render({"findings": []}, today=FIXED_TODAY)
    expect("指摘なし → overall: correct", "- overall: correct" in text)
    expect("指摘なしでも各セクションを「なし」で出す", text.count("- なし") >= 3)

    # 9) follow-up 7 件 → 表示上限なしで全件フル表示(割愛行を出さない)
    fus = [_base_finding("FU-%02d" % i, trigger_class="conditional") for i in range(1, 8)]
    text, _ = render({"findings": fus}, today=FIXED_TODAY)
    expect("follow-up のみ → mostly correct", "- overall: mostly correct" in text)
    expect("follow-up 7 件を全件フル表示(上限なし)", text.count("### [P2]") == 7, text)
    expect("全件フル表示なので「他 N 件は割愛」行を出さない", "割愛" not in text, text)
    expect("フル形式なので各指摘に Action 行が付く", text.count("- Action: follow-up") == 7, text)

    # 9b) block / fix-before-merge / follow-up 混在 → Action の重い順で全件フル表示
    mixed = [_base_finding("M-blk", impact="critical"),                       # block
             _base_finding("M-fx", impact="high"),                           # fix-before-merge
             _base_finding("M-fu", trigger_class="conditional")]             # follow-up
    text, _ = render({"findings": mixed}, today=FIXED_TODAY)
    expect("混在 3 件を全件表示", text.count("### [P0]") == 1
           and text.count("### [P1]") == 1 and text.count("### [P2]") == 1, text)
    expect("並び順は block → fix-before-merge → follow-up",
           text.find("### [P0]") < text.find("### [P1]") < text.find("### [P2]"), text)

    # 10) 残留リスク 4 項目 → 表示上限なしで全件表示(集約行を出さない)
    res = [_base_finding("R-%02d" % i, uncertainty_kind="behavior") for i in range(1, 5)]
    text, _ = render({"findings": res}, today=FIXED_TODAY)
    expect("残留リスク 4 項目を全件表示(上限なし)", text.count("[要仕様確認]") == 4, text)
    expect("残留リスクも集約行を出さない", "他 1 項目は review.json" not in text, text)

    # 11) Profile gaps 4 件 → true 優先で 3 件表示 + artifact に全件
    gaps = [{"field": "/facts/scale/g%d" % i, "proposed_question": "q%d" % i,
             "action_changes_if_answered": i != 1} for i in range(4)]
    text, artifacts = render({"findings": [], "profile_gaps": gaps}, today=FIXED_TODAY)
    expect("gaps 表示 3 件 + 超過集約行", "他 1 件は profile_gaps.json" in text, text)
    expect("action_changes_if_answered: true を優先表示",
           "/facts/scale/g1" not in text.split("## Profile gaps")[1])
    expect("gaps artifact は全件保存", len(artifacts["profile_gaps"]["all"]) == 4)

    # 12) audit モード: 見出し置換・導出 omit のバックログ・audit 語彙 verdict
    text, _ = render({"findings": [_base_finding("A-01", impact="high"),
                                   _base_finding("A-02", impact="low", trigger_class="adversarial")]},
                     mode="audit", today=FIXED_TODAY)
    expect("audit → 見出しを「優先して直すべきコードベース指摘」へ置換",
           "## 優先して直すべきコードベース指摘" in text and "## 新規または拡大した指摘" not in text)
    expect("audit → Codebase audit verdict + risky",
           "## Codebase audit verdict" in text and "- overall: risky" in text)
    expect("audit → 導出 omit をバックログとして保持", "A-02" in text and "バックログ" in text)

    # 13) 通常レビューでは導出 omit を表示しない(artifact には残る)
    text, artifacts = render({"findings": [_base_finding("O-01", impact="low", trigger_class="adversarial"),
                                           _base_finding("F-10", trigger_class="conditional")]},
                             today=FIXED_TODAY)
    expect("通常レビューで導出 omit は非表示", "O-01" not in text)
    expect("omit も derivation artifact には残る(監査用)",
           any(r["id"] == "O-01" for r in artifacts["derivations"]))

    # 14) CLI: exit code と artifact 書き出し
    parse_error = os.path.join(FIXTURES_DIR, "review_parse_error.json")
    rc = subprocess.run([sys.executable, os.path.abspath(__file__), parse_error],
                        capture_output=True).returncode
    expect("CLI: parse 不能 → exit 3", rc == EXIT_PARSE, "rc=%d" % rc)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump({"target": {"repo": "x"}}, fh)
        no_findings = fh.name
    try:
        rc = subprocess.run([sys.executable, os.path.abspath(__file__), no_findings],
                            capture_output=True).returncode
        expect("CLI: findings 欠落 → exit 2", rc == EXIT_STRUCTURE, "rc=%d" % rc)
    finally:
        os.unlink(no_findings)
    with tempfile.TemporaryDirectory() as td:
        gaps_path = os.path.join(td, "profile_gaps.json")
        deriv_path = os.path.join(td, "action_derivation.json")
        rc = subprocess.run([sys.executable, os.path.abspath(__file__),
                             "--today", FIXED_TODAY.isoformat(),
                             "--gaps-out", gaps_path, "--derivation-out", deriv_path,
                             "-o", os.path.join(td, "review.md"), good],
                            capture_output=True).returncode
        expect("CLI: 良例 → exit 0", rc == EXIT_OK, "rc=%d" % rc)
        expect("CLI: --gaps-out / --derivation-out が生成される",
               os.path.isfile(gaps_path) and os.path.isfile(deriv_path))
        if os.path.isfile(deriv_path):
            with open(deriv_path, encoding="utf-8") as fh:
                deriv_data = json.load(fh)
            expect("derivation artifact に flip count を保存",
                   deriv_data.get("context_action_flip_count") == 1, str(deriv_data)[:200])

    print()
    if failures:
        print("selftest 失敗: %d 件 — %s" % (len(failures), "; ".join(failures)))
        return 1
    print("selftest 全件合格(Action 導出表・調整 1-6・P 固定写像・artifact・exit 0/2/3 を確認)")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
