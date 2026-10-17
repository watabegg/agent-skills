#!/usr/bin/env bash
# lint_plan.py / build_plan_html.py の回帰テスト。
# 期待: 有効フィクスチャは PASS、テンプレート（未記入プレースホルダ）は FAIL。
# usage: selftest.sh   →  exit 0 = 全ケース期待どおり
set -uo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
LINT="$DIR/scripts/lint_plan.sh"
FX="$DIR/tests/fixtures"
REF="$DIR/references"
fails=0
tmp=$(mktemp -d)

expect() { # expect <期待exit> <ラベル> <args...>
  local want="$1" label="$2"; shift 2
  "$LINT" "$@" > /tmp/selftest_out.txt 2>&1
  local got=$?
  if [[ "$got" == "$want" ]]; then
    echo "ok: $label"
  else
    echo "NG: $label (expect exit $want, got $got)"
    sed 's/^/    /' /tmp/selftest_out.txt | head -10
    fails=$((fails + 1))
  fi
}

# ---- 正例 ----
expect 0 "full 計画 + todo（バンドル相互参照込み）" "$FX/valid-full-plan.md" "$FX/valid-full-todo.md"
expect 0 "lite 計画" "$FX/valid-lite-plan.md"
expect 0 "goal-prompt" "$FX/valid-full-goal-prompt.md"
expect 0 "detailed フェーズ" "$FX/valid-detailed-phase.md"
expect 0 "detailed フェーズ（--require-detailed）" --require-detailed "$FX/valid-detailed-phase.md"
expect 0 "outline フェーズ（緩和モード）" "$FX/valid-outline-phase.md"
expect 0 "causal-v1 ownership 計画" "$FX/valid-causal-plan.md" "$FX/valid-causal-todo.md" "$FX/valid-causal-goal-prompt.md"
expect 0 "causal-v1 M=N 分離計画" "$FX/valid-causal-split-plan.md" "$FX/valid-causal-split-todo.md" "$FX/valid-causal-split-goal-prompt.md"
expect 0 "causal-v1 lite 問題モデル" "$FX/valid-causal-lite-plan.md"
expect 0 "causal-v1 outline phase" "$FX/valid-causal-outline-phase.md"

sed 's/- 状態: outline/<!-- - 状態: outline -->/' "$FX/valid-causal-outline-phase.md" > "$tmp/causal-comment-state.md"
expect 1 "causal-v1 コメント内 state 宣言 FAIL" "$tmp/causal-comment-state.md"

sed 's/- 設計ハブ: true/<!-- - 設計ハブ: true -->/' "$FX/valid-causal-detailed-phase.md" > "$tmp/causal-comment-hub.md"
expect 1 "causal-v1 コメント内 design hub 宣言 FAIL" "$tmp/causal-comment-hub.md"

sed 's/- 状態: outline/- 状態: outline garbage/' "$FX/valid-causal-outline-phase.md" > "$tmp/causal-garbage-state.md"
expect 1 "causal-v1 state 宣言の余剰文字 FAIL" "$tmp/causal-garbage-state.md"

sed 's/- 設計ハブ: true/- 設計ハブ: true garbage/' "$FX/valid-causal-detailed-phase.md" > "$tmp/causal-garbage-hub.md"
expect 1 "causal-v1 design hub 宣言の余剰文字 FAIL" "$tmp/causal-garbage-hub.md"

sed 's/- 状態: detailed/- 状態: outline/' "$FX/valid-causal-phase2.md" > "$tmp/causal-hidden-outline.md"
expect 1 "causal-v1 outline に隠れた Step FAIL" "$tmp/causal-hidden-outline.md"

# ---- outline は詳細化 Gate では失格 ----
expect 1 "outline フェーズ（--require-detailed で失格）" --require-detailed "$FX/valid-outline-phase.md"

# ---- テンプレートは未記入で必ず FAIL ----
# 2026-08-29 リニューアルで causal-v1 テンプレートは legacy/causal-v1/references へ退避した。
# lint は causal-v1 計画専用のため、検査対象も退避先を参照する。
LEGACY_REF="$(dirname "$REF")/legacy/causal-v1/references"
for t in plan-template plan-lite-template scaled-overview-template scaled-phase-template todo-template goal-prompt-template; do
  expect 1 "テンプレート未記入 FAIL: $t" "$LEGACY_REF/$t.md"
done

# ---- causal-v1: Cause → Model → Repair → LCU → Step の参照契約 ----
sed "s#- falsifier: 全 membership write path が共通の導出関数を必ず呼ぶことを call graph と test で確認できた場合#- falsifier: なし#" "$FX/valid-causal-plan.md" > "$tmp/causal-no-falsifier.md"
expect 1 "causal-v1 falsifier なし FAIL" "$tmp/causal-no-falsifier.md"

sed '/^- 証拠:/c\
- 証拠: writer が複数ある' "$FX/valid-causal-plan.md" > "$tmp/causal-no-evidence.md"
expect 1 "causal-v1 構造化証拠なし FAIL" "$tmp/causal-no-evidence.md"

sed '/^- authoritative source:/d' "$FX/valid-causal-plan.md" > "$tmp/causal-pattern-first.md"
expect 1 "causal-v1 reconcile 名だけで概念モデル欠落 FAIL" "$tmp/causal-pattern-first.md"

sed 's/corrective-bundle(2)/corrective-bundle(99)/' "$FX/valid-causal-plan.md" > "$tmp/causal-input-count.md"
expect 1 "causal-v1 入力件数と Input Ref 不一致 FAIL" "$tmp/causal-input-count.md"

sed 's/corrective-bundle(2)/corrective-bundle(0)/' "$FX/valid-causal-plan.md" > "$tmp/causal-zero-bundle.md"
expect 1 "causal-v1 corrective-bundle(0) FAIL" "$tmp/causal-zero-bundle.md"

sed 's/corrective-bundle(2)/corrective-bundle(1)/' "$FX/valid-causal-plan.md" > "$tmp/causal-one-bundle.md"
expect 1 "causal-v1 corrective-bundle(1) FAIL" "$tmp/causal-one-bundle.md"

sed 's/期待動作/期待結果/' "$FX/valid-causal-plan.md" > "$tmp/causal-no-expected-column.md"
expect 1 "causal-v1 Finding 期待動作列なし FAIL" "$tmp/causal-no-expected-column.md"

sed -e 's/| F-01 | I-01 |/| F-01 | I-01, I-02 |/' \
    -e 's/| F-02 | I-02 |/| F-02 | variant: V-01 |/' \
    -e 's/扱い: include: F-01, F-02/扱い: include: F-01/' \
    "$FX/valid-causal-plan.md" > "$tmp/causal-variant-origin-mismatch.md"
expect 1 "causal-v1 Variant 起源 Finding の include 漏れ FAIL" "$tmp/causal-variant-origin-mismatch.md"

sed 's/- Seed: C-01/- Seed: UNKNOWN-999/' "$FX/valid-causal-plan.md" > "$tmp/causal-variant-unknown-seed.md"
expect 1 "causal-v1 Variant の未知 Seed FAIL" "$tmp/causal-variant-unknown-seed.md"

sed -e 's/| F-01 | I-01 |/| F-01 | I-01, I-02 |/' \
    -e 's/| F-02 | I-02 |/| F-02 | variant: V-01 |/' \
    -e 's/- Seed: C-01/- Seed: F-02/' \
    "$FX/valid-causal-plan.md" > "$tmp/causal-variant-cyclic-seed.md"
expect 1 "causal-v1 Variant Seed が初期入力へ遡れない FAIL" "$tmp/causal-variant-cyclic-seed.md"

sed 's/corrective-single/corrective-bundle(2)/' "$FX/valid-causal-lite-plan.md" > "$tmp/causal-lite-bundle.md"
expect 1 "causal-v1 lite corrective-bundle 禁止 FAIL" "$tmp/causal-lite-bundle.md"

sed 's/- 責務の owner: ownership repository の transaction helper/- 責務の owner: 対象外: owner を置かないため/' "$FX/valid-causal-plan.md" > "$tmp/causal-model-no-owner.md"
expect 1 "causal-v1 Model owner 対象外 FAIL" "$tmp/causal-model-no-owner.md"

