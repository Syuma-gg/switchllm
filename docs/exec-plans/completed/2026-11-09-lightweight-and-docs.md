# 軽量化とドキュメントの構造化

- 完了日: 2026-11-09

## 目的

公開に向けて、機能の約束を保守し続けられる範囲に絞る。あわせて、設計の情報を構造化した docs にまとめる。

## 手順

- [x] `@tool` の自動変換を `SUPPORTED_TYPES` に固定した。対応外の型は `TypeError`（以前は黙って文字列扱い）。`@tool(parameters=...)` を追加した（[判断](../../design-docs/tool-schema-scope.md)）
- [x] プリセットを13個から7個にした（[判断](../../design-docs/verified-presets.md)）
- [x] `register_adapter()` を削除し、`unillm.providers` を内部実装と明記した（[判断](../../design-docs/minimal-public-api.md)）
- [x] 旧 `docs/HANDOFF.md` と README の設計説明を、design-docs / product-specs / references / exec-plans に分けて移した
- [x] `CLAUDE.md` を短い地図にし、`ARCHITECTURE.md` を新しく作った
- [x] ドキュメントと層構造の機械的な検査を足した（`tests/test_docs.py`、`tests/test_architecture.py`）

## 判断ログ

- 2026-11-09: `@tool` と `run_tool_calls` を削る案も検討した。ツールは LLM を組み込むアプリではほぼ必須で、使い心地を下げたくないので、残したうえで対応する型を固定することにした
- 2026-11-09: 画像入力は、実装が小さく需要が大きいので残した
- 2026-11-09: docs の構成は OpenAI の [Harness engineering](https://openai.com/ja-JP/index/harness-engineering/) で紹介された構造化 docs に倣った。元記事は自動取得できなかったので、構成は二次情報の要約で確かめた。製品向けの `DESIGN.md` や `PRODUCT_SENSE.md` など、このライブラリに該当する中身がないものは作っていない
- 2026-11-09: [スコープ](../../product-specs/scope.md) の「やらないこと」は、会話で出した提案をもとに書いた
