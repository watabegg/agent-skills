#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""crv2 review.json 機械ゲート(validator)。

LLM が生成した review.json(正本: ../schemas/review.schema.json)を検査し、
出力契約への違反を exit code に拘束する。Action 導出は render_review.py の
derive_action を import して共用する(写像の正本は references/output-format.md、
二重実装しない)。profile の読み取りに PyYAML を必須とし、import できない場合は
exit 3 で「PyYAML 必須」と明示する(正規表現フォールバックは廃止。oracle §2(d))。

チェック一覧(コード → 内容):
  REVIEW_SCHEMA          スキーマ準拠(型・enum・pattern・additionalProperties 等)。
                         action / P / severity / verdict の直書きキーもここで弾く
                         (Action・P・verdict・件数は renderer が固定写像で機械導出する)。
  FIELD_MISSING          全指摘に統一必須のフィールド欠落(impact / exposure / mitigation /
                         confidence / defect_class / uncertainty / sentinel / trigger /
                         evidence / counterevidence_checked / invariant / symptom_or_cause)。
                         P0/P1 だけ重くすると P2 へ逃げるため全件同一要求。
  ACTION_CONFIDENCE      confidence_evidence_type=inference(推測)の指摘が導出後に
                         block / fix-before-merge として表示される場合は error
                         (confidence=low なら renderer が残留リスクへ移すため対象外)。
  CONTEXT_DEMOTION_UNSUPPORTED
                         operational_rate=rare の降格主張の根拠不備(oracle §1.4 / 盲点B):
                         context_effect 不在 / basis の profile_field が resolved profile に
                         実在しない / /facts/ 配下でない / profile 側の値が unknown・<要確認> /
                         source_type が降格不適格(telemetry / user-confirmed / incident-review
                         以外)/ profile 側の source・as_of と不一致 / as_of が未来日・90 日超過 /
                         profile が missing・draft・stale・expired。ゴム印降格を機械拒否する。
  NON_DEMOTABLE_VIOLATION
                         defect_class が非降格クラス(authz-boundary / destructive-data-loss /
                         irreversible-external-effect / production-credential + profile 追加分)
                         なのに context_effect で降格を主張している(環境・規模で降格不能)。
  ENV_AS_AUTHZ_BASIS     /facts/environment/* を authz 系・データ露出系指摘の降格根拠に
                         使っている(VPN / SSO / 社内は認可制御の代替にならない。oracle 盲点D)。
  CLUSTER_ORPHAN         cluster_id の参照切れ(children を持つ cause 親が不在)等。
  CLUSTER_UNRELATED      同一 cluster 内の指摘が同一パス prefix の根拠を 1 つも共有しない
                         (無関係な指摘の束ね疑い)。warning として coordinator が再判定。
  PROFILE_GAP_DUP        profile_gaps 内で同じ field への質問が dedupe されていない(warning)。
  FORBIDDEN_TERM         a11y 探索の再生産防止。無曖昧語(aria- / role=" / スクリーンリーダー /
                         支援技術 / accessible name / WCAG)= error、多義語(コントラスト /
                         キーボード / フォーカス)= warning。resolved profile の
                         review_risk_policy.a11y_review_by_default: true か --allow-a11y で全スキップ。
  SECURITY_REACHABILITY  security カテゴリと推定される指摘に security_reachability が無い。
  COUNT_INFO             findings 総数が目安(12 件)超のとき出す warning(非ブロッキング)。
                         ノイズ回帰の早期シグナル用で、出力は妨げない(exit code を拘束しない)。

表示件数の上限検査(error)は行わない。検証を通過した指摘は renderer が全件フル表示する
(表示制御は renderer の責務。findings には文脈適用で omit/降格された候補も監査のため全件残す)。
件数は COUNT_INFO(warning)で監視するだけで、切り詰め・要約はしない。

violation 出力: 1 行 1 件「<CODE>\t<location>\t<detail>」。warning は detail 先頭に
「warning:」。--json で構造化出力。

exit code: 0 = 合格(warning のみ含む) / 2 = violation(error あり) / 3 = parse 不能・環境不備。

使い方:
  python3 validate_review.py --profile <resolved-profile.yaml> review.json
  python3 validate_review.py --json --mode audit --today 2026-07-12 review.json
  python3 validate_review.py --selftest
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

EXIT_OK = 0
EXIT_VIOLATION = 2
EXIT_PARSE = 3

try:
    import yaml
except ImportError:  # 正規表現フォールバックは廃止(oracle §2(d))
    print("error: PyYAML 必須(pip install pyyaml)。profile を strict parse できないため"
          "検証を続行しない(fail-closed)", file=sys.stderr)
    sys.exit(EXIT_PARSE)

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
try:
    from render_review import (  # Action 導出の共用(写像の二重実装を禁止)
        ORG_NON_DEMOTABLE,
        DEMOTION_SOURCE_TYPES,
        FRESHNESS_DAYS,
        derive_action,
        _parse_date,
    )
except ImportError:
    print("error: render_review.py を import できない(同一ディレクトリに必要)。"
          "Action 導出写像を共有できないため検証を続行しない", file=sys.stderr)
    sys.exit(EXIT_PARSE)

DEFAULT_SCHEMA = os.path.join(_HERE, "..", "schemas", "review.schema.json")
FIXTURES_DIR = os.path.join(_HERE, "fixtures")

# 全指摘に統一必須のフィールド(P0/P1 だけ重くしない)
UNIFIED_FIELDS = [
    "impact",
    "exposure",
    "mitigation",
    "confidence",
    "defect_class",
    "uncertainty",
    "sentinel",
    "trigger",
    "evidence",
    "counterevidence_checked",
    "invariant",
    "symptom_or_cause",
]

# renderer が機械導出するためモデルが直書きしてはならないキー(小文字比較)
DERIVED_ONLY_KEYS = {"action", "p", "severity", "priority", "verdict", "推奨"}

# findings 総数のノイズ回帰シグナル(非ブロッキング warning)。表示は切らないが、
# 生成側のノイズ抑制が効かず件数が膨らんだ場合の早期シグナルとして COUNT_INFO を出す。
COUNT_INFO_THRESHOLD = 12

# a11y 無曖昧語(error)と多義語(warning)
A11Y_ERROR_TERMS = ["aria-", 'role="', "スクリーンリーダー", "支援技術", "accessible name", "wcag"]
A11Y_WARNING_TERMS = ["コントラスト", "キーボード", "フォーカス"]

# security カテゴリの近似判定語彙(schema に category が無いための近似)
SECURITY_HINT_TERMS = [
    "認可",
    "認証",
    "権限",
    "セキュリティ",
    "security",
    "authz",
    "authn",
    "authorization",
    "authentication",
    "unauthenticated",
    "unauthorized",
    "xss",
    "csrf",
    "インジェクション",
    "injection",
    "漏えい",
    "漏洩",
    "credential",
    "資格情報",
    "秘密鍵",
    "なりすまし",
    "テナント越境",
]

# ENV_AS_AUTHZ_BASIS の対象: environment を根拠にできない defect_class(非降格 4 クラスに加え
# データ露出につながるクラス)。security_reachability を持つ指摘・security 語彙の指摘も対象。
ENV_SENSITIVE_CLASSES = set(ORG_NON_DEMOTABLE) | {"data-integrity"}


class Violation(object):
    def __init__(self, code, location, detail, severity="error"):
        self.code = code
        self.location = location
        self.detail = detail
        self.severity = severity  # "error" | "warning"

    def as_line(self):
        detail = self.detail if self.severity == "error" else "warning: " + self.detail
        return "%s\t%s\t%s" % (self.code, self.location, detail)

    def as_dict(self):
        return {
            "code": self.code,
            "location": self.location,
            "detail": self.detail,
            "severity": self.severity,
        }


# ---------------------------------------------------------------------------
# JSON Schema(draft-07 サブセット)ミニ検証器
# review.schema.json が使う語彙のみ対応: type(union 含む) / required / properties /
# additionalProperties(false) / enum / pattern / minLength / minItems / minimum /
# items / $ref(#/definitions/...)
# ---------------------------------------------------------------------------

def _resolve_ref(root, ref):
    node = root
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    return node


def _type_ok(value, tname):
    if tname == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if tname == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    mapping = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
    expected = mapping.get(tname)
    return expected is not None and isinstance(value, expected)


def schema_check(root, schema, value, path, out):
    """out に (path, kind, detail) タプルを追記する。"""
    if "$ref" in schema:
        schema = _resolve_ref(root, schema["$ref"])

    stype = schema.get("type")
    if stype is not None:
        types = stype if isinstance(stype, list) else [stype]
        if not any(_type_ok(value, t) for t in types):
            out.append((path, "type", "型が %s でない(実際: %s)" % ("/".join(types), type(value).__name__)))
            return

    if value is None:
        return

    if "enum" in schema and value not in schema["enum"]:
        out.append((path, "enum", "値 %r は %s に含まれない" % (value, "/".join(map(str, schema["enum"])))))

    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append((path, "pattern", "値 %r が pattern %s に一致しない" % (value, schema["pattern"])))
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.append((path, "minLength", "空文字または短すぎる"))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append((path, "minimum", "値 %s が最小値 %s 未満" % (value, schema["minimum"])))

    if isinstance(value, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in value:
                out.append((path, "required", req))
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    out.append((path, "additionalProperties", key))
        for key, sub in props.items():
            if key in value:
                schema_check(root, sub, value[key], path + "." + key if path else key, out)

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append((path, "minItems", "要素数 %d は最小 %d 未満" % (len(value), schema["minItems"])))
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(value):
                schema_check(root, items, item, "%s[%d]" % (path, i), out)


# ---------------------------------------------------------------------------
# profile の読み取り(strict YAML。status / 鮮度 / non_demotable / a11y)
# ---------------------------------------------------------------------------

def load_profile(profile_path, today, source_kind="unknown"):
    """resolved profile を読み、(data, meta) を返す。

    meta = {"status": ..., "usable": bool, "reason": str}
    usable=False の profile の事実は降格根拠に使えない(fail-closed)。
    """
    if not profile_path or not os.path.exists(profile_path):
        return None, {"status": "missing", "usable": False,
                      "reason": "resolved profile が無い"}
    try:
        with open(profile_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        return None, {"status": "invalid", "usable": False,
                      "reason": "profile を parse できない: %s" % exc}
    if not isinstance(data, dict):
        return None, {"status": "invalid", "usable": False,
                      "reason": "profile のルートがオブジェクトでない"}

    md = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    status = str(md.get("status") or "unknown")
    reason = ""
    if status == "approved":
        review_by = _parse_date(md.get("review_by"))
        approved_at = _parse_date(md.get("approved_at"))
        if review_by is None:
            status, reason = "draft", "review_by が無い approved は draft 扱い"
        elif review_by < today:
            status, reason = "stale", "review_by(%s)超過" % review_by.isoformat()
        elif approved_at is not None and approved_at > today:
            status, reason = "draft", "approved_at(%s)が未来日" % approved_at.isoformat()
    usable = status == "approved" and source_kind != "shared-default"
    if source_kind == "shared-default":
        reason = "shared-default profile は降格根拠に使えない"
    if not usable and not reason:
        reason = "status=%s は降格根拠に使えない(approved のみ可)" % status
    return data, {"status": status, "usable": usable, "reason": reason}


def profile_non_demotable(profile_data):
    """組織必須 4 クラス + profile の review_risk_policy.non_demotable 追加分。"""
    classes = list(ORG_NON_DEMOTABLE)
    if isinstance(profile_data, dict):
        policy = profile_data.get("review_risk_policy")
        if isinstance(policy, dict) and isinstance(policy.get("non_demotable"), list):
            for c in policy["non_demotable"]:
                if isinstance(c, str) and c not in classes:
                    classes.append(c)
    return tuple(classes)


def profile_a11y_enabled(profile_data):
    if not isinstance(profile_data, dict):
        return False
    policy = profile_data.get("review_risk_policy")
    if isinstance(policy, dict):
        return policy.get("a11y_review_by_default") is True
    return False


def _resolve_pointer(data, pointer):
    """JSON Pointer を profile dict 上で解決する。不在は (False, None)。"""
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        return False, None
    node = data
    for raw in pointer[1:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and key in node:
            node = node[key]
        elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
            node = node[int(key)]
        else:
            return False, None
    return True, node


# ---------------------------------------------------------------------------
# 個別チェック
# ---------------------------------------------------------------------------

def _finding_loc(index, finding, suffix=""):
    fid = finding.get("id") if isinstance(finding, dict) else None
    base = "findings[%d]" % index
    if isinstance(fid, str) and fid:
        base += "(%s)" % fid
    return base + suffix


def _iter_findings(review):
    findings = review.get("findings")
    if not isinstance(findings, list):
        return []
    return [(i, f) for i, f in enumerate(findings) if isinstance(f, dict)]


def check_schema(review, schema, violations):
    raw = []
    schema_check(schema, schema, review, "", raw)
    for path, kind, detail in raw:
        # findings[i] 直下の統一必須フィールド欠落は FIELD_MISSING として報告する
        m = re.match(r"^findings\[(\d+)\]$", path)
        if kind == "required" and m and detail in UNIFIED_FIELDS:
            idx = int(m.group(1))
            finding = review["findings"][idx]
            violations.append(Violation(
                "FIELD_MISSING", _finding_loc(idx, finding),
                "統一必須フィールド %s が無い(全指摘同一要求)" % detail))
            continue
        # findings[i].evidence の空配列も FIELD_MISSING(根拠なし)として報告する
        m = re.match(r"^findings\[(\d+)\]\.evidence$", path)
        if kind == "minItems" and m:
            idx = int(m.group(1))
            finding = review["findings"][idx]
            violations.append(Violation(
                "FIELD_MISSING", _finding_loc(idx, finding, ".evidence"),
                "evidence が空。根拠のコード位置を 1 件以上書く"))
            continue
        # action / P / verdict 等の直書きは専用メッセージで弾く
        if kind == "additionalProperties" and str(detail).lower() in DERIVED_ONLY_KEYS:
            violations.append(Violation(
                "REVIEW_SCHEMA", path or "(root)",
                "キー %r はモデルが書かない。Action・P・verdict・件数は renderer が"
                " references/output-format.md の固定写像で機械導出する" % detail))
            continue
        loc = path or "(root)"
        if kind == "required":
            msg = "必須フィールド %s が無い" % detail
        elif kind == "additionalProperties":
            msg = "スキーマ外のキー %r がある" % detail
        else:
            msg = detail
        violations.append(Violation("REVIEW_SCHEMA", loc, msg))


def check_action_confidence(review, violations, non_demotable, today):
    """推測(inference)の指摘が block / fix-before-merge として表示される場合は error。"""
    for idx, f in _iter_findings(review):
        deriv = derive_action(f, non_demotable, today)
        if deriv is None:
            continue  # 構造不備は REVIEW_SCHEMA / FIELD_MISSING が報告する
        if (f.get("confidence_evidence_type") == "inference"
                and deriv["bucket"] == "main"
                and deriv["final_action"] in ("block", "fix-before-merge")):
            violations.append(Violation(
                "ACTION_CONFIDENCE", _finding_loc(idx, f),
                "根拠種別が推測(inference)の指摘が %s に導出される。コードトレースか再現で"
                "裏取りするか、uncertainty を正しく分類する(behavior→clarify-spec / "
                "evidence→confidence: low)" % deriv["final_action"]))


def _basis_violations(basis, profile_data, profile_meta, today):
    """context_effect.basis 1 件分の降格適格性検査。detail 文字列のリストを返す。"""
    problems = []
    field = basis.get("profile_field")

    if not profile_meta["usable"]:
        problems.append("resolved profile が降格根拠に使えない状態(%s: %s)"
                        % (profile_meta["status"], profile_meta["reason"]))
        return problems  # profile 全体が不可なら個別照合は行わない

    if not isinstance(field, str) or not field.startswith("/facts/"):
        problems.append("profile_field %r は /facts/(運用事実)配下でない" % field)
        return problems

    exists, node = _resolve_pointer(profile_data, field)
    if not exists:
        problems.append("profile_field %s が resolved profile に実在しない" % field)
        return problems

    src = basis.get("source_type")
    if src not in DEMOTION_SOURCE_TYPES:
        problems.append("source_type %r は降格不適格(%s のみ可)"
                        % (src, " / ".join(DEMOTION_SOURCE_TYPES)))

    as_of = _parse_date(basis.get("as_of"))
    if as_of is None:
        problems.append("as_of %r を日付として解釈できない" % basis.get("as_of"))
    elif as_of > today:
        problems.append("as_of %s が未来日(未来の事実は降格根拠にならない)" % as_of.isoformat())
    elif (today - as_of).days > FRESHNESS_DAYS:
        problems.append("as_of %s が鮮度ウィンドウ(%d 日)超過" % (as_of.isoformat(), FRESHNESS_DAYS))

    # profile 側の事実と引用の一致(偽装防止)。facts の葉は {value, source, as_of, ...} 構造。
    if isinstance(node, dict) and "value" in node:
        value = node.get("value")
        if value is None or (isinstance(value, str) and
                             (value.strip() == "unknown" or "<要確認>" in value)):
            problems.append("profile_field %s の値が unknown / <要確認>(fail-closed で降格不可)" % field)
        p_src = node.get("source")
        if isinstance(p_src, str) and src and p_src != src:
            problems.append("source_type %r が profile 側の source %r と一致しない" % (src, p_src))
        elif not isinstance(p_src, str) or p_src == "unknown":
            problems.append("profile_field %s に降格適格な source 記録が無い" % field)
        p_as_of = _parse_date(node.get("as_of"))
        if p_as_of is None:
            problems.append("profile_field %s に as_of 記録が無い(鮮度を検証できない)" % field)
        elif as_of is not None and p_as_of != as_of:
            problems.append("as_of %s が profile 側の記録 %s と一致しない"
                            % (as_of.isoformat(), p_as_of.isoformat()))
    else:
        problems.append("profile_field %s は {value, source, as_of} 構造の事実でない" % field)
    return problems


def _is_security_like(f):
    if isinstance(f.get("security_reachability"), dict):
        return True
    if f.get("defect_class") in ("authz-boundary", "production-credential"):
        return True
    texts = [f.get("title"), f.get("trigger")]
    inv = f.get("invariant")
    if isinstance(inv, dict):
        texts.append(inv.get("text"))
    blob = " ".join(t for t in texts if isinstance(t, str)).lower()
    return any(term in blob for term in SECURITY_HINT_TERMS)


def check_context_demotion(review, violations, profile_data, profile_meta,
                           non_demotable, today):
    """CONTEXT_DEMOTION_UNSUPPORTED / NON_DEMOTABLE_VIOLATION / ENV_AS_AUTHZ_BASIS。"""
    nd_set = set(non_demotable)
    for idx, f in _iter_findings(review):
        ce = f.get("context_effect")
        exposure = f.get("exposure") if isinstance(f.get("exposure"), dict) else {}
        rate = exposure.get("operational_rate")
        is_non_demotable = f.get("defect_class") in nd_set

        if isinstance(ce, dict) and is_non_demotable:
            violations.append(Violation(
                "NON_DEMOTABLE_VIOLATION", _finding_loc(idx, f),
                "defect_class=%s は非降格クラス。環境(VPN/SSO/社内)・規模・利用実態を"
                "理由に降格できないため context_effect を書かない" % f.get("defect_class")))

        # ENV_AS_AUTHZ_BASIS: environment 系フィールドを authz 系・データ露出系の根拠に使用
        if isinstance(ce, dict):
            for b in ce.get("basis") or []:
                if not isinstance(b, dict):
                    continue
                field = str(b.get("profile_field", ""))
                if not field.startswith("/facts/environment"):
                    continue
                if is_non_demotable or f.get("defect_class") in ENV_SENSITIVE_CLASSES \
                        or _is_security_like(f):
                    violations.append(Violation(
                        "ENV_AS_AUTHZ_BASIS", _finding_loc(idx, f),
                        "environment 系フィールド %s を authz 系・データ露出系指摘の降格根拠に"
                        "使えない(ネットワーク位置は認可制御の代替にならない。Zero Trust)" % field))

        if rate != "rare":
            continue

        # operational_rate=rare は降格主張: 有効な context_effect が必須
        if not isinstance(ce, dict):
            violations.append(Violation(
                "CONTEXT_DEMOTION_UNSUPPORTED", _finding_loc(idx, f),
                "operational_rate=rare なのに context_effect が無い。rare は fresh な "
                "profile 事実の引用(basis + causal_link + counterfactual)がないと主張できない"
                "(事実が無ければ unknown にする)"))
            continue
        if is_non_demotable:
            continue  # NON_DEMOTABLE_VIOLATION 側で報告済み(basis 照合は行わない)
        basis_list = ce.get("basis") if isinstance(ce.get("basis"), list) else []
        if not basis_list:
            violations.append(Violation(
                "CONTEXT_DEMOTION_UNSUPPORTED", _finding_loc(idx, f, ".context_effect"),
                "basis が空。降格には実在する profile 事実の引用が必須"))
            continue
        for bi, b in enumerate(basis_list):
            if not isinstance(b, dict):
                continue
            for problem in _basis_violations(b, profile_data, profile_meta, today):
                violations.append(Violation(
                    "CONTEXT_DEMOTION_UNSUPPORTED",
                    _finding_loc(idx, f, ".context_effect.basis[%d]" % bi),
                    problem))
        if not str(ce.get("counterfactual") or "").strip():
            violations.append(Violation(
                "CONTEXT_DEMOTION_UNSUPPORTED", _finding_loc(idx, f, ".context_effect"),
                "counterfactual(どんな観測があれば降格を取り消すか)が無い"))


def check_clusters(review, violations):
    findings = _iter_findings(review)
    members = {}
    for idx, f in findings:
        cid = f.get("cluster_id")
        if isinstance(cid, str) and cid:
            members.setdefault(cid, []).append((idx, f))

    for idx, f in findings:
        if isinstance(f.get("children"), list) and f["children"]:
            if not f.get("cluster_id"):
                violations.append(Violation(
                    "CLUSTER_ORPHAN", _finding_loc(idx, f),
                    "children を持つクラスタ親に cluster_id が無い"))
            if f.get("symptom_or_cause") != "cause":
                violations.append(Violation(
                    "CLUSTER_ORPHAN", _finding_loc(idx, f),
                    "children を持つクラスタ親は symptom_or_cause=cause でなければならない"))

    for cid, group in sorted(members.items()):
        parents = [(idx, f) for idx, f in group
                   if isinstance(f.get("children"), list) and f["children"]]
        if not parents:
            locs = ", ".join(_finding_loc(idx, f) for idx, f in group)
            violations.append(Violation(
                "CLUSTER_ORPHAN", locs,
                "cluster_id=%s に children を保持する cause 親がいない(参照切れ)。"
                "子 finding の id / impact / trigger を親に構造化保持する" % cid))

        # 同一パス prefix 近似: 「同一機構」の意味判定は機械化できないため、
        # cluster 構成員の evidence パスが prefix を 1 つも共有しなければ warning
        if len(group) >= 2:
            prefix_sets = []
            for _, f in group:
                prefixes = set()
                for ev in f.get("evidence") or []:
                    if not isinstance(ev, dict):
                        continue
                    parts = str(ev.get("path", "")).split("/")
                    for depth in range(1, len(parts)):
                        prefixes.add("/".join(parts[:depth]))
                prefix_sets.append(prefixes)
            shared = set.intersection(*prefix_sets) if prefix_sets else set()
            if not shared:
                violations.append(Violation(
                    "CLUSTER_UNRELATED", "cluster:%s" % cid,
                    "同一クラスタの指摘が同一パス prefix の根拠を 1 つも共有していない。"
                    "無関係な指摘の束ねでないか(1 つの修正機構で全子症状が消えるか)を再判定する",
                    severity="warning"))


def check_profile_gaps(review, violations):
    gaps = review.get("profile_gaps")
    if not isinstance(gaps, list):
        return
    seen = {}
    for i, g in enumerate(gaps):
        if not isinstance(g, dict):
            continue
        field = g.get("field")
        if isinstance(field, str) and field in seen:
            violations.append(Violation(
                "PROFILE_GAP_DUP", "profile_gaps[%d]" % i,
                "field %s への質問が profile_gaps[%d] と重複している(同じ欠落フィールドへの"
                "質問は 1 件に dedupe する)" % (field, seen[field]),
                severity="warning"))
        elif isinstance(field, str):
            seen[field] = i


def _finding_texts(f):
    texts = []
    for key in ("title", "trigger"):
        v = f.get(key)
        if isinstance(v, str):
            texts.append((key, v))
    inv = f.get("invariant")
    if isinstance(inv, dict) and isinstance(inv.get("text"), str):
        texts.append(("invariant.text", inv["text"]))
    ce = f.get("counterevidence_checked")
    if isinstance(ce, dict):
        for key in ("hypothesis", "where_checked"):
            if isinstance(ce.get(key), str):
                texts.append(("counterevidence_checked." + key, ce[key]))
    for i, child in enumerate(f.get("children") or []):
        if isinstance(child, dict) and isinstance(child.get("trigger"), str):
            texts.append(("children[%d].trigger" % i, child["trigger"]))
    return texts


def check_forbidden_terms(review, violations, a11y_enabled):
    if a11y_enabled:
        return  # profile で a11y レビューが有効なら全スキップ

    def scan(loc, text):
        lowered = text.lower()
        for term in A11Y_ERROR_TERMS:
            if term in lowered:
                violations.append(Violation(
                    "FORBIDDEN_TERM", loc,
                    "a11y 無曖昧語 %r を含む。既定では a11y 指摘は出力しない(profile の "
                    "review_risk_policy.a11y_review_by_default か明示 focus でのみ有効)" % term))
        for term in A11Y_WARNING_TERMS:
            if term in text:
                violations.append(Violation(
                    "FORBIDDEN_TERM", loc,
                    "多義語 %r を含む。a11y 文脈なら削除、業務ブロック(Operational UX クラス3)なら"
                    "そのまま残す — coordinator が再判定する" % term,
                    severity="warning"))

    for idx, f in _iter_findings(review):
        for field, text in _finding_texts(f):
            scan(_finding_loc(idx, f, "." + field), text)
    risks = review.get("residual_risks")
    if isinstance(risks, list):
        for i, r in enumerate(risks):
            if not isinstance(r, dict):
                continue
            for key in ("title", "check_procedure"):
                if isinstance(r.get(key), str):
                    scan("residual_risks[%d].%s" % (i, key), r[key])


def check_finding_count(review, violations):
    """findings 総数が閾値超のとき COUNT_INFO(warning, 非ブロッキング)を出す。

    表示は切らない(全件フル表示は renderer の責務)。これはノイズ回帰の早期シグナルで、
    出力そのものは妨げない。旧 COUNT_LIMIT(error)とは異なり exit code を拘束しない。
    """
    findings = review.get("findings")
    total = len(findings) if isinstance(findings, list) else 0
    if total > COUNT_INFO_THRESHOLD:
        violations.append(Violation(
            "COUNT_INFO", "findings",
            "findings 総数 %d 件が目安 %d 件を超過。表示は全件フルのままだが、生成側の"
            "ノイズ抑制が効いているか確認する(ノイズ回帰の早期シグナル。ブロックはしない)"
            % (total, COUNT_INFO_THRESHOLD),
            severity="warning"))


def check_security_reachability(review, violations):
    for idx, f in _iter_findings(review):
        if isinstance(f.get("security_reachability"), dict):
            continue  # サブフィールドの必須はスキーマ検査が担う
        # 誤検知を抑えるため判定対象は title / trigger / invariant.text に限定する
        texts = [f.get("title"), f.get("trigger")]
        inv = f.get("invariant")
        if isinstance(inv, dict):
            texts.append(inv.get("text"))
        blob = " ".join(t for t in texts if isinstance(t, str)).lower()
        hit = next((term for term in SECURITY_HINT_TERMS if term in blob), None)
        if hit or f.get("defect_class") in ("authz-boundary", "production-credential"):
            violations.append(Violation(
                "SECURITY_REACHABILITY", _finding_loc(idx, f),
                "security カテゴリと推定される指摘(%s)に security_reachability"
                "(caller_capability / reachability)が無い"
                % ("語 %r を含む" % hit if hit else "defect_class=%s" % f.get("defect_class"))))


# ---------------------------------------------------------------------------
# 実行本体
# ---------------------------------------------------------------------------

def validate(review, schema, mode="review", a11y_enabled=False,
             profile_data=None, profile_meta=None, today=None):
    today = today or datetime.date.today()
    if profile_meta is None:
        profile_meta = {"status": "missing", "usable": False,
                        "reason": "resolved profile が無い"}
    non_demotable = profile_non_demotable(profile_data)
    violations = []
    check_schema(review, schema, violations)
    check_action_confidence(review, violations, non_demotable, today)
    check_context_demotion(review, violations, profile_data, profile_meta,
                           non_demotable, today)
    check_clusters(review, violations)
    check_profile_gaps(review, violations)
    check_forbidden_terms(review, violations, a11y_enabled)
    check_security_reachability(review, violations)
    check_finding_count(review, violations)
    return violations


def run(argv):
    parser = argparse.ArgumentParser(
        description="crv2 review.json validator(exit 0=合格 / 2=violation / 3=parse不能)")
    parser.add_argument("review", nargs="?", help="review.json のパス")
    parser.add_argument("--schema", default=DEFAULT_SCHEMA, help="review.schema.json のパス")
    parser.add_argument("--profile", default=None,
                        help="resolved product profile YAML(run.py が base 版解決済みのものを渡す。"
                             "無い場合は fail-closed: 全 context_effect 降格を拒否)")
    parser.add_argument(
        "--profile-source-kind",
        choices=["repo", "repo-base", "agents-md", "shared-default", "unknown"],
        default="unknown",
        help="resolved profile provenance; shared-default is never usable for demotion",
    )
    parser.add_argument("--mode", choices=["review", "audit"], default="review",
                        help="review=通常レビュー(既定) / audit=codebase audit(renderer の表示切替と対で保持)")
    parser.add_argument("--today", default=None, metavar="YYYY-MM-DD",
                        help="as_of 鮮度・status 判定の基準日(selftest / 再現用。既定は実行日)")
    parser.add_argument("--allow-a11y", action="store_true",
                        help="ユーザーの明示 focus 時に FORBIDDEN_TERM 検査を無効化する")
    parser.add_argument("--json", action="store_true", help="構造化 JSON で出力する")
    parser.add_argument("--selftest", action="store_true",
                        help="fixtures で exit 0/2/3 と各チェックコードを自己検証する")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    if not args.review:
        print("error: review.json のパスを指定する(または --selftest)", file=sys.stderr)
        return EXIT_PARSE

    today = datetime.date.today()
    if args.today:
        parsed = _parse_date(args.today)
        if parsed is None:
            print("error: --today は YYYY-MM-DD 形式で指定する", file=sys.stderr)
            return EXIT_PARSE
        today = parsed

    try:
        with open(args.schema, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
    except (OSError, ValueError) as exc:
        print("error: スキーマを読めない: %s" % exc, file=sys.stderr)
        return EXIT_PARSE

    try:
        with open(args.review, "r", encoding="utf-8") as fh:
            review = json.load(fh)
    except (OSError, ValueError) as exc:
        print("error: review.json を parse できない: %s" % exc, file=sys.stderr)
        return EXIT_PARSE

    if not isinstance(review, dict):
        print("error: review.json のルートがオブジェクトでない", file=sys.stderr)
        return EXIT_PARSE

    profile_data, profile_meta = load_profile(
        args.profile, today, source_kind=args.profile_source_kind
    )
    a11y_enabled = args.allow_a11y or profile_a11y_enabled(profile_data)
    violations = validate(review, schema, mode=args.mode, a11y_enabled=a11y_enabled,
                          profile_data=profile_data, profile_meta=profile_meta, today=today)
    errors = [v for v in violations if v.severity == "error"]
    warnings = [v for v in violations if v.severity == "warning"]
    exit_code = EXIT_VIOLATION if errors else EXIT_OK

    if args.json:
        print(json.dumps({
            "ok": not errors,
            "mode": args.mode,
            "a11y_enabled": a11y_enabled,
            "profile_status": profile_meta["status"],
            "profile_demotion_usable": profile_meta["usable"],
            "errors": len(errors),
            "warnings": len(warnings),
            "exit_code": exit_code,
            "violations": [v.as_dict() for v in violations],
        }, ensure_ascii=False, indent=2))
    else:
        for v in violations:
            print(v.as_line())
    return exit_code


# ---------------------------------------------------------------------------
# selftest: fixtures で exit 0 / 2 / 3 と各チェックコードを確認する
# ---------------------------------------------------------------------------

FIXTURE_TODAY = "2026-07-12"  # fixtures の as_of / review_by が期限内になる基準日
FIXTURE_PROFILE = os.path.join(FIXTURES_DIR, "product-profile.approved.yaml")


def _run_cli(args):
    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__)] + args,
        capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def selftest():
    good = os.path.join(FIXTURES_DIR, "review_good.json")
    bad_fields = os.path.join(FIXTURES_DIR, "review_bad_fields.json")
    bad_contract = os.path.join(FIXTURES_DIR, "review_bad_contract.json")
    parse_error = os.path.join(FIXTURES_DIR, "review_parse_error.json")
    base_args = ["--today", FIXTURE_TODAY, "--profile", FIXTURE_PROFILE]
    failures = []

    def expect(name, cond, extra=""):
        status = "ok" if cond else "NG"
        print("[%s] %s%s" % (status, name, (" — " + extra) if (extra and not cond) else ""))
        if not cond:
            failures.append(name)

    def codes_of(stdout):
        data = json.loads(stdout)
        return (
            {v["code"] for v in data["violations"] if v["severity"] == "error"},
            {v["code"] for v in data["violations"] if v["severity"] == "warning"},
        )

    # 1) 良例: exit 0・violation なし(approved profile + 期限内 as_of)
    rc, out, err = _run_cli(["--json"] + base_args + [good])
    expect("良例 review_good.json → exit 0", rc == EXIT_OK, "rc=%d %s" % (rc, err[:200]))
    data = json.loads(out) if out else {"violations": ["parse-failed"]}
    expect("良例 → violation 0 件", not data.get("violations"), str(data.get("violations"))[:300])

    # 2) 違反例1(スキーマ/フィールド): exit 2 と期待コード
    rc, out, _ = _run_cli(["--json"] + base_args + [bad_fields])
    expect("違反例 review_bad_fields.json → exit 2", rc == EXIT_VIOLATION, "rc=%d" % rc)
    errs, warns = codes_of(out)
    expect("違反例1 → REVIEW_SCHEMA(action / P 直書き・enum・旧 exposure 形式)",
           "REVIEW_SCHEMA" in errs, str(errs))
    expect("違反例1 → FIELD_MISSING(defect_class / uncertainty / sentinel 等)",
           "FIELD_MISSING" in errs, str(errs))
    expect("違反例1 → ACTION_CONFIDENCE(推測が block に導出される)",
           "ACTION_CONFIDENCE" in errs, str(errs))
    rc, out, _ = _run_cli(["--json"] + base_args + [bad_fields])
    expect("違反例1 → action 直書きの専用メッセージ",
           "モデルが書かない" in out, out[:300])

    # 3) 違反例2(降格契約): exit 2 と期待コード
    rc, out, _ = _run_cli(["--json"] + base_args + [bad_contract])
    expect("違反例 review_bad_contract.json → exit 2", rc == EXIT_VIOLATION, "rc=%d" % rc)
    errs, warns = codes_of(out)
    for code in ("CONTEXT_DEMOTION_UNSUPPORTED", "NON_DEMOTABLE_VIOLATION",
                 "ENV_AS_AUTHZ_BASIS", "CLUSTER_ORPHAN", "FORBIDDEN_TERM",
                 "SECURITY_REACHABILITY"):
        expect("違反例2 → %s (error)" % code, code in errs, str(errs))
    for code in ("CLUSTER_UNRELATED", "FORBIDDEN_TERM", "PROFILE_GAP_DUP"):
        expect("違反例2 → %s (warning)" % code, code in warns, str(warns))
    data = json.loads(out)
    g_details = [v["detail"] for v in data["violations"]
                 if v["code"] == "CONTEXT_DEMOTION_UNSUPPORTED"]
    expect("違反例2 → rare + context_effect 不在を検出",
           any("context_effect が無い" in d for d in g_details), str(g_details)[:300])
    expect("違反例2 → 実在しない profile_field を検出",
           any("実在しない" in d for d in g_details), str(g_details)[:300])
    expect("違反例2 → shared-default source_type を拒否",
           any("降格不適格" in d for d in g_details), str(g_details)[:300])
    expect("違反例2 → 未来日 as_of を拒否",
           any("未来日" in d for d in g_details), str(g_details)[:300])
    expect("違反例2 → 鮮度ウィンドウ超過 as_of を拒否",
           any("超過" in d for d in g_details), str(g_details)[:300])

    # 4) parse 不能: exit 3
    rc, _, _ = _run_cli(base_args + [parse_error])
    expect("parse 不能 review_parse_error.json → exit 3", rc == EXIT_PARSE, "rc=%d" % rc)

    # 5) fail-closed: profile 無しでは context_effect 降格を全拒否(良例でも exit 2)
    rc, out, _ = _run_cli(["--json", "--today", FIXTURE_TODAY, good])
    errs, _w = codes_of(out)
    expect("profile 無し → 良例の context_effect も CONTEXT_DEMOTION_UNSUPPORTED(fail-closed)",
           rc == EXIT_VIOLATION and "CONTEXT_DEMOTION_UNSUPPORTED" in errs, str(errs))

    # 6) stale profile(review_by 超過)も降格根拠に使えない
    with open(FIXTURE_PROFILE, "r", encoding="utf-8") as fh:
        stale_profile = yaml.safe_load(fh)
    stale_profile["metadata"]["review_by"] = "2026-06-01"
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
        yaml.safe_dump(stale_profile, fh, allow_unicode=True)
        stale_path = fh.name
    try:
        rc, out, _ = _run_cli(["--json", "--today", FIXTURE_TODAY, "--profile", stale_path, good])
        data = json.loads(out)
        expect("stale profile(review_by 超過)→ 降格拒否 + profile_status=stale",
               rc == EXIT_VIOLATION and data.get("profile_status") == "stale", out[:300])
    finally:
        os.unlink(stale_path)

    # 6b) shared-default と未来日 approved_at は降格根拠に使えない
    rc, out, _ = _run_cli(
        [
            "--json",
            "--today",
            FIXTURE_TODAY,
            "--profile",
            FIXTURE_PROFILE,
            "--profile-source-kind",
            "shared-default",
            good,
        ]
    )
    shared_data = json.loads(out)
    expect(
        "approved shared-default → 降格拒否",
        rc == EXIT_VIOLATION
        and shared_data.get("profile_demotion_usable") is False,
        out[:300],
    )

    with open(FIXTURE_PROFILE, "r", encoding="utf-8") as fh:
        future_profile = yaml.safe_load(fh)
    future_profile["metadata"]["approved_at"] = "2027-01-01"
    with tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as fh:
        yaml.safe_dump(future_profile, fh, allow_unicode=True)
        future_path = fh.name
    try:
        rc, out, _ = _run_cli(
            ["--json", "--today", FIXTURE_TODAY, "--profile", future_path, good]
        )
        future_data = json.loads(out)
        expect(
            "未来日 approved_at → draft として降格拒否",
            rc == EXIT_VIOLATION and future_data.get("profile_status") == "draft",
            out[:300],
        )
    finally:
        os.unlink(future_path)

    # 7) a11y: profile の review_risk_policy.a11y_review_by_default: true で全スキップ
    with open(FIXTURE_PROFILE, "r", encoding="utf-8") as fh:
        a11y_profile = yaml.safe_load(fh)
    a11y_profile["review_risk_policy"]["a11y_review_by_default"] = True
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
        yaml.safe_dump(a11y_profile, fh, allow_unicode=True)
        a11y_path = fh.name
    try:
        rc, out, _ = _run_cli(["--json", "--today", FIXTURE_TODAY, "--profile", a11y_path,
                               bad_contract])
        errs, warns = codes_of(out)
        expect("a11y 有効 profile → FORBIDDEN_TERM 全スキップ",
               "FORBIDDEN_TERM" not in errs and "FORBIDDEN_TERM" not in warns,
               str(errs | warns))
    finally:
        os.unlink(a11y_path)

    # 8) --allow-a11y でも同様にスキップ
    rc, out, _ = _run_cli(["--json", "--allow-a11y"] + base_args + [bad_contract])
    errs, warns = codes_of(out)
    expect("--allow-a11y → FORBIDDEN_TERM 全スキップ",
           "FORBIDDEN_TERM" not in errs and "FORBIDDEN_TERM" not in warns,
           str(errs | warns))

    # 9) テキスト出力の書式(1 行 1 件、CODE\tlocation\tdetail)
    rc, out, _ = _run_cli(base_args + [bad_fields])
    lines = [l for l in out.splitlines() if l.strip()]
    expect("テキスト出力が CODE\\tlocation\\tdetail 形式",
           lines and all(len(l.split("\t")) == 3 for l in lines), out[:200])

    # 10) COUNT_INFO: findings 総数が閾値超で warning(非ブロッキング)。閾値以下では出さない
    over = {"findings": [{"id": "C-%02d" % i} for i in range(COUNT_INFO_THRESHOLD + 1)]}
    v_over = []
    check_finding_count(over, v_over)
    expect("findings 総数 > 閾値 → COUNT_INFO を 1 件出す",
           len(v_over) == 1 and v_over[0].code == "COUNT_INFO", str([x.code for x in v_over]))
    expect("COUNT_INFO は warning(error ではない=非ブロッキング)",
           v_over and v_over[0].severity == "warning", str(v_over and v_over[0].severity))
    at = {"findings": [{"id": "C-%02d" % i} for i in range(COUNT_INFO_THRESHOLD)]}
    v_at = []
    check_finding_count(at, v_at)
    expect("findings 総数 = 閾値 → COUNT_INFO を出さない", not v_at, str([x.code for x in v_at]))

    print()
    if failures:
        print("selftest 失敗: %d 件 — %s" % (len(failures), "; ".join(failures)))
        return 1
    print("selftest 全件合格(新スキーマ・降格契約・fail-closed・exit 0/2/3 を確認)")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