sed 's/- trigger: owner role membership を変更する全 command の transaction 終端/- trigger: 対象外: 常時実行するため/' "$FX/valid-causal-plan.md" > "$tmp/causal-repair-no-trigger.md"
expect 1 "causal-v1 Repair trigger 対象外 FAIL" "$tmp/causal-repair-no-trigger.md"

sed 's/- enforcement point: ownership repository の reconcile helper/- enforcement point: 対象外: 呼出側に任せるため/' "$FX/valid-causal-plan.md" > "$tmp/causal-repair-no-enforcement.md"
expect 1 "causal-v1 Repair enforcement point 対象外 FAIL" "$tmp/causal-repair-no-enforcement.md"

sed "/^- Primary LCU: LCU-01$/d" "$FX/valid-causal-plan.md" > "$tmp/causal-no-primary-step.md"
expect 1 "causal-v1 Step Primary LCU 欠落 FAIL" "$tmp/causal-no-primary-step.md"

sed 's/Step 1 (LCU-01):/Step 1 (LCU-99):/' "$FX/valid-causal-plan.md" > "$tmp/causal-plan-todo-wrong-lcu.md"
expect 1 "causal-v1 plan TodoList の Primary LCU 不一致 FAIL" "$tmp/causal-plan-todo-wrong-lcu.md"

sed "/^- Primary LCU: LCU-01$/a\\
- Shared LCU: LCU-01" "$FX/valid-causal-plan.md" > "$tmp/causal-shared-no-reason.md"
expect 1 "causal-v1 Shared LCU 理由なし FAIL" "$tmp/causal-shared-no-reason.md"

sed "/^比較不要: membership /d" "$FX/valid-causal-plan.md" > "$tmp/causal-no-comparison-decision.md"
expect 1 "causal-v1 比較表も比較不要理由もなし FAIL" "$tmp/causal-no-comparison-decision.md"

sed "s/| LCU-01 | F-01, F-02 | C-01 | M-01 | R-01 | 併合:/| LCU-01 | F-01 | C-01 | M-01 | R-01 | 分離:/" "$FX/valid-causal-plan.md" > "$tmp/causal-unmapped-finding.md"
expect 1 "causal-v1 Primary Finding 被覆漏れ FAIL" "$tmp/causal-unmapped-finding.md"

sed '/^| 判断 | 採用 | 根拠 | LCU |$/,/^| enforcement point |/d' "$FX/valid-causal-plan.md" > "$tmp/causal-no-decision-table.md"
expect 1 "causal-v1 他表による設計判断代用 FAIL" "$tmp/causal-no-decision-table.md"

sed '/^比較不要:/c\
- 比較対象: LCU-01\
\
| 軸 | 案1: transaction reconcile | 案2: nightly repair |\
| --- | --- | --- |\
| 要求・不変条件・安全性を満たすか | PASS commit 時に整合 | PASS 翌朝には整合 |\
| 不整合時間 | ◎ 0秒 | × 最大24時間 |\
| 既存境界との一致 | ◎ repository 内 | △ job 追加 |\
| 歪み | transaction が少し長くなる | 不整合を許容する |\
| この案が勝つ現在条件 | commit 時整合が必要な現在 | 日次整合で足りる場合 |\
| 判定 | 採用 | 不採用 |\
\
- 比較対象: lock strategy\
\
| 軸 | 案1: aggregate lock | 案2: optimistic retry |\
| --- | --- | --- |\
| 要求・不変条件・安全性を満たすか | PASS transaction 内で整合 | PASS retry 成功時に整合 |\
| 既存方式との一致 | ◎ 既存 lock を再利用 | △ version 列が必要 |\
| 競合時の単純さ | ◎ 直列化 | △ retry 分岐 |\
| 歪み | lock 保持時間が増える | retry 上限が必要 |\
| この案が勝つ現在条件 | 既存 row lock がある現在 | lock を持てない場合 |\
| 判定 | 採用 | 不採用 |' "$FX/valid-causal-plan.md" > "$tmp/causal-option-table.md"
expect 0 "causal-v1 判断ごとの複数比較表 PASS" "$tmp/causal-option-table.md"

sed 's/比較対象: LCU-01/比較対象: LCU-99/' "$tmp/causal-option-table.md" > "$tmp/causal-option-unknown-lcu.md"
expect 1 "causal-v1 比較対象の未知 LCU FAIL" "$tmp/causal-option-unknown-lcu.md"

sed -e '/^- 比較対象: LCU-01/a\
- 比較対象: lock strategy' \
    -e '/^- 比較対象: lock strategy$/d' \
    "$tmp/causal-option-table.md" > "$tmp/causal-option-grouped-targets.md"
expect 1 "causal-v1 比較対象を表前へまとめる迂回 FAIL" "$tmp/causal-option-grouped-targets.md"

sed -e 's/| 併合: 同じ projection 不変条件と reconcile postcondition で閉じる | Step 1 |/| 併合: 同じ projection 不変条件と reconcile postcondition で閉じる | Step 1, Step 2 |/' \
    -e '/^| 1 | `go test \.\/internal\/ownership\/\.\.\.` | exit 0 |$/a\
| 2 | `go test ./internal/ownership/...` | exit 0 |' \
    -e '/^- \[ \] Step 1 (LCU-01):/a\
- [ ] Step 2 (LCU-01): `internal/ownership/reconcile.go` 回帰テスト（Gate: `go test ./internal/ownership/...`）' \
    -e '/^## 14\. テスト計画/i\
### Step 2: reconcile の回帰テストを追加する\
\
- Primary LCU: LCU-01\
- 変更ファイル: `internal/ownership/reconcile.go`\
- 実装内容:\
  1. owner 削除と交代の回帰ケースを追加する\
- 完了条件 (DoD): `go test ./internal/ownership/...` が exit 0\
- 実行コマンド: `go test ./internal/ownership/...`\
- Hard Gate:\
  - Command: `go test ./internal/ownership/...`\
  - Expect: exit 0\
' "$FX/valid-causal-plan.md" > "$tmp/causal-multi-step-lcu.md"
expect 0 "causal-v1 1 LCU を複数 Step で実装 PASS" "$tmp/causal-multi-step-lcu.md"

sed -e 's/| 併合: 同じ projection 不変条件と reconcile postcondition で閉じる | Step 1, Step 2 |/| 併合: 同じ projection 不変条件と reconcile postcondition で閉じる | Step 1 |/' \
    -e 's/### Step 2: reconcile の回帰テストを追加する/### Step 2: 次計画を詳細化する/' \
    -e '/### Step 2:/,/^## 14\./s/- Primary LCU: LCU-01/- Primary LCU: plan-maintenance/' \
    -e 's/Step 2 (LCU-01):/Step 2 (plan-maintenance):/' \
    "$tmp/causal-multi-step-lcu.md" > "$tmp/causal-full-plan-maintenance.md"
expect 1 "causal-v1 full で plan-maintenance 使用 FAIL" "$tmp/causal-full-plan-maintenance.md"

sed -e 's/^### Step 1:/### Step 9:/' \
    -e 's/^### Step 2:/### Step 1:/' \
    -e 's/^### Step 9:/### Step 2:/' \
    "$tmp/causal-multi-step-lcu.md" > "$tmp/causal-reversed-step-order.md"
expect 1 "causal-v1 Step 文書順の逆転 FAIL" "$tmp/causal-reversed-step-order.md"

# ---- バンドル・モード適格性 ----
sed 's/valid-full-plan.md/other-plan.md/' "$FX/valid-full-todo.md" > "$tmp/broken-todo.md"
expect 1 "バンドル相互参照切れ FAIL" "$FX/valid-full-plan.md" "$tmp/broken-todo.md"

cp "$FX/valid-full-todo.md" "$tmp/extra-todo.md"
expect 1 "todo 複数渡し BUNDLE FAIL" "$FX/valid-full-plan.md" "$FX/valid-full-todo.md" "$tmp/extra-todo.md"

sed 's/README の綴り誤りを修正する。/認証まわりの README の綴り誤りを修正する。/' "$FX/valid-lite-plan.md" > "$tmp/lite-forbidden.md"
expect 1 "lite 禁止領域（認証） FAIL" "$tmp/lite-forbidden.md"

