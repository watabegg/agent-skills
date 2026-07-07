# agent-skills

個人用の Codex Skill をまとめるリポジトリです。
このリポジトリ内の `skills/<skill-name>/SKILL.md` を起点に、各 Skill の手順、スクリプト、テンプレートを利用します。

## 取り込み方針

この repo は public 前提です。既存のインストール済み skill のうち、次の条件に合うものだけを入れています。

- Git 管理元が見つからないもの
- 既存の Git 管理元と実質的な差分があるもの
- 公開しても秘密値、社内運用情報、本番環境情報を含まないもの

`~/.codex/skills/.system` と plugin cache 配下の skill は配布元管理のため対象外です。会社やプロダクト固有の本番運用 skill は、既存の private repo 側で管理します。

## ディレクトリ構成

```text
.
├── AGENTS.md
├── README.md
└── skills/
    ├── japanese-tech-writing/
    ├── pencil-pencli/
    ├── ealps-moodle-operator/
    ├── herdr-dev-loop/
    └── shinshu-portal-auth/
        └── env.example
```

## Codex への依頼でグローバルインストールする

インストール先は通常 `~/.codex/skills` です。
`CODEX_HOME` が設定されている場合は `$CODEX_HOME/skills` を優先します。

### 依頼例 1. 全 Skill をインストール

```text
このリポジトリの skills をグローバル環境に全部インストールして。
CODEX_HOME があればそちらを優先して、なければ ~/.codex を使って。
最後にインストールされた SKILL.md の一覧を表示して。
```

### 依頼例 2. 特定 Skill だけインストール

```text
skills/japanese-tech-writing だけをグローバル環境にインストールして。
既存の同名 Skill があれば上書きして、完了後に確認結果を教えて。
```

### 依頼例 3. インストール状態だけ確認

```text
グローバル Skill ディレクトリの SKILL.md 一覧を確認して、
このリポジトリの Skill が反映されているかチェックして。
```

### 依頼例 4. 反映されないとき

```text
Skill を入れ替えたので、再読み込みが必要か確認して。
必要なら Codex 再起動の手順も案内して。
```

## 各 Skill の説明

### `japanese-tech-writing`

- 目的:
  - 日本語の長文・高負荷な技術文書、設計 Markdown、仕様書、レビュー、調査報告、推敲の文章規範を定める。
  - 段落構成、論証の厳密さ、読み手の負荷、LLM っぽい表現の抑制を扱う。
  - 短い進捗報告、単純な質問回答、通常の会話では使わない。
