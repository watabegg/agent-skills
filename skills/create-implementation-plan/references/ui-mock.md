# UI Mock

フロントエンドの変更がある実装計画で、新 UI のイメージを実装前に揃えるための静的モックの作り方。使用モデルはユーザーが指定する。未指定なら質問して回答を待ち、確定したモデルでモックを作成する。

## 目的と位置づけ

- 読み手（依頼者・レビュアー・実装者）が同じ絵を見て「これで作る」と決めるための資料。仕様の正は計画書、表示の正はモック。
- 承認前は提案。承認後は `画面変更一覧` から「表示の正」として参照し、実装エージェントに渡す。
- モックは静的 HTML 1 枚。動く UI を作らない。状態ごとの枠を並べて見せる。

## 並列実行の手順

1. 4.（設計判断）と 4.6（画面変更一覧の下書き）が固まり、ユーザーが使用モデルを指定してからモック作成を依頼する。モデル未指定なら質問し、回答待ちの間はモック以外の作業を続ける。
2. 委ねるときに渡すもの: 出力パス、対象画面と URL path、設計判断、画面変更一覧の下書き、使う seed データ（架空）、状態の一覧（3〜4 枠）、注記に書く内容、取得済みのスクリーンショット、既存デザイン部品の見た目（shadcn 等、実画面の色、余白、文字サイズ）。CLI は親の会話を引き継がないため、必要な内容を依頼文にまとめ、参照ファイルは絶対パスで渡す。
3. 自分は残りのセクション（API、DB、状態遷移、実装ステップ、テスト、Hard Gate）を書き進める。
4. 完了後、モックと設計判断の一致を確認する。計画書一式の作成後に [日本語の仕上げ](japanese-rewrite.md) を行う場合はその反映を待ち、実施の有無によらず最終版のモックを描画して崩れ（注記ピンのずれ、はみ出し）を直す。
5. 承認前は計画書の `画面変更一覧` 冒頭に「モック案 `<path>`（承認待ち）」と書いてリンクする。レビュー用の公開は SKILL.md の `納品と実装フェーズ` に従う。納品メッセージで承認を求め、承認後に「表示の正はモック `<path>`（承認日）」へ更新する。

## 指定モデルでの実行

- 同じタスクでユーザーが既にモデルを指定していれば、その指定を使い再質問しない。指定がなければ、利用可能と確認できたモデルを候補にして質問し、回答を待つ。
- ユーザーの指定に対応するモデル ID と実行経路を確認する。曖昧で一意に決まらない場合は質問する。別モデルや別バージョンへ勝手に読み替えない。
- CLI の `--model` など、その実行環境で確認したモデル指定引数、または Agent ツールの `model` 引数に確定したモデルを明示する。必要な調査結果と参照ファイルを渡す。
- 指定モデルの実行が確認できない経路は使わない。CLI がない、モデルが利用できない、呼び出しが失敗する場合は、原因を伝えて代替モデルの指定を求める。既定モデルや親自身へ自動で切り替えない。
- 実行の終了コード、応答エラー、モデル情報を確認し、指定モデルで作成できたことを記録する。指定と実際のモデルが一致しない結果を完成品として扱わない。
- 応答本文の HTML を親が指定先へ保存する。書き込みはモックの出力先に限定する。親自身が作成する場合も、現在のモデルをユーザーが明示的に選択し、そのモデルを確認できる場合に限る。

依頼文には次の制約を含める。

```text
添付した調査結果、設計判断、画面変更一覧、スクリーンショットとデザイン部品を根拠に、
変更後の UI を表す静的 HTML モックを1枚作成してください。
指定された状態と番号付き注記を含め、既存アプリの見た目に合わせてください。
仕様が未確定の点は未確定と示し、機能や仕様を追加しないでください。
データは指定した架空データだけを使ってください。
成果物はモックだけです。アプリの実装や計画書の編集は行わないでください。
返答の本文には完全な HTML だけを含め、コードフェンスや前後の説明を付けないでください。
```

