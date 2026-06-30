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
    └── pencil-pencli/
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
  - 日本語の質問回答、技術文書、設計、レビュー、作業報告の文章規範を定める。
  - 段落構成、論証の厳密さ、読み手の負荷、LLM っぽい表現の抑制を扱う。
- 出典:
  - この Skill は k16shikano 氏の Gist「日本語技術文書の文章規範」（https://gist.github.com/k16shikano/fd287c3133457c4fd8f5601d34aa817d）を元にしたものです。
  - この repo では Codex Skill として使うための frontmatter と、個人運用上の調整を加えています。
- 主な利用シーン:
  - 日本語で回答する通常の技術相談。
  - 技術書、記事、設計文書、コードレビュー、調査報告の執筆や推敲。

### `pencil-pencli`

- 目的:
  - Pencil の `.pen`、`.pencli`、`design.pen` を headless CLI で安全に扱う。
  - 暗号化された Pencil ファイルを通常の filesystem read、grep、cat、Python で読まない運用を徹底する。
- 主な利用シーン:
  - Pencil canvas の確認、編集、validation、export。
  - `pencil interactive` を使った design file の headless 操作。

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
