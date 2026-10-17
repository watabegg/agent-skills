# goal prompt: owner 整合性修復

## /goal 貼り付け用

```
owner 整合性修復を実装してください。
- 作業の正: valid-causal-scaled-overview.md
- 進捗台帳: valid-causal-scaled-todo.md
- 診断・モデル正本の LCU と postcondition に従うこと。
- 許可パス外または未登録 variant を発見したら停止して報告。
- 完了条件: todo 全行消し込み + 全 Hard Gate PASS + 逸脱報告。
- 実装は $codex-impl を使用。
```

## 正本参照ブロック

- 計画書: valid-causal-scaled-overview.md
- 進捗台帳: valid-causal-scaled-todo.md
- 診断・モデル正本: valid-causal-detailed-phase.md
- 許可パスリスト: `internal/ownership/**` — 計画成果物と開始前 dirty は除外
- コミット状態: 未コミット（テスト fixture）

## 事前回答（preflight）

- gap-check 回数: 1
- レビューループ上限: 3
- QA 環境: local

## 再開手順（別セッション・コンテキスト圧縮後）

1. todo と診断・モデル正本の現在 LCU を再読する
2. 24時間超または base branch 進行時は鮮度を再検証する

## 実装後フロー

1. PR 作成
2. レビューと QA
