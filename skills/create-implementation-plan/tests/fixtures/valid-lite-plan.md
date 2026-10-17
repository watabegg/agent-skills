<!-- plan-mode: lite -->
# 実装計画書 (lite): README のタイポ修正

- 作成日時: 2026-07-11 12:00（base: main@abc1234）
- 進捗台帳: `valid-lite-todo.md`
- lite 判定根拠: 変更1ファイル（3以内）／ diff 一文説明: 「README の綴り誤りを2箇所直す」

## 1. 目的

README の綴り誤りを修正する。

- In scope: README.md の誤字
- Out of scope: 構成変更

## 2. 変更対象ファイルと実装内容

| パス | 新規/更新 | 実装内容 |
| --- | --- | --- |
| `README.md` | 更新 | 誤字2箇所を修正 |

## 3. 不変条件・逸脱禁止

- 触らないパス: `src/**`
- やらないこと: 文章の書き換え
- 許可パスリスト: `README.md`

## 4. Unclear Points

`Unclear Points: None（実装外として除外した論点: なし）`

## 5. TodoList

- [ ] `README.md` 誤字修正（Gate: `rg -n "teh" README.md` が0件）

## 6. Definition of Done

1. `rg -n "teh" README.md` の該当が0件