# ---- 設計判断・メンタルモデル ----
sed 's/204 No Content を返す案/特になし/' "$FX/valid-full-plan.md" > "$tmp/design-once.md"
expect 1 "Design It Twice 放棄 FAIL" "$tmp/design-once.md"

sed 's/^| 軸 | モデル1: 薄いハンドラ追加 | モデル2: 監視サブシステム新設 |$/| 軸 | モデル1: 薄いハンドラ追加 |/' "$FX/valid-full-plan.md" > "$tmp/single-model.md"
expect 1 "メンタルモデル1候補のみ FAIL" "$tmp/single-model.md"

sed 's/^| 判定 | 採用 | 不採用 |$/| 判定 | 保留 | 不採用 |/' "$FX/valid-full-plan.md" > "$tmp/no-adopted-model.md"
expect 1 "メンタルモデル採用なし FAIL" "$tmp/no-adopted-model.md"

sed 's/依存先ヘルスチェックは表現できない/なし/' "$FX/valid-full-plan.md" > "$tmp/zero-distortion.md"
expect 1 "歪みゼロ申告 FAIL" "$tmp/zero-distortion.md"

sed '/このモデルが勝つ現在条件/d' "$FX/valid-full-plan.md" > "$tmp/no-win-cond.md"
expect 1 "勝つ現在条件行なし FAIL" "$tmp/no-win-cond.md"

sed 's/^| # | 判断項目 | 採用 | 非採用案 | 理由 |$/| 案 | メリット | デメリット |/' "$FX/valid-full-plan.md" > "$tmp/proscons.md"
expect 1 "Pros\/Cons 表 FAIL" "$tmp/proscons.md"

# ---- Hard Gate ----
sed 's/| 1 | `go test .\/internal\/api` | exit 0 |/| 1 | `go test .\/internal\/api` | 正しく動作することが確認できる |/' "$FX/valid-full-plan.md" > "$tmp/banned-expect.md"
expect 1 "Gate 表 Expect 判定不能語 FAIL" "$tmp/banned-expect.md"

sed 's/  - Command: `go test .\/internal\/api`/  - Command: `true`/' "$FX/valid-full-plan.md" > "$tmp/noop-gate.md"
expect 1 "no-op Gate FAIL" "$tmp/noop-gate.md"

# ---- プレースホルダ ----
sed 's/`internal\/api\/health.go`/`[path]`/' "$FX/valid-full-plan.md" > "$tmp/backtick-placeholder.md"
expect 1 "バッククォート内プレースホルダ FAIL" "$tmp/backtick-placeholder.md"

sed 's/API サーバーに死活監視用の GET \/health を追加する。/API サーバーに死活監視用の GET \/health を追加する。設定は `[notifications]` セクションを読む。/' "$FX/valid-full-plan.md" > "$tmp/real-brackets.md"
expect 0 "実データ角括弧（インラインコード）PASS" "$tmp/real-brackets.md"

# ---- リスク・todo・scaled-phase ----
sed '/| ルート衝突 |/d' "$FX/valid-full-plan.md" > "$tmp/risk-no-rows.md"
expect 1 "リスク表データ行なし FAIL" "$tmp/risk-no-rows.md"

sed '/状態: detailed/d' "$FX/valid-detailed-phase.md" > "$tmp/no-state-phase.md"
expect 1 "scaled-phase 状態行なし FAIL" "$tmp/no-state-phase.md"

# ---- goal-prompt ----
sed '/実装後フロー/d' "$FX/valid-full-goal-prompt.md" > "$tmp/goal-no-flow.md"
expect 1 "goal-prompt 実装後フロー欠落 FAIL" "$tmp/goal-no-flow.md"

sed 's/- gap-check 回数: 1/- gap-check 回数: 12/' "$FX/valid-full-goal-prompt.md" > "$tmp/goal-bad-preflight.md"
expect 1 "goal-prompt preflight 不正値 FAIL" "$tmp/goal-bad-preflight.md"

# ---- 要求適合 FAIL のモデルを採用 ----
sed 's/PASS 死活監視の要求を満たし認可要件なし/FAIL 認可要件を満たさない/' "$FX/valid-full-plan.md" > "$tmp/adopt-fail-model.md"
expect 1 "要求適合 FAIL モデル採用 FAIL" "$tmp/adopt-fail-model.md"

# ---- パイプ入り Gate コマンドは正当（表セル分割の共有パーサ） ----
sed 's/| 1 | `go test .\/internal\/api` | exit 0 |/| 1 | `go test .\/internal\/api \| grep -c ok` | 1 以上 |/' "$FX/valid-full-plan.md" > "$tmp/pipe-gate.md"
expect 0 "パイプ入り Gate コマンド PASS" "$tmp/pipe-gate.md"

# ---- 引数エラー ----
expect 2 "未知フラグ exit 2" --require_detailed "$FX/valid-detailed-phase.md"

# ---- finalize（納品トランザクション） ----
bundle=$(mktemp -d)
cp "$FX/valid-full-plan.md" "$FX/valid-full-todo.md" "$FX/valid-full-goal-prompt.md" "$bundle/"
expect 0 "finalize: full バンドル PASS（HTML 生成込み）" --finalize "$bundle/valid-full-plan.md"
if [[ -s "$bundle/valid-full-plan.html" ]]; then
  echo "ok: finalize が HTML を生成"
else
  echo "NG: finalize が HTML を生成していない"
  fails=$((fails + 1))
fi

causal_bundle=$(mktemp -d)
cp "$FX/valid-causal-plan.md" "$FX/valid-causal-todo.md" "$FX/valid-causal-goal-prompt.md" "$causal_bundle/"
expect 0 "finalize: causal-v1 バンドル PASS（星取り表なし）" --finalize "$causal_bundle/valid-causal-plan.md"

causal_split_bundle=$(mktemp -d)
cp "$FX/valid-causal-split-plan.md" "$FX/valid-causal-split-todo.md" \
   "$FX/valid-causal-split-goal-prompt.md" "$causal_split_bundle/"
expect 0 "finalize: causal-v1 2 findings → 2 LCU（M=N）PASS" --finalize "$causal_split_bundle/valid-causal-split-plan.md"

causal_multi_step_bundle=$(mktemp -d)
cp "$tmp/causal-multi-step-lcu.md" "$causal_multi_step_bundle/valid-causal-plan.md"
cp "$FX/valid-causal-goal-prompt.md" "$causal_multi_step_bundle/"
sed -e '/^| 1 | `go test \.\/internal\/ownership\/\.\.\.` | exit 0 | 未実行 |$/a\
| 2 | `go test ./internal/ownership/...` | exit 0 | 未実行 |' \
    -e '/^- \[ \] Step 1 (LCU-01):/a\
- [ ] Step 2 (LCU-01): `internal/ownership/reconcile.go` 回帰テスト（Gate: `go test ./internal/ownership/...`）' \
    "$FX/valid-causal-todo.md" > "$causal_multi_step_bundle/valid-causal-todo.md"
expect 0 "finalize: causal-v1 1 LCU → 複数 Step PASS" --finalize "$causal_multi_step_bundle/valid-causal-plan.md"

causal_bundle_bad_lcu=$(mktemp -d)
cp "$FX/valid-causal-plan.md" "$FX/valid-causal-goal-prompt.md" "$causal_bundle_bad_lcu/"
sed 's/Step 1 (LCU-01):/Step 1:/' "$FX/valid-causal-todo.md" > "$causal_bundle_bad_lcu/valid-causal-todo.md"
expect 1 "finalize: causal-v1 todo LCU 転記欠落 FAIL" --finalize "$causal_bundle_bad_lcu/valid-causal-plan.md"

causal_bundle_extra_lcu=$(mktemp -d)
cp "$FX/valid-causal-plan.md" "$FX/valid-causal-goal-prompt.md" "$causal_bundle_extra_lcu/"
sed 's/Step 1 (LCU-01):/Step 1 (LCU-01, LCU-99):/' \
    "$FX/valid-causal-todo.md" > "$causal_bundle_extra_lcu/valid-causal-todo.md"
