# causal-v1 版の退避（2026-08-29）

2026-08-29 のリニューアルで置き換えた旧版（causal-v1 スキーマ版）の ARCHIVED.md と references 一式である。旧版が独立した skill として自動検出されないよう、SKILL.md は ARCHIVED.md に改名している。
Finding / Cause / LCU / variant の様式、lite / full / scaled のモード体系、lint finalize と HTML ビュー生成を前提としていた。

リニューアル後は、2026-03 時点の旧 markdown 版の構成へ戻した上で、計画書の正本を HTML（`references/plan-template.html` を直接記述）に変更し、Google Design Doc の観点（背景、代替案、セキュリティ・プライバシー・可観測性、ロールアウト）と画面変更一覧を追加した。lint と build_plan_html.py によるビュー生成は使わない。

## 現役のまま残しているもの

次は退避せず元のパスに残している。冒頭に `plan-schema: causal-v1` marker を持つ実装途中の計画書が、まだこれらを参照するためである。

- `scripts/lint_plan.sh`、`scripts/lint_plan.py`、`scripts/build_plan_html.py`、`scripts/selftest.sh`
- `assets/plan-template.html`
- `tests/fixtures/`

causal-v1 の計画がすべて完了したら、これらも削除してよい。