## 中身の構成

1. 見出し: 対象画面、URL path、根拠（計画書のパス）、方針を 1 行ずつ。
2. 状態の枠（3〜4 枠、縦に並べる）:
   - 変更後の一覧（まだ操作していない）
   - 操作中（モーダル、フォーム、ドロワーなど。実装で縦スクロールするものは全項目を展開して見せる）
   - 保存後・完了後
   - 破壊的操作の確認ダイアログ（あれば）
3. 各枠の右に番号付き注記。番号は枠の左端のピンと対応させ、「何が変わるか・条件・エラー時」を書く。
4. 末尾に「齟齬がないか見てほしい点」: 実装前に決めたい判断（項目の出し方、色、文言、操作の要否）を番号で列挙する。ここが承認時の議論の入口になる。
5. 凡例（状態チップの色など）。

## 見た目の規範

- 既存アプリのデザインシステムに合わせる（フォント、罫線、角丸、文字サイズ、ボタンの形）。新しい識別色は状態を表す最小限（例: 確定=緑、上書き=空色、要確認=琥珀）に留める。
- 既存アプリがライトテーマ固定ならモックもライト固定でよい。その場合も背景と文字色は明示的に塗る。
- データは seed の架空データを使う。実案件名、実在の人名、token、URL の秘密を入れない。
- 実装で使う文言をそのまま書く（ボタン名、説明文、エラー文、確認ダイアログの文）。モックの文言が実装の文言になる。
- 数字は `font-variant-numeric: tabular-nums`。単位（円、名）は入力欄の右端。

## HTML の骨格

```html
<title>[画面名] モック</title>
<style>
:root{--page:#F6F6F4;--surface:#FFF;--border:#E4E4E7;--ink:#18181B;--ink-2:#52525B;--ink-3:#71717A;--primary:#18181B;--pin:#B03A2E}
body{margin:0;background:var(--page);color:var(--ink);font-family:-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif;font-size:14px;line-height:1.7}
.frame{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:24px;margin:0 0 44px}
.stage{position:relative;background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px}
.pin{position:absolute;width:22px;height:22px;border-radius:50%;background:var(--pin);color:#fff;font-size:11px;font-weight:800;display:grid;place-items:center}
.notes{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.notes li{position:relative;padding:9px 12px 9px 40px;background:var(--surface);border:1px solid var(--border);border-radius:9px;font-size:12.5px}
.notes li::before{content:attr(data-n);position:absolute;left:10px;top:9px;width:22px;height:22px;border-radius:50%;background:var(--pin);color:#fff;font-size:11px;font-weight:800;display:grid;place-items:center}
.overlay{position:absolute;inset:0;background:rgba(24,24,27,.42);border-radius:12px}
.modal{position:relative;z-index:2;width:min(640px,100%);margin:0 auto;background:var(--surface);border:1px solid var(--border);border-radius:14px;box-shadow:0 24px 60px rgba(0,0,0,.28);padding:20px 22px}
</style>
<section class="frame">
  <div class="frame-head">状態 1: [変更後の一覧]</div>
  <div class="stage">
    <span class="pin" style="left:-10px;top:8px">1</span>
    <!-- 既存アプリの部品に合わせたマークアップ -->
  </div>
  <ol class="notes"><li data-n="1"><b>[対象 UI]</b><span>[何が変わるか・条件]</span></li></ol>
</section>
```

## 承認後にやること

1. 承認で決まった文言・配置・色・状態を計画書の `画面変更一覧` と該当 Step の変更ファイル表に反映し、改訂履歴に 1 行残す。
2. todo.md のヘッダにモックのパスを「表示の正」として追記する。
3. 実装エージェントへの指示にモックのパスを含め、「文言・配置・色はモックに合わせる」と明記する。