expect 1 "finalize: causal-v1 todo の余分な LCU tag FAIL" --finalize "$causal_bundle_extra_lcu/valid-causal-plan.md"

causal_bundle_bad_master=$(mktemp -d)
cp "$FX/valid-causal-todo.md" "$FX/valid-causal-goal-prompt.md" "$causal_bundle_bad_master/"
sed 's/診断・モデル正本: `valid-causal-plan.md`/診断・モデル正本: `missing-plan.md`/' \
    "$FX/valid-causal-plan.md" > "$causal_bundle_bad_master/valid-causal-plan.md"
expect 1 "finalize: causal-v1 plan の診断・モデル正本不一致 FAIL" --finalize "$causal_bundle_bad_master/valid-causal-plan.md"

causal_bundle_copied_analysis=$(mktemp -d)
cp "$FX/valid-causal-plan.md" "$FX/valid-causal-goal-prompt.md" "$causal_bundle_copied_analysis/"
sed '/^## タスク/i\
## 因果分析\
\
#### Cause C-99: stale copy\
' "$FX/valid-causal-todo.md" > "$causal_bundle_copied_analysis/valid-causal-todo.md"
expect 1 "finalize: causal-v1 todo への分析正本複写 FAIL" --finalize "$causal_bundle_copied_analysis/valid-causal-plan.md"

causal_bundle_bad_todo_master=$(mktemp -d)
cp "$FX/valid-causal-plan.md" "$FX/valid-causal-goal-prompt.md" "$causal_bundle_bad_todo_master/"
sed 's/診断・モデル正本: `valid-causal-plan.md`/診断・モデル正本: `missing-plan.md`/' \
    "$FX/valid-causal-todo.md" > "$causal_bundle_bad_todo_master/valid-causal-todo.md"
expect 1 "finalize: causal-v1 todo の診断・モデル正本不一致 FAIL" --finalize "$causal_bundle_bad_todo_master/valid-causal-plan.md"

bundle2=$(mktemp -d)
cp "$FX/valid-full-plan.md" "$FX/valid-full-todo.md" "$bundle2/"
expect 1 "finalize: goal-prompt 欠落 FAIL" --finalize "$bundle2/valid-full-plan.md"

# finalize: 計画書自身を todo として参照する自己参照 FAIL（役割検査 + suffix 検査）
bundle4=$(mktemp -d)
sed 's/`valid-full-todo.md`/`valid-full-plan.md`/' "$FX/valid-full-plan.md" > "$bundle4/valid-full-plan.md"
cp "$FX/valid-full-goal-prompt.md" "$bundle4/"
expect 1 "finalize: 自己参照（plan を todo と主張） FAIL" --finalize "$bundle4/valid-full-plan.md"
rm -rf "$bundle4"

# finalize: scaled-phase を直接渡す迂回は FAIL
expect 1 "finalize: scaled-phase 直接指定 FAIL" --finalize "$FX/valid-detailed-phase.md"

# finalize: --require-detailed との同時指定は exit 2
expect 2 "finalize: フラグ同時指定 exit 2" --finalize --require-detailed "$FX/valid-full-plan.md"

# finalize: コメントに正しい参照・可視行に誤参照 → 可視行が使われて FAIL
bundle5=$(mktemp -d)
cp "$FX/valid-full-todo.md" "$FX/valid-full-goal-prompt.md" "$bundle5/"
sed 's/- 進捗台帳: `valid-full-todo.md`/<!-- - 進捗台帳: `valid-full-todo.md` -->\n- 進捗台帳: `missing-todo.md`/' "$FX/valid-full-plan.md" > "$bundle5/valid-full-plan.md"
expect 1 "finalize: コメント隠し参照 FAIL" --finalize "$bundle5/valid-full-plan.md"
rm -rf "$bundle5"

# finalize: todo の正本計画書が別ファイルを指す → FAIL（構造化相互参照）
bundle6=$(mktemp -d)
cp "$FX/valid-full-plan.md" "$FX/valid-full-goal-prompt.md" "$bundle6/"
sed 's/- 正本計画書: `valid-full-plan.md`/- 正本計画書: `other-plan.md`/' "$FX/valid-full-todo.md" > "$bundle6/valid-full-todo.md"
cp "$FX/valid-full-plan.md" "$bundle6/other-plan.md"
expect 1 "finalize: todo の正本が別計画 FAIL" --finalize "$bundle6/valid-full-plan.md"
rm -rf "$bundle6"

# scaled overview の完了 Gate 列: バッククォートなしコマンドは FAIL
bundle7=$(mktemp -d)
cp "$FX/valid-scaled-todo.md" "$FX/valid-scaled-goal-prompt.md" "$FX/valid-detailed-phase.md" "$FX/valid-outline-phase.md" "$bundle7/"
sed 's/| `go test .\/internal\/notify` → exit 0 |/| true → exit 0 |/' "$FX/valid-scaled-overview.md" > "$bundle7/valid-scaled-overview.md"
expect 1 "scaled 完了 Gate バッククォートなし FAIL" --finalize "$bundle7/valid-scaled-overview.md"
rm -rf "$bundle7"

# echo をパイプ後段で検証するコマンドは no-op ではない（誤検知ガード）
sed 's/  - Command: `go test .\/internal\/api`/  - Command: `echo ok | grep -c ok`/' "$FX/valid-full-plan.md" > "$tmp/echo-pipe.md"
expect 0 "echo+パイプ検証コマンド PASS" "$tmp/echo-pipe.md"

# メンタルモデル採点行の空セルは FAIL
sed 's/| 既存構造との一致 | ○ 既存 router の流儀のまま | △ 新しい層を持ち込む |/| 既存構造との一致 | ○ 既存 router の流儀のまま |  |/' "$FX/valid-full-plan.md" > "$tmp/empty-score.md"
expect 1 "メンタルモデル採点行の空セル FAIL" "$tmp/empty-score.md"

# finalize: /goal フェンスの実行参照差し替え FAIL（r4 major1 再現）
bundle8=$(mktemp -d)
cp "$FX/valid-full-plan.md" "$FX/valid-full-todo.md" "$bundle8/"
sed 's/- 作業の正: valid-full-plan.md/- 作業の正: wrong-plan.md/' "$FX/valid-full-goal-prompt.md" > "$bundle8/valid-full-goal-prompt.md"
expect 1 "finalize: \/goal フェンス差し替え FAIL" --finalize "$bundle8/valid-full-plan.md"
rm -rf "$bundle8"

# 本文を HTML コメントで包んだ計画は FAIL（r4 major2 再現）
python3 - "$FX/valid-full-plan.md" "$tmp/comment-wrapped.md" <<'PY'
import sys
text = open(sys.argv[1]).read()
start = text.index("## 1. 目的")
open(sys.argv[2], "w").write(text[:start] + "<!--\n" + text[start:] + "\n-->\n")
PY
expect 1 "コメント包み計画 FAIL" "$tmp/comment-wrapped.md"

# finalize: scaled 詳細化 Step を語句だけで代用 FAIL（r4 major3 再現）
bundle9=$(mktemp -d)
cp "$FX/valid-scaled-overview.md" "$FX/valid-scaled-todo.md" "$FX/valid-scaled-goal-prompt.md" "$FX/valid-outline-phase.md" "$bundle9/"
sed 's/^### Step 2: 次フェーズの詳細化$/参考語（Gate ではない）: --require-detailed valid-outline-phase.md/' "$FX/valid-detailed-phase.md" > "$bundle9/valid-detailed-phase.md"
expect 1 "finalize: 詳細化 Step 語句代用 FAIL" --finalize "$bundle9/valid-scaled-overview.md"
rm -rf "$bundle9"

# finalize: phase の Overview 逆参照が別ファイル FAIL
bundle10=$(mktemp -d)
cp "$FX/valid-scaled-overview.md" "$FX/valid-scaled-todo.md" "$FX/valid-scaled-goal-prompt.md" "$FX/valid-outline-phase.md" "$bundle10/"
sed 's/- Overview: `valid-scaled-overview.md`/- Overview: `wrong-overview.md`/' "$FX/valid-detailed-phase.md" > "$bundle10/valid-detailed-phase.md"
expect 1 "finalize: Overview 逆参照不一致 FAIL" --finalize "$bundle10/valid-scaled-overview.md"
rm -rf "$bundle10"

