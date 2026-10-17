<!-- plan-mode: lite -->
<!-- plan-schema: causal-v1 -->
# 実装計画書 (lite): CSV header の正規化

- 作成日時: 2026-07-13 13:00（base: main@123abcd）
- 入力タイプ: corrective-single
- 診断・モデル正本: `valid-causal-lite-plan.md`
- 進捗台帳: `valid-lite-todo.md`
- lite 判定根拠: 変更 1 ファイル／禁止領域なし／diff 一文説明: 「BOM 付き header を読み取り時に正規化する」

## 1. 目的

BOM 付き CSV の先頭 header も通常の header と同じ名前で扱う。

- In scope: CSV reader の先頭 header 正規化
- Out of scope: delimiter 自動判定

## 2. 問題と解決モデル

- 観測事象: BOM 付き CSV だけ必須列が欠落したと判定される
- 原因: decoder が BOM を先頭 header の識別子へ含め、列 identity が入力表現に依存する
- 違反した不変条件: header identity は Unicode BOM の有無に依存しない
- 証拠: `go test ./internal/csvreader -run TestBOMHeader` が現状 fail
- 反証条件: decoder の出力から BOM が既に除去されていると test で確認できた場合
- 解決方針: reader 境界で先頭 header だけを正規化し、以後は正規名を authoritative identity とする
- 同型探索: `rg -n 'ReadAll|header' internal/csvreader` → reader は1入口、別 writer は0件

## 3. 変更対象ファイルと実装内容

| パス | 新規/更新 | 実装内容 |
| --- | --- | --- |
| `internal/csvreader/reader.go` | 更新 | 先頭 header から BOM を除去する |

## 4. 不変条件・逸脱禁止

- 触らないパス: `internal/export/**`
- やらないこと: delimiter 自動判定
- 許可パスリスト: `internal/csvreader/**` — 計画成果物と開始前 dirty は除外

## 5. Unclear Points

`Unclear Points: None（実装外として除外した論点: なし）`

## 6. TodoList

- [ ] `internal/csvreader/reader.go` BOM 正規化を追加（Gate: `go test ./internal/csvreader`）
- [ ] テスト: `go test ./internal/csvreader`

## 7. Definition of Done

1. `go test ./internal/csvreader` が exit 0
2. todo の全行が消し込まれている
