<!-- plan-mode: scaled-overview -->
<!-- plan-schema: causal-v1 -->
# 実装計画書 (scaled): [topic] — Overview

- 作成日時: [YYYY-MM-DD HH:MM]（base: [branch]@[short-sha]）
- 入力タイプ: [corrective-single / corrective-bundle(N>=2) / feature-gap]
- scaled 判定根拠: [Step 8以上 / Todo 26件以上 / 変更31ファイル以上]
- 進捗台帳: `[docs/plans/<prefix>-<topic>/<topic>-todo.md]`
- goal prompt: `[docs/plans/<prefix>-<topic>/<topic>-goal-prompt.md]`
- 事前調査・読了ファイル: `[docs/plans/<prefix>-<topic>/01-research.md]`
- 診断・モデル正本: `[docs/plans/<prefix>-<topic>/10-phase1-<name>.md]`
- HTML ビュー: `[docs/plans/<prefix>-<topic>/00-overview.html]`（finalize が生成）
- 改訂履歴: [YYYY-MM-DD HH:MM 初版]
- このファイルは80行以内を目安にする（120行超は lint 失格）。

## 1. 目的

[達成すること。1〜3文]

- In scope: [対象]
- Out of scope: [対象外]
- 解決モデル: [authoritative source、owner、修復 postcondition を一文で]
- LCU: [N] findings → [M] LCU

## 2. 不変条件・逸脱禁止（全フェーズ共通）

- 触らないパス: [`path/**`]
- 変えない挙動・UI: [項目]
- やらないこと: [Out of scope の転記]
- 許可パスリスト: [`path/**` — `git diff --name-only` が外へ出たら停止。計画成果物と開始前から dirty だったファイルは除外]

## 3. フェーズ一覧

| # | フェーズ | 対象 LCU | 状態 | ファイル | 完了 Gate |
| --- | --- | --- | --- | --- | --- |
| 1 | [name] | LCU-01 | detailed | `10-phase1-[name].md` | `[command]` → [expect] |
| 2 | [name] | LCU-02 | outline | `20-phase2-[name].md` | `[command]` → [expect] |

フェーズ境界は LCU 境界へ合わせる。同じ LCU を複数フェーズへ分ける場合は、依存理由と最終 postcondition Gate を診断・モデル正本に書く。

## 4. Unclear Points

`Unclear Points: None（実装外として除外した論点: なし）`

または Point / Why / Impact / Need / Fallback / 推奨で列挙する。

## 5. Resolved Points

| # | 論点 | 決定 | 根拠 | 反映先 |
| --- | --- | --- | --- | --- |
| 1 | [論点] | [決定] | [引用] | [フェーズ/セクション] |

## 6. TodoList（フェーズ単位）

正本は todo.md。detailed フェーズの細目は各フェーズファイルから todo.md へ転記する。

- [ ] Phase 1 (LCU-01): [name]（Gate: `[command]`）
- [ ] Phase 1 完了時: Phase 2 を detailed に詳細化
- [ ] Phase 2 (LCU-02): [name]（Gate: `[command]`）

各 Phase の括弧タグは、フェーズ一覧の対象 LCU と過不足なく一致させる。

## 7. Definition of Done

1. todo.md の全行が消し込まれている
2. cancelled でない全フェーズの完了 Gate が PASS
3. `[実環境動作確認コマンド]` が成功
4. 新規 variant、LCU 境界変更、postcondition 変更の有無が完了報告にある