# finalize: phase の H1（# Phase N:）欠落 FAIL
bundle11=$(mktemp -d)
cp "$FX/valid-scaled-overview.md" "$FX/valid-scaled-todo.md" "$FX/valid-scaled-goal-prompt.md" "$FX/valid-detailed-phase.md" "$bundle11/"
sed 's/^# Phase 2: Slack チャネル — notification-v2$/# Slack チャネル/' "$FX/valid-outline-phase.md" > "$bundle11/valid-outline-phase.md"
expect 1 "finalize: phase H1 欠落 FAIL" --finalize "$bundle11/valid-scaled-overview.md"
rm -rf "$bundle11"

# finalize: コメント内の旧 /goal フェンス優先の穴（r5 major1 再現）
bundle12=$(mktemp -d)
cp "$FX/valid-full-plan.md" "$FX/valid-full-todo.md" "$bundle12/"
python3 - "$FX/valid-full-goal-prompt.md" "$bundle12/valid-full-goal-prompt.md" <<'PY'
import sys
s = open(sys.argv[1]).read()
start = s.index("## /goal")
fence_close = s.index("```", s.index("```", start) + 3) + 3
old_section = s[start:fence_close]
s = s[:start] + "<!--\n" + old_section + "\n-->\n\n" + s[start:]
comment_end = s.index("-->") + 3
head, visible = s[:comment_end], s[comment_end:]
visible = visible.replace("- 作業の正: valid-full-plan.md", "- 作業の正: wrong-plan.md", 1)
open(sys.argv[2], "w").write(head + visible)
PY
expect 1 "finalize: コメント内旧 \/goal フェンス FAIL" --finalize "$bundle12/valid-full-plan.md"
rm -rf "$bundle12"

# 許可パスリストの実体なしは FAIL（r5 major2 再現）
sed '/^- 許可パスリスト:/d' "$FX/valid-full-plan.md" > "$tmp/no-allowlist.md"
expect 1 "許可パスリスト行なし FAIL" "$tmp/no-allowlist.md"
sed 's#^- 許可パスリスト: `internal/api/\*\*`.*#- 許可パスリスト: 計画書の不変条件節を参照#' "$FX/valid-full-goal-prompt.md" > "$tmp/goal-no-allowlist.md"
expect 1 "goal 許可パス実体なし FAIL" "$tmp/goal-no-allowlist.md"

# finalize: todo から Step が欠落・別 Gate 参照は FAIL（r5 major3-A 再現）
bundle13=$(mktemp -d)
cp "$FX/valid-full-plan.md" "$FX/valid-full-goal-prompt.md" "$bundle13/"
sed -e '/- \[ \] Step 1:/d' -e 's/| 1 | `go test .\/internal\/api` | exit 0 | 未実行 |/| 2 | `go test .\/...` | exit 0 | 未実行 |/' "$FX/valid-full-todo.md" > "$bundle13/valid-full-todo.md"
expect 1 "finalize: todo の Step 欠落+別 Gate FAIL" --finalize "$bundle13/valid-full-plan.md"
rm -rf "$bundle13"

# finalize: plan にブロッカー・todo が None は FAIL（r5 major3-B 再現）
bundle14=$(mktemp -d)
cp "$FX/valid-full-todo.md" "$FX/valid-full-goal-prompt.md" "$bundle14/"
python3 - "$FX/valid-full-plan.md" "$bundle14/valid-full-plan.md" <<'PY'
import sys
s = open(sys.argv[1]).read()
old = "`Unclear Points: None（実装外として除外した論点: なし）`"
new = """1. Point: 実環境確認先 URL が未確定
   - Why: staging の確認先が提示されていない
   - Impact: ブロッカー
   - Need: staging URL を指定してほしい
   - Fallback: localhost で暫定確認する
   - 推奨: staging URL を確定してから着手する"""
open(sys.argv[2], "w").write(s.replace(old, new, 1))
PY
expect 1 "finalize: plan ブロッカー vs todo None FAIL" --finalize "$bundle14/valid-full-plan.md"
rm -rf "$bundle14"

# finalize: phase H1 のコロンなしは FAIL（r5 minor1 再現）
bundle15=$(mktemp -d)
cp "$FX/valid-scaled-overview.md" "$FX/valid-scaled-todo.md" "$FX/valid-scaled-goal-prompt.md" "$FX/valid-detailed-phase.md" "$bundle15/"
sed 's/^# Phase 2: /# Phase 2 /' "$FX/valid-outline-phase.md" > "$bundle15/valid-outline-phase.md"
expect 1 "finalize: phase H1 コロンなし FAIL" --finalize "$bundle15/valid-scaled-overview.md"
rm -rf "$bundle15"

# HTML サマリがコメント内の Step を数えないこと（r5 minor2 再現）
htmp2=$(mktemp -d)
{ cat "$FX/valid-full-plan.md"; printf '\n<!--\n### Step 99: コメントアウト済み\n- [x] コメントアウト済みタスク\n- Command: `go test ./hidden`\n-->\n'; } > "$htmp2/c.md"
python3 "$DIR/scripts/build_plan_html.py" "$htmp2/c.md" --output "$htmp2/c.html" > /dev/null 2>&1
if python3 - "$htmp2/c.html" <<'PY'
import json, re, sys
html = open(sys.argv[1]).read()
m = re.search(r'<script id="plan-manifest" type="application/json">(.*?)</script>', html, re.S)
mani = json.loads(m.group(1))
assert mani["steps"] == 1, mani["steps"]
assert "go test ./hidden" not in html, "コメント内Commandが本文に出ている"
PY
then
  echo "ok: HTML サマリがコメント内を数えない"
else
  echo "NG: HTML サマリがコメント内を数えない"
  fails=$((fails + 1))
fi
rm -rf "$htmp2"

# --- r6 回帰 ---

# todo の Gate 行の Command/Expect 空セルは FAIL（r6 major1）
sed 's/| 1 | `go test .\/internal\/api` | exit 0 | 未実行 |/| 1 |  |  | 未実行 |/' "$FX/valid-full-todo.md" > "$tmp/empty-gate-todo.md"
expect 1 "todo Gate 空セル FAIL" "$tmp/empty-gate-todo.md"

# None 宣言と Point の同居は FAIL（r6 major2）
python3 - "$FX/valid-full-plan.md" "$tmp/none-and-point.md" <<'PY'
import sys
s = open(sys.argv[1]).read()
old = "`Unclear Points: None（実装外として除外した論点: なし）`"
new = old + "\n\n1. Point: staging URL 未確定\n   - Why: 未提示\n   - Impact: ブロッカー\n   - Need: URL 指定\n   - Fallback: localhost\n   - 推奨: 確定後に着手"
open(sys.argv[2], "w").write(s.replace(old, new, 1))
PY
expect 1 "None と Point の同居 FAIL" "$tmp/none-and-point.md"

# 許可パスが変更対象を包含しないバンドルは FAIL（r6 major3）
bundle16=$(mktemp -d)
cp "$FX/valid-full-todo.md" "$bundle16/"
sed 's#`internal/api/\*\*`#`docs/**`#' "$FX/valid-full-plan.md" > "$bundle16/valid-full-plan.md"
sed 's#`internal/api/\*\*`#`docs/**`#' "$FX/valid-full-goal-prompt.md" > "$bundle16/valid-full-goal-prompt.md"
expect 1 "finalize: 許可パスが変更対象を非包含 FAIL" --finalize "$bundle16/valid-full-plan.md"
rm -rf "$bundle16"

# 全許可 glob（**/*）は FAIL（r6 major3）
sed 's#`internal/api/\*\*`#`**/*`#' "$FX/valid-full-plan.md" > "$tmp/universal-glob.md"
expect 1 "全許可 glob FAIL" "$tmp/universal-glob.md"

