# Visual Planning

フロントエンド変更の画面説明と、複数 Step の実装順を視覚化するときに使う。

## 実画面スクリーンショット

### 取得元の優先順位

1. 対象 commit と一致する Preview または検証環境
2. 対象 commit のローカル fixture / seed 環境
3. 対象 commit で取得済みの QA 証跡
4. Storybook または静的 fixture

対象 commit と一致しない画像は、現行画面の参考としてのみ使い、その差をキャプションへ書く。
画面へアクセスできない場合は理由を明記し、架空画面や別機能の画像で埋めない。

### 保存と証跡

- 保存先は計画書と同じディレクトリの `<topic>-assets/screenshots/` とする。
- HTML の `img src` はリポジトリ相対パスにする。
- キャプションへ環境、取得日時、URL path、対象 SHA を書く。
- 認証 query、cookie、token、個人情報を画像やキャプションへ残さない。
- production の実データより Preview / fixture の架空データを優先する。

### 赤入れ

元画像は証跡として保持し、赤枠やコメントを画像ファイルへ焼き込まない。
HTML で次を重ねる。

- `position:absolute` の赤枠
- 赤枠と同じ番号を持つマーカー
- 番号、対象 UI 名、変更内容、表示条件を持つコメント
- 視覚変更がない挙動変更では「表示は不変」「保存時の request / error behavior が変わる」と明記

パーセント座標を使い、画像幅が変わっても注釈が追従するようにする。
モバイルでは画像とコメントを1列にする。
色だけで対応を示さず、番号と対象 UI 名を必ず併記する。

### セクションごとの役割

`既存実装の理解` では、現行フローの説明そのものに画面と注釈を組み込む。

- ユーザー操作の入口
- API request を作る入力範囲
- DB の現在値を表示する領域
- 履歴または副作用を確認できる領域
- 現在のエラー表示や保存境界

`画面変更一覧` では、同じ画面上で将来の差分を注釈する。

- 表示や配置が変わる箇所
- request / validation / error behavior が変わる操作
- 新しい表示条件
- 変更しない UI と、そのまま残す理由

説明の末尾に「現行画面で追う既存フロー」のような独立小節を追加しない。
同じスクリーンショットを再利用してよいが、現行フローの説明と将来差分の説明を一つのコメントへ混ぜない。

`Problem と Solution` では、画像の番号ごとに AS-IS と TO-BE を並べる。
AS-IS は現在起きることと困りごと、TO-BE は変更後の挙動と利用者への効果を書く。
API 名や DB 列名は、問題の理解に必要な場合だけ使う。

最小構造:

```html
<div class="screen-redline">
  <figure class="shot">
    <div class="shot-stage">
      <img src="[relative screenshot path]" alt="[画面名と状態]">
      <span class="redbox" style="left:5%;top:20%;width:90%;height:18%"></span>
      <span class="pin" style="left:2%;top:18%">1</span>
    </div>
    <figcaption>[environment] / [path] / [sha] / [captured at]</figcaption>
  </figure>
  <ol class="annotation-list">
    <li><strong>1. [対象 UI]</strong><span>[変更内容と条件]</span></li>
  </ol>
</div>
```

## 実装ステップの視覚化

全 Step の依存図や要約カードは作らない。
各 Step の詳細カードに情報を集約する。

カードは次の順で構成する。

1. Step 番号、名前、対象レイヤー、目的
2. 変更ファイル表
3. 実装手順
4. 必要な場合だけ、その Step の Sequence Diagram または flowchart
5. DoD
6. 失敗時の切り分け
7. Appendix B の Hard Gate 参照

図は複数コンポーネント間の呼び出し、transaction、非同期処理、エラー分岐を説明できる場合に使う。
単一ファイルの単純な編集を飾るためには使わない。
図中の処理順とカードの実装手順を一致させる。
