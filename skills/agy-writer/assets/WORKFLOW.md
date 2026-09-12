# 日本語文書の執筆

Codex・Claude からは `agy-writer` skill を使う。依頼元が事実と設計を確定し、Gemini が日本語の文章を書き、依頼元が材料との意味の一致を確認する。

```sh
agent-write-ja draft --packet packet.md --out draft.md
```

直接対話する場合は `agy-writer` で起動する。Gemini 向けの指示と語彙の好みは、このフォルダの `GEMINI.md` にまとめている。

起動スクリプトと設定の管理元は `~/agent-skills/skills/agy-writer`。編集後はリポジトリから次のコマンドで配置へ反映する。

```sh
python3 scripts/sync_installed_skills.py --only agy-writer --writer-runtime --apply
```

詳しい配置と保守の手順は、管理元の `references/setup.md` を参照する。