# 非機械的な Expect（成功する）は error（r6 major4）
sed 's/  - Expect: exit 0/  - Expect: 成功する/' "$FX/valid-full-plan.md" > "$tmp/vague-expect.md"
expect 1 "曖昧 Expect（成功する） FAIL" "$tmp/vague-expect.md"

# 同一文字列の /goal H2 複製は FAIL（r6 major5）
{ cat "$FX/valid-full-goal-prompt.md"; printf '\n## /goal 貼り付け用（この短文だけを使う）\n\n```\n別の実装。\n```\n'; } > "$tmp/dup-goal-h2.md"
expect 1 "goal H2 複製 FAIL" "$tmp/dup-goal-h2.md"

# outline のフェーズ行を詳細化タスクで代用は FAIL（r6 major6）
bundle17=$(mktemp -d)
cp "$FX/valid-scaled-overview.md" "$FX/valid-scaled-goal-prompt.md" "$FX/valid-detailed-phase.md" "$FX/valid-outline-phase.md" "$bundle17/"
sed '/- \[ \] Phase 2: Slack 実装/d' "$FX/valid-scaled-todo.md" > "$bundle17/valid-scaled-todo.md"
expect 1 "finalize: outline 行の詳細化タスク代用 FAIL" --finalize "$bundle17/valid-scaled-overview.md"
rm -rf "$bundle17"

# §6 一覧と Step 詳細の Expect 不一致は FAIL（r6 major7）
bundle18=$(mktemp -d)
cp "$FX/valid-full-todo.md" "$FX/valid-full-goal-prompt.md" "$bundle18/"
sed 's/| 1 | `go test .\/internal\/api` | exit 0 |/| 1 | `go test .\/internal\/api` | exit 1 |/' "$FX/valid-full-plan.md" > "$bundle18/valid-full-plan.md"
expect 1 "finalize: §6 一覧と Step 詳細の不一致 FAIL" --finalize "$bundle18/valid-full-plan.md"
rm -rf "$bundle18"

# todo.md 側だけタスクを26件以上にしても scaled を要求（r6 major8）
bundle19=$(mktemp -d)
cp "$FX/valid-full-plan.md" "$FX/valid-full-goal-prompt.md" "$bundle19/"
python3 - "$FX/valid-full-todo.md" "$bundle19/valid-full-todo.md" <<'PY'
import sys
s = open(sys.argv[1]).read()
for i in range(1, 26):
    s += f"\n- [ ] 補助タスク {i:02d}: 確認項目 {i:02d} を処理する"
open(sys.argv[2], "w").write(s + "\n")
PY
expect 1 "finalize: todo 26件超は scaled 要求 FAIL" --finalize "$bundle19/valid-full-plan.md"
rm -rf "$bundle19"

# todo の必須項目をコメント化すると FAIL（r6 minor1）
sed 's/^- アクティブタスク: P1-S1 未着手$/<!-- - アクティブタスク: P1-S1 未着手 -->/' "$FX/valid-scaled-todo.md" > "$tmp/comment-hidden-todo.md"
expect 1 "todo 必須項目のコメント化 FAIL" "$tmp/comment-hidden-todo.md"

# --- r7 回帰 ---

# lite: finalize PASS（plan + todo バンドル）
bundle20=$(mktemp -d)
cp "$FX/valid-lite-plan.md" "$FX/valid-lite-todo.md" "$bundle20/"
expect 0 "finalize: lite バンドル PASS" --finalize "$bundle20/valid-lite-plan.md"
rm -rf "$bundle20"

# lite: plan と todo の Gate コマンド不一致は FAIL（r7 major3）
bundle21=$(mktemp -d)
cp "$FX/valid-lite-plan.md" "$bundle21/"
sed 's#`rg -n "teh" README.md`#`git diff --check`#g' "$FX/valid-lite-todo.md" > "$bundle21/valid-lite-todo.md"
expect 1 "finalize: lite Gate 不一致 FAIL" --finalize "$bundle21/valid-lite-plan.md"
rm -rf "$bundle21"

# 重複 Unclear Points セクション（decoy）は FAIL（r7 major1）
python3 - "$FX/valid-full-plan.md" "$tmp/dup-unclear.md" <<'PY'
import sys
s = open(sys.argv[1]).read()
old = "## 11. Unclear Points"
new = "## 10.5 Unclear Points\n\n`Unclear Points: None（実装外として除外した論点: なし）`\n\n## 11. Unclear Points"
open(sys.argv[2], "w").write(s.replace(old, new, 1))
PY
expect 1 "重複 Unclear セクション FAIL" "$tmp/dup-unclear.md"

# §Hard Gate 一覧の表を削除すると finalize FAIL（r7 major2）
bundle22=$(mktemp -d)
cp "$FX/valid-full-todo.md" "$FX/valid-full-goal-prompt.md" "$bundle22/"
python3 - "$FX/valid-full-plan.md" "$bundle22/valid-full-plan.md" <<'PY'
import sys
s = open(sys.argv[1]).read()
block = "| Step | Command | Expect |\n| --- | --- | --- |\n| 1 | `go test ./internal/api` | exit 0 |\n\n"
open(sys.argv[2], "w").write(s.replace(block, "", 1))
PY
expect 1 "finalize: §6 表削除 FAIL" --finalize "$bundle22/valid-full-plan.md"
rm -rf "$bundle22"

# §Hard Gate 一覧の重複 ID は finalize FAIL（r7 major2）
bundle23=$(mktemp -d)
cp "$FX/valid-full-todo.md" "$FX/valid-full-goal-prompt.md" "$bundle23/"
sed 's/| 1 | `go test .\/internal\/api` | exit 0 |/| 1 | `git diff --check` | exit 0 |\n| 1 | `go test .\/internal\/api` | exit 0 |/' "$FX/valid-full-plan.md" > "$bundle23/valid-full-plan.md"
expect 1 "finalize: §6 重複 ID FAIL" --finalize "$bundle23/valid-full-plan.md"
rm -rf "$bundle23"

# decoy 許可パス行（正本外）は無視され、包含違反で FAIL（r7 major4-A）
bundle24=$(mktemp -d)
cp "$FX/valid-full-goal-prompt.md" "$bundle24/"
python3 - "$FX/valid-full-plan.md" "$bundle24/valid-full-plan.md" <<'PY'
import sys
s = open(sys.argv[1]).read()
s = s.replace("\n## 1. 目的", "\n- 許可パスリスト: `**`\n\n## 1. 目的", 1)
s = s.replace("internal/api/health.go", "internal/auth/secret.go")
open(sys.argv[2], "w").write(s)
PY
sed 's#internal/api/health.go#internal/auth/secret.go#g' "$FX/valid-full-todo.md" > "$bundle24/valid-full-todo.md"
expect 1 "finalize: decoy 許可パス行 FAIL" --finalize "$bundle24/valid-full-plan.md"
rm -rf "$bundle24"

# 全許可 glob の混在（** + 具体パス）は FAIL（r7 major4-B）
sed 's#`internal/api/\*\*`#`**`, `internal/api/**`#' "$FX/valid-full-plan.md" > "$tmp/mixed-universal.md"
expect 1 "全許可 glob 混在 FAIL" "$tmp/mixed-universal.md"

# .. を含む変更対象パスは finalize FAIL（r7 major4-C）
bundle25=$(mktemp -d)
cp "$FX/valid-full-goal-prompt.md" "$bundle25/"
sed 's#internal/api/health.go#internal/api/../auth/secret.go#g' "$FX/valid-full-plan.md" > "$bundle25/valid-full-plan.md"
sed 's#internal/api/health.go#internal/api/../auth/secret.go#g' "$FX/valid-full-todo.md" > "$bundle25/valid-full-todo.md"
expect 1 "finalize: .. 含みパス FAIL" --finalize "$bundle25/valid-full-plan.md"
rm -rf "$bundle25"

# 引用+数字で包んだ主観 Expect は FAIL（r7 major5）
sed 's/  - Expect: exit 0/  - Expect: 「正しく動作することが確認できる（1回）」/' "$FX/valid-full-plan.md" > "$tmp/quoted-vague.md"
expect 1 "引用+数字の主観 Expect FAIL" "$tmp/quoted-vague.md"

