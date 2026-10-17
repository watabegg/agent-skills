# goal prompt: owner projection と audit timestamp の独立修復

## /goal 貼り付け用

```
owner projection と audit timestamp の独立修復を実装してください。
- 作業の正: valid-causal-split-plan.md
- 進捗台帳: valid-causal-split-todo.md
- 各 LCU の authority、owner、Repair postcondition に従うこと。
- 未登録 variant は実装を広げず、todo を blocked にして報告すること。
- 完了条件: todo 全行消し込み + 全 Hard Gate PASS + 逸脱報告。
- 実装は $codex-impl を使用。
```

## 正本参照ブロック

- 計画書: valid-causal-split-plan.md
- 進捗台帳: valid-causal-split-todo.md
- 診断・モデル正本: valid-causal-split-plan.md
- 許可パスリスト: `internal/ownership/**`, `internal/audit/**` — 計画成果物と開始前 dirty は照合対象外
- コミット状態: 未コミット（テスト fixture）

## 事前回答（preflight）

- gap-check 回数: 1
- レビューループ上限: 3
- QA 環境: local

## 再開手順（別セッション・コンテキスト圧縮後）

1. todo 全文、診断・モデル正本の現在 LCU、現在 Step を再読する
2. 計画検証から24時間超、または base branch が進んだ場合は鮮度を再検証する

## 実装後フロー

1. PR 作成
2. レビューと QA
3. 新規 variant、LCU 境界、postcondition の逸脱有無を報告
