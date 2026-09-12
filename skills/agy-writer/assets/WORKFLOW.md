# 日本語文書の執筆

Codex・Claude からは `agy-writer` skill を使う。依頼元が事実と設計を確定し、Gemini が章ごとに執筆・校閲する。依頼元は完成稿を材料と照合し、同じ会話で修正後の確認まで続ける。

```sh
agent-write-ja chapters --packet packet.md --plan chapters.json --out draft.md
```

短い文書は `--plan` を省略すると、一章分として執筆・校閲する。章分けの指定方法は skill の `references/chapters.md` を参照する。

直接対話する場合は `agy-writer` で起動する。Gemini 向けの指示と語彙の好みは、このフォルダの `GEMINI.md` にまとめている。

起動スクリプトと設定の管理元は `~/agent-skills/skills/agy-writer`。編集後はリポジトリから次のコマンドで配置へ反映する。

```sh
python3 scripts/sync_installed_skills.py --only agy-writer --writer-runtime --apply
```

詳しい配置と保守の手順は、管理元の `references/setup.md` を参照する。