# 1セルに複数パスを詰めるのは FAIL（r7 major6）
python3 - "$FX/valid-full-plan.md" "$tmp/packed-paths.md" <<'PY'
import sys
s = open(sys.argv[1]).read()
paths = ", ".join(f"`internal/api/file{i:02d}.go`" for i in range(1, 32))
old = "| `internal/api/health.go` | 新規 | handler 追加 | 1 |"
open(sys.argv[2], "w").write(s.replace(old, f"| {paths} | 新規 | 31ファイルを追加 | 1 |", 1))
PY
expect 1 "1セル複数パス FAIL" "$tmp/packed-paths.md"

# scaled: フェーズ完了 Gate セルが空なら finalize FAIL（r7 major7）
bundle26=$(mktemp -d)
cp "$FX/valid-scaled-todo.md" "$FX/valid-scaled-goal-prompt.md" "$FX/valid-detailed-phase.md" "$FX/valid-outline-phase.md" "$bundle26/"
sed 's/| 2 | Slack 実装 | outline | `valid-outline-phase.md` | `go test .\/internal\/notify` → exit 0 |/| 2 | Slack 実装 | outline | `valid-outline-phase.md` |  |/' "$FX/valid-scaled-overview.md" > "$bundle26/valid-scaled-overview.md"
expect 1 "finalize: フェーズ完了 Gate 空 FAIL" --finalize "$bundle26/valid-scaled-overview.md"
rm -rf "$bundle26"

# 実環境動作確認: なし は FAIL（r7 major8）
sed 's#- 実環境動作確認: `curl -s localhost:8080/health` が 200 を返す#- 実環境動作確認: なし#' "$FX/valid-full-plan.md" > "$tmp/env-nashi.md"
expect 1 "実環境動作確認なし FAIL" "$tmp/env-nashi.md"

# manifest JSON の </script> エスケープ（r7 major9）
if python3 - "$DIR/scripts" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from build_plan_html import json_for_script
out = json_for_script({"source": "x</script><script>alert(1)</script>"})
assert "</script" not in out, out
assert "\\u003c" in out
PY
then echo "ok: manifest JSON エスケープ"; else echo "NG: manifest JSON エスケープ"; fails=$((fails + 1)); fi

# 固定名 tmp への symlink 攻撃が成立しない（r7 major10）
sltmp=$(mktemp -d)
cp "$FX/valid-full-plan.md" "$sltmp/"
victim=$(mktemp); printf 'DO NOT OVERWRITE\n' > "$victim"
ln -s "$victim" "$sltmp/valid-full-plan.html.tmp"
python3 "$DIR/scripts/build_plan_html.py" "$sltmp/valid-full-plan.md" > /dev/null 2>&1
if grep -q 'DO NOT OVERWRITE' "$victim"; then
  echo "ok: symlink 攻撃不成立"
else
  echo "NG: symlink 経由で victim が上書きされた"
  fails=$((fails + 1))
fi
rm -rf "$sltmp" "$victim"

# タスク行の Gate と Hard Gate 表の不一致は finalize FAIL（r7 minor1）
bundle27=$(mktemp -d)
cp "$FX/valid-full-plan.md" "$FX/valid-full-goal-prompt.md" "$bundle27/"
sed 's#（Gate: `go test ./internal/api`）#（Gate: `git clean -fdx`）#' "$FX/valid-full-todo.md" > "$bundle27/valid-full-todo.md"
expect 1 "finalize: タスク行 Gate 不一致 FAIL" --finalize "$bundle27/valid-full-plan.md"
rm -rf "$bundle27"

# 非採用案の空セルは FAIL（r7 minor2）
sed 's/204 No Content を返す案//' "$FX/valid-full-plan.md" > "$tmp/empty-alt.md"
expect 1 "非採用案 空セル FAIL" "$tmp/empty-alt.md"

# Step DoD にコマンドがないのは FAIL（r7 minor3）
sed 's#- 完了条件 (DoD): `go test ./internal/api` が exit 0#- 完了条件 (DoD): レビュー済み#' "$FX/valid-full-plan.md" > "$tmp/dod-no-cmd.md"
expect 1 "Step DoD コマンドなし FAIL" "$tmp/dod-no-cmd.md"

# 複数バッククォートのコードスパンを含む表セルの分割
if python3 -c "
import sys; sys.path.insert(0, '$DIR/scripts')
from mdtable import split_row
cells = split_row('| 1 | \`\`echo \`x\` | wc -l\`\` | exit 0 |')
assert len(cells) == 3, cells
"; then
  echo "ok: mdtable 複数バッククォート分割"
else
  echo "NG: mdtable 複数バッククォート分割"
  fails=$((fails + 1))
fi

bundle3=$(mktemp -d)
cp "$FX/valid-scaled-overview.md" "$FX/valid-scaled-todo.md" "$FX/valid-scaled-goal-prompt.md" \
   "$FX/valid-detailed-phase.md" "$FX/valid-outline-phase.md" "$bundle3/"
expect 0 "finalize: scaled バンドル PASS" --finalize "$bundle3/valid-scaled-overview.md"

causal_scaled_bundle=$(mktemp -d)
cp "$FX/valid-causal-scaled-overview.md" "$FX/valid-causal-scaled-todo.md" \
   "$FX/valid-causal-scaled-goal-prompt.md" "$FX/valid-causal-detailed-phase.md" \
   "$FX/valid-causal-phase2.md" "$causal_scaled_bundle/"
expect 0 "finalize: causal-v1 scaled 設計正本 PASS" --finalize "$causal_scaled_bundle/valid-causal-scaled-overview.md"

causal_scaled_bad_overview_tag=$(mktemp -d)
cp "$FX/valid-causal-scaled-todo.md" "$FX/valid-causal-scaled-goal-prompt.md" \
   "$FX/valid-causal-detailed-phase.md" "$FX/valid-causal-phase2.md" "$causal_scaled_bad_overview_tag/"
sed 's/Phase 2 (LCU-01):/Phase 2 (LCU-99):/' \
    "$FX/valid-causal-scaled-overview.md" > "$causal_scaled_bad_overview_tag/valid-causal-scaled-overview.md"
expect 1 "finalize: causal-v1 overview TodoList の LCU tag 不一致 FAIL" --finalize "$causal_scaled_bad_overview_tag/valid-causal-scaled-overview.md"

causal_scaled_bad_phase_todo=$(mktemp -d)
cp "$FX/valid-causal-scaled-overview.md" "$FX/valid-causal-scaled-todo.md" \
   "$FX/valid-causal-scaled-goal-prompt.md" "$FX/valid-causal-detailed-phase.md" "$causal_scaled_bad_phase_todo/"
sed 's/Step 1 (LCU-01):/Step 1 (LCU-99):/' \
    "$FX/valid-causal-phase2.md" > "$causal_scaled_bad_phase_todo/valid-causal-phase2.md"
expect 1 "finalize: causal-v1 後続 phase Todo の LCU tag 不一致 FAIL" --finalize "$causal_scaled_bad_phase_todo/valid-causal-scaled-overview.md"

causal_scaled_outline=$(mktemp -d)
cp "$FX/valid-causal-scaled-goal-prompt.md" "$FX/valid-causal-outline-phase.md" "$causal_scaled_outline/"
sed -e 's/| 2 | writer 統合 | LCU-01 | detailed | `valid-causal-phase2.md`/| 2 | writer 統合 | LCU-01 | outline | `valid-causal-outline-phase.md`/' \
    "$FX/valid-causal-scaled-overview.md" > "$causal_scaled_outline/valid-causal-scaled-overview.md"
sed 's/valid-causal-phase2.md/valid-causal-outline-phase.md/g' \
    "$FX/valid-causal-detailed-phase.md" > "$causal_scaled_outline/valid-causal-detailed-phase.md"
sed -e '/^| P2-S1 |/d' \
    -e 's/valid-causal-phase2.md/valid-causal-outline-phase.md/g' \
    -e 's/^- \[ \] P2-S1 (LCU-01): .*$/- [ ] Phase 2 (LCU-01): writer 統合（Gate: `go test .\/internal\/ownership\/...`）/' \
    "$FX/valid-causal-scaled-todo.md" > "$causal_scaled_outline/valid-causal-scaled-todo.md"
