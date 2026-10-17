# 因果分析と設計判断の原則

この文書は、観測された failure を個別パッチへ直結させず、本質的に同じ問題を一つの設計変更として閉じるための判断基準である。
一般的な用語では、次の組み合わせに近い。

- Causal Analysis and Resolution: failure から設計上の原因と再発防止へ遡る。
- Conceptual / Domain Modeling: コードより先に、事実、状態、同一性、責務、境界を定義する。
- Repair Pattern: 目標モデルを実現する具体的な修復機構を選ぶ。
- Variant Analysis: 既知の欠陥と同じ原因を持つ未知の兄弟事象を探す。
- Logical Change Unit: 同じ不変条件と postcondition で閉じる実装単位を定める。

「メンタルモデル」は日常語としては通じるが、このスキルでは上の異なる操作を一語へ押し込まない。

## failure、error state、cause を分ける

計画では次の階層を混同しない。

1. Finding: ユーザー、テスト、ログ、レビューから観測できる failure または未充足要求。
2. error state / 発生機構: failure の直前に内部で成立している誤った状態。
3. 近接原因: 今回の failure を直接発火させた条件。
4. 違反した不変条件: 本来、常に成立すべきだった条件。
5. 設計原因・寄与条件: 不変条件の違反を許したデータ表現、責務、境界、writer、状態遷移。

初期入力の単位は、issue や報告書という文書単位ではなく、独立して検証できる主張である。
一つの報告に独立した failure が複数あれば、先に I-01、I-02 へ分ける。
`corrective-bundle(N)` の N は2以上とし、各 Input Ref を重複なく一つの Finding へ対応させる。
複数入力を一つの Finding に正規化してよいが、入力自体を落としてはならない。
variant 探索で発見した Finding は、その Variant ID を provenance として持つ。

`because` を繰り返して原因候補を作るのはよい。
しかし「所有権の問題」「状態管理の問題」「境界の問題」のような傘語へ収束しただけでは原因を特定したことにならない。

採用 Cause には、コード位置、検索結果、テスト、ログなどの証拠と、仮説が誤りだと判断できる falsifier を置く。
証拠のない断定も、反証不能な説明も実装根拠にしない。

因果関係は多対多でよい。
一つの Finding に複数 Cause が寄与することも、一つの Cause が複数 Finding を生むこともある。
分析を排他的な分類へ変形しない。

## 解決の概念モデルを先に定める

原因が分かった後も、すぐ `if`、hook、reconcile job へ落とさない。先に次を定義する。

- authoritative source: どの事実が正か。
- derived projection: 何が正から導出される表示・キャッシュ・索引・関係か。
- owner / writer: 誰だけがその状態を変更できるか。
- identity / boundary: 同一性、集約、tenant、transaction の境界はどこか。
- state / time semantics: current、履歴、削除、競合をどう解釈するか。
- invariant: モデルの下で常に成立する条件は何か。

たとえば ownership bug を「代入漏れ」と見ると、欠けた write path ごとに patch が増える。
「authoritative membership から current owner projection を導出する」とモデル化すれば、同じ不変条件を一つの enforcement point で復元できる可能性が見える。

ここで重要なのは reconcile という名前ではなく、authority、projection、owner、postcondition が一貫することである。
reconcile はそのモデルを実現する repair pattern の一つにすぎない。

## 修復モデルで実装意味論を固定する

Repair には次を明示する。

- authoritative / observed state: 何と何を比較するか。
- mechanism: 何を計算し、どの状態を書き換えるか。
- trigger: request、event、schedule、migration のどこで起動するか。
- enforcement point: 不変条件をどこで一元的に強制するか。
- transaction boundary: どこまでを原子的に扱うか。
- postcondition: 完了後に何が成立するか。
- idempotency / concurrency: 再実行と競合の意味は何か。
- cleanup / migration: 既存の誤状態をどう直すか。
- retired paths: 旧 writer、旧 predicate、重複ロジックをどう廃止するか。

非該当項目は空欄や `N/A` にせず、なぜ対象外かを書く。これにより未検討と本当の非該当を区別する。

Repair の postcondition は Finding の期待動作を覆い、Hard Gate で観測できなければならない。
実装方式を説明できても postcondition が書けない場合、変更の閉じ方がまだ曖昧である。

## Logical Change Unit を決める

LCU は次の条件をすべて満たす範囲である。

1. 同じ違反不変条件または強く結合した不変条件を扱う。
2. 同じ Target Model を採用する。
3. 同じ Repair postcondition で子症状が消える。
4. 一つの変更単位として実装し、機械的に検証できる。

