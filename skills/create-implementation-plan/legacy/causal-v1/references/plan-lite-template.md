<!-- plan-mode: lite -->
<!-- plan-schema: causal-v1 -->
# 実装計画書 (lite): [topic]

- 作成日時: [YYYY-MM-DD HH:MM]（base: [branch]@[short-sha]）
- 入力タイプ: [corrective-single / feature-gap。corrective-bundle は full にする]
- 診断・モデル正本: `[この plan.md 自身のリポジトリ相対パス]`
- 進捗台帳: `[docs/.../<prefix>-<topic>-todo.md]`
- HTML ビュー: `[docs/.../<prefix>-<topic>-plan.html]`（finalize が生成）
- lite 判定根拠: 変更 [N] ファイル（3以内）／禁止領域なし／diff 一文説明: 「[一文]」

## 1. 目的

[達成すること。1〜2文]

- In scope: [対象]
- Out of scope: [対象外]

## 2. 問題と解決モデル

lite でも観測事象から修正行へ飛ばない。原因が仮説なら、証拠と反証条件を併記する。

- 観測事象: [再現可能な failure または未充足要求]
- 原因: [近接原因ではなく、違反を許した設計・状態表現・境界]
- 違反した不変条件: [常に成立すべき条件]
- 証拠: `[path:line、検索、テスト、ログ]`
- 反証条件: [原因仮説が誤りだと判定できる観測]
- 解決方針: [authoritative source、owner、postcondition が分かる一文]
- 同型探索: `[rg -n '...' path]` → [件数と include / exclude の結果]

## 3. 変更対象ファイルと実装内容

| パス | 新規/更新 | 実装内容 |
| --- | --- | --- |
| `[path]` | 更新 | [変更内容] |

## 4. 不変条件・逸脱禁止

- 触らないパス: [`path/**`]
- やらないこと: [Out of scope の転記]
- 許可パスリスト: [`path/**` — `git diff --name-only` が外へ出たら停止。計画成果物と開始前から dirty だったファイルは除外]

## 5. Unclear Points

`Unclear Points: None（実装外として除外した論点: なし）`

または Point / Why / Impact / Need / Fallback / 推奨で列挙する。

## 6. TodoList

正本は todo.md。

- [ ] `[path]` [やること]（Gate: `[command]`）
- [ ] テスト: `[command]`

## 7. Definition of Done

1. `[実行コマンド]` が exit 0
2. todo.md の全行が消し込まれている