- 出典:
  - この Skill は [k16shikano 氏の Gist「日本語技術文書の文章規範」](https://gist.github.com/k16shikano/fd287c3133457c4fd8f5601d34aa817d) を元にしたものです。
  - この repo では Codex Skill として使うための frontmatter と、個人運用上の調整を加えています。
- 主な利用シーン:
  - 設計書、仕様書、実装計画、技術記事の執筆や推敲。
  - コードレビュー、調査報告、PR 向け説明のような、読み手の判断に使われる長めの日本語文書。

### `pencil-pencli`

- 目的:
  - Pencil の `.pen`、`.pencli`、`design.pen` を headless CLI で安全に扱う。
  - 暗号化された Pencil ファイルを通常の filesystem read、grep、cat、Python で読まない運用を徹底する。
- 主な利用シーン:
  - Pencil canvas の確認、編集、validation、export。
  - `pencil interactive` を使った design file の headless 操作。

### `shinshu-portal-auth`

- 目的:
  - 信州大学 ACSU 認証が必要な eALPS、時間割ポータル、キャンパス情報システム、SharePoint ACSU サイトを、秘匿値を出力せずにブラウザ自動化で開く。
  - Tampermonkey に依存せず、ACSU ID/password、WisePoint 画像パスワード、Shibboleth 同意、SharePoint の Microsoft サインインから ACSU へのフェデレーションを扱う。
  - DOM 構造、リンク、ボタン、入力、テーブル、スクリーンショットを調査用に要約する。
- 主な利用シーン:
  - `*.ealps.shinshu-u.ac.jp`、`gakumu-web02.shinshu-u.ac.jp/campus`、`shinshuuniversity.sharepoint.com` のページを開いて、課題、時間割、学務情報、ACSU SharePoint リンクを確認する。
  - `ACSU_LOGIN_ID`、`ACSU_LOGIN_PASSWORD`、`ACSU_LOGIN_MULTIFACTOR` を `~/.config/shinshu-portal-auth/env` などから読み、値をログや commit に残さず調査する。
- 秘匿値の置き場所:
  - `skills/shinshu-portal-auth/env.example` を `~/.config/shinshu-portal-auth/env` にコピーして実値を入れる。
  - `chmod 600 ~/.config/shinshu-portal-auth/env` を設定する。
  - 別ファイルを使う場合だけ `SHINSHU_AUTH_ENV` または `--env-file` で指定する。
  - この repo は public 前提なので、実値入りの `.env`、`env`、Cookie、Chrome profile は commit しない。

### `ealps-moodle-operator`

- 目的:
  - `shinshu-portal-auth` で eALPS にログインした後の Moodle 操作を扱う。
  - コースセクション、課題、資料、小テストの確認、Moodle filemanager 経由のファイル提出、小テストの回答保存と最終送信、提出状態の検証を扱う。
  - 提出や小テスト送信のような副作用のある操作は、ユーザーが明示した場合だけ実行する。
- 主な利用シーン:
  - eALPS の課題一覧を確認して、ローカルに問題文や提出用コードを整理する。
  - 課題ファイルの提出、小テスト回答、提出後の `評定のために提出済み` や `ステータス 終了` の確認を行う。
  - `/tmp` に保存した Moodle 操作 JSON から、提出状況を表形式で要約する。

### `herdr-dev-loop`

- 目的:
  - Herdr 上で Manager / Worker / Gap Auditor / Reviewer の Codex agent を、git worktree、integration branch、`.ai/loop` artifact によって安全に協調させる。
  - Worker / Reviewer は既定でこの Skill 内蔵の HLoop protocol を使い、外部の `$codex-impl` / `$codex-review-multi-v2` は必要時だけ選ぶ互換モードにする。
  - Gap Auditor には元 repo の plan/spec と統合ブランチ実装の相違確認を担当させ、仕様判断が必要な場合は `DECISIONS.md` と `USER_ACTION_REQUIRED.md` に分離して止める。
  - `scripts/hloop` で init、task 作成、Worker/Gap Auditor/Reviewer 起動、harvest、merge、validation、pump、triage、report を実行する。
  - `.ai/loop/PROFILE.md` で branch strategy、Worker protocol、Review protocol、Review lanes、Worker QA profile、Manager final QA profile をプロダクトごとに調整する。
  - Worker / Gap Auditor / Reviewer は対話式 Codex TUI を既定にし、Gap Auditor と Reviewer は detached worktree で最終 Markdown artifact だけを書き込む。
  - 既定では最大3 Workerを並列に走らせ、Reviewは検証済みmergeごと、Gap Auditorは低頻度に走らせる。
  - `pump` で安全な tick を複数回drainし、Review/Gapの指摘は `triage` でfix-task draftにしてからManager承認後にqueued task化する。
  - 元 plan/spec だけでは判断できない仕様判断は `.ai/loop/DECISIONS.md` に記録し、ユーザー判断が必要なものは `USER_ACTION_REQUIRED.md` に分離する。
- 主な利用シーン:
  - `/goal` や大きめの実装依頼を、複数 Codex agent に分割して進めたいとき。
  - Worker の書き込み範囲、Gap Auditor の plan/spec coverage gate、Reviewer の review artifact 境界、merge gate、validation gate をファイルベースで固定したいとき。
  - Herdr pane id や Codex CLI の挙動を `hloop doctor` で確認しながら、bounded tick で運用したいとき。
- 注意:
  - 実プロジェクトで生成される `.ai/loop`、pane transcript、秘密値、社内 URL、本番運用情報はこの public repo に commit しない。

## Skill を追加する方法

### 1. Skill ディレクトリを作る

```bash
mkdir -p skills/<new-skill>/{agents,scripts,references}
```

最低限 `skills/<new-skill>/SKILL.md` を作成してください。
`agents/`、`scripts/`、`references/` は必要に応じて追加します。

### 2. `SKILL.md` を作成する

先頭に front matter を置き、利用目的を明確化します。

```md
---
name: <new-skill>
description: <このSkillの目的と、どんな依頼で使うか>
---
```

### 3. 動作確認する

```bash
python3 /home/watabegg/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/<new-skill>
```

public repo に置く前に、秘密値、cookie、token、社内 URL、本番運用手順が混ざっていないか確認してください。

### 4. グローバル環境へ反映する

手動コピーではなく、Codex に次のように依頼して反映してください。

```text
追加した skills/<new-skill> をグローバル環境にインストールして。
完了後に SKILL.md 一覧で反映確認して、必要なら再起動が必要か教えて。
```