expect 0 "finalize: causal-v1 LCU が future outline phase に跨る PASS" --finalize "$causal_scaled_outline/valid-causal-scaled-overview.md"

causal_scaled_outline_missing_tag=$(mktemp -d)
cp -R "$causal_scaled_outline/." "$causal_scaled_outline_missing_tag/"
sed 's/Phase 2 (LCU-01):/Phase 2:/' \
    "$causal_scaled_outline/valid-causal-scaled-todo.md" > "$causal_scaled_outline_missing_tag/valid-causal-scaled-todo.md"
expect 1 "finalize: causal-v1 outline todo の LCU tag 欠落 FAIL" --finalize "$causal_scaled_outline_missing_tag/valid-causal-scaled-overview.md"

causal_scaled_outline_bad_gate=$(mktemp -d)
cp -R "$causal_scaled_outline/." "$causal_scaled_outline_bad_gate/"
sed 's/- 最終 Gate: `P2-S1`/- 最終 Gate: `P9-S9`/' \
    "$causal_scaled_outline/valid-causal-detailed-phase.md" > "$causal_scaled_outline_bad_gate/valid-causal-detailed-phase.md"
expect 1 "finalize: causal-v1 outline 最終 Gate の phase 不一致 FAIL" --finalize "$causal_scaled_outline_bad_gate/valid-causal-scaled-overview.md"

causal_scaled_bad_intermediate=$(mktemp -d)
cp "$FX/valid-causal-scaled-overview.md" "$FX/valid-causal-scaled-todo.md" \
   "$FX/valid-causal-scaled-goal-prompt.md" "$FX/valid-causal-phase2.md" "$causal_scaled_bad_intermediate/"
sed 's/中間 postcondition: Phase 1=/中間 postcondition: Phase 10=/' \
    "$FX/valid-causal-detailed-phase.md" > "$causal_scaled_bad_intermediate/valid-causal-detailed-phase.md"
expect 1 "finalize: causal-v1 LCU Split 中間 phase の部分一致 FAIL" --finalize "$causal_scaled_bad_intermediate/valid-causal-scaled-overview.md"

causal_scaled_empty_model=$(mktemp -d)
cp "$FX/valid-causal-scaled-todo.md" "$FX/valid-causal-scaled-goal-prompt.md" \
   "$FX/valid-causal-detailed-phase.md" "$FX/valid-causal-phase2.md" "$causal_scaled_empty_model/"
sed 's/^- 解決モデル: .*$/- 解決モデル:/' \
    "$FX/valid-causal-scaled-overview.md" > "$causal_scaled_empty_model/valid-causal-scaled-overview.md"
expect 1 "finalize: causal-v1 overview の空解決モデル FAIL" --finalize "$causal_scaled_empty_model/valid-causal-scaled-overview.md"

causal_scaled_bad=$(mktemp -d)
cp "$FX/valid-causal-scaled-overview.md" "$FX/valid-causal-scaled-todo.md" \
   "$FX/valid-causal-scaled-goal-prompt.md" "$FX/valid-causal-phase2.md" "$causal_scaled_bad/"
sed 's/- 設計ハブ: true/- 設計ハブ: false/' "$FX/valid-causal-detailed-phase.md" > "$causal_scaled_bad/valid-causal-detailed-phase.md"
expect 1 "finalize: causal-v1 scaled 設計ハブ欠落 FAIL" --finalize "$causal_scaled_bad/valid-causal-scaled-overview.md"

causal_scaled_bad_ref=$(mktemp -d)
cp "$FX/valid-causal-scaled-overview.md" "$FX/valid-causal-scaled-todo.md" \
   "$FX/valid-causal-scaled-goal-prompt.md" "$FX/valid-causal-detailed-phase.md" "$causal_scaled_bad_ref/"
sed 's/診断・モデル正本: `valid-causal-detailed-phase.md`/診断・モデル正本: `missing-phase.md`/' \
    "$FX/valid-causal-phase2.md" > "$causal_scaled_bad_ref/valid-causal-phase2.md"
expect 1 "finalize: causal-v1 後続 phase の正本参照不一致 FAIL" --finalize "$causal_scaled_bad_ref/valid-causal-scaled-overview.md"

causal_scaled_second_hub=$(mktemp -d)
cp "$FX/valid-causal-scaled-overview.md" "$FX/valid-causal-scaled-todo.md" \
   "$FX/valid-causal-scaled-goal-prompt.md" "$FX/valid-causal-detailed-phase.md" "$causal_scaled_second_hub/"
sed 's/- 設計ハブ: false/- 設計ハブ: true/' \
    "$FX/valid-causal-phase2.md" > "$causal_scaled_second_hub/valid-causal-phase2.md"
expect 1 "finalize: causal-v1 後続 phase の第二設計ハブ FAIL" --finalize "$causal_scaled_second_hub/valid-causal-scaled-overview.md"

causal_scaled_copied_analysis=$(mktemp -d)
cp "$FX/valid-causal-scaled-overview.md" "$FX/valid-causal-scaled-todo.md" \
   "$FX/valid-causal-scaled-goal-prompt.md" "$FX/valid-causal-detailed-phase.md" "$causal_scaled_copied_analysis/"
sed '/^## 2\. 設計判断/i\
## 因果分析\
\
分析正本からの複写。\
' "$FX/valid-causal-phase2.md" > "$causal_scaled_copied_analysis/valid-causal-phase2.md"
expect 1 "finalize: causal-v1 後続 phase の分析複写 FAIL" --finalize "$causal_scaled_copied_analysis/valid-causal-scaled-overview.md"

causal_scaled_bad_gate=$(mktemp -d)
cp "$FX/valid-causal-scaled-overview.md" "$FX/valid-causal-scaled-todo.md" \
   "$FX/valid-causal-scaled-goal-prompt.md" "$FX/valid-causal-phase2.md" "$causal_scaled_bad_gate/"
sed 's/- 最終 Gate: `P2-S1`/- 最終 Gate: `P9-S9`/' \
    "$FX/valid-causal-detailed-phase.md" > "$causal_scaled_bad_gate/valid-causal-detailed-phase.md"
expect 1 "finalize: causal-v1 LCU Split の不存在最終 Gate FAIL" --finalize "$causal_scaled_bad_gate/valid-causal-scaled-overview.md"

rm -rf "$tmp" "$bundle" "$bundle2" "$bundle3" \
  "$causal_bundle" "$causal_split_bundle" "$causal_multi_step_bundle" \
  "$causal_bundle_bad_lcu" "$causal_bundle_extra_lcu" \
  "$causal_bundle_bad_master" "$causal_bundle_bad_todo_master" "$causal_bundle_copied_analysis" \
  "$causal_scaled_bundle" "$causal_scaled_bad_overview_tag" "$causal_scaled_bad_phase_todo" \
  "$causal_scaled_bad" "$causal_scaled_bad_ref" \
  "$causal_scaled_outline" "$causal_scaled_outline_missing_tag" "$causal_scaled_outline_bad_gate" \
  "$causal_scaled_bad_intermediate" "$causal_scaled_empty_model" \
  "$causal_scaled_second_hub" "$causal_scaled_copied_analysis" "$causal_scaled_bad_gate"

# ---- HTML ビュー生成のスモークテスト（再現性: 2回目は unchanged） ----
htmp=$(mktemp -d)
if python3 "$DIR/scripts/build_plan_html.py" "$FX/valid-full-plan.md" --output "$htmp/plan.html" > /dev/null 2>&1 \
   && [[ -s "$htmp/plan.html" ]] && grep -q "plan-manifest" "$htmp/plan.html" \
   && python3 "$DIR/scripts/build_plan_html.py" "$FX/valid-full-plan.md" --output "$htmp/plan.html" 2>/dev/null | grep -q "unchanged"; then
  echo "ok: HTML ビュー生成（再実行で unchanged）"
else
  echo "NG: HTML ビュー生成"
  fails=$((fails + 1))
fi
rm -rf "$htmp"

echo
if [[ $fails -eq 0 ]]; then
  echo "selftest: all passed"
  exit 0
else
  echo "selftest: $fails case(s) failed"
  exit 1
fi