同じファイル、同じ endpoint、同じレイヤー、同じ傘語だけでは併合しない。
逆に、複数 helper や複数レイヤーへ変更が広がっても、一つの不変条件を実現するなら同じ LCU でよい。

M=N は失敗ではない。独立した原因と postcondition を持つ Finding は分離する。束ねること自体を目的にしない。

因果関係は多対多のまま維持し、Primary Finding の LCU 所属だけを排他的にする。これは分析上の関係と、実装責任の割り当てを混同しないためである。

共有 Step は例外である。一つの Primary LCU を持たせ、他の LCU を Shared として理由付きで参照する。共有という語で別の症状を雑に束ねない。

一つの LCU は複数 Step や phase にまたがってよい。その場合も全 Step は同じ Primary LCU を参照し、最後の Gate が LCU 全体の Repair postcondition を閉じる。

## Variant Analysis で未知の兄弟事象を探す

報告済みの場所だけを直すと、同じ設計原因を持つ未報告箇所が残る。各 LCU について、少なくとも次から該当する軸を選んで検索する。

- 同じ predicate や current 判定。
- 同じ状態への writer。
- 同じ状態遷移。
- 同じ設計判断の複製。
- 別 endpoint、job、event consumer などの入口。
- 新しい enforcement point を迂回する旧経路。

検索コマンドと scope、件数、include / exclude の判断を残す。
0件も結果である。
Seed は既知の Finding、Cause、Model、Repair、LCU のいずれか一つとし、初期 Input Ref を持つ Finding へ遡れるものを選ぶ。
variant が発見した Finding 自身を Seed にして循環正当化しない。
include した variant は Finding 台帳と Primary LCU へ追加する。
exclude には、別の authority や postcondition を持つなどの分離理由を書く。

## アプローチ比較は必要な判断だけで行う

グローバルに2〜4個の「メンタルモデル」を捏造しない。
要求、既存契約、証拠から方式が一つに定まる場合は、具体的な理由を書いて比較を省略する。

現実的な案が複数残る場合だけ、判断または LCU 単位で比較する。

1. 要求、不変条件、安全性を満たさない案を先に落とす。
2. 本当に差が出る軸を3〜6個抽出する。
3. 軸と案を同じ表で ◎○△× により比較する。
4. 各案の歪みと、その案が勝つ現在条件を書く。
5. 採用の決め手となった軸を残す。

Pros / Cons は各案の説明を並べるだけになりやすいため使わない。比較表を置くこと自体を品質と見なさない。

Design It Twice は「必ず非採用案を1件書く」という帳票規則ではない。
インターフェース、データモデル、エラー意味論などに本物の自由度があるとき、複数案を検討する規律である。
存在しない代替案を捏造しない。

## 複雑性と境界の判断

要求と安全性を満たす候補の中では、次の複雑性を減らす。

1. 変更増幅: 一つの仕様変更が多数箇所へ波及する。
2. 認知負荷: 安全な変更に覚えるべき知識が多い。
3. 未知の未知: どこを直すべきか、何を知るべきかが分からない。

設計時には次も確認する。

- 深いモジュール: 単純な interface の裏へ判断を隠す。
- 情報隠蔽: 同じ設計判断を複数 module へ漏らさない。
- temporal decomposition の回避: 実行順を module 境界にしない。
- complexity downward: 呼び出し側へ設定と例外処理を押し付けない。
- error elimination: postcondition の意味論を変えてエラー自体を消せないか検討する。

## スコープを守る

- 頼まれていない機能、単一用途の抽象、推測ベースの設定可能性を入れない。
- 変更する行は Finding、LCU、またはその Gate へ遡れるようにする。
- 隣接コードのついで改善を計画へ混ぜない。
- 許可パスリスト外へ出たら停止し、黙ってスコープを広げない。

## Red Flags

| 兆候 | 見直す点 |
| --- | --- |
| Cause が傘語だけ | 不変条件、発生機構、証拠、falsifier まで遡る |
| パターン名が先に決まる | authority と Target Model を先に書く |
| 複数 Finding が根拠なく1対1の Stepへ並ぶ | LCU の併合条件を再検討する |
| 一つの LCU に無関係な症状が入る | postcondition と検証単位で分離する |
| 報告箇所しか検索していない | variant の writer、predicate、入口を探索する |
| 比較表が全案ほぼ同点 | 比較軸か候補が人工的である |
| 名前や説明が長い | Model または module boundary が曖昧である |
| 同じ判断が複数 Step に散る | enforcement point と owner を一元化する |
