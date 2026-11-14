# 品質評価

領域ごとの状態。テストや検証の状況が変わったら更新する（最終更新: 2026-11-14）。

テストは GitHub Actions（`.github/workflows/test.yml`）で、Ubuntu の Python 3.10〜3.15 と Windows の 3.10・3.15 で回す。GitHub のリポジトリを作るまでは動いていない。

## 評価の基準

| 評価 | 意味 |
|---|---|
| A | モックのテストが揃っていて、実 API でも確認済み |
| B | モックのテスト（リクエスト形状 / ツール往復 / ストリーミング / エラー）は揃っているが、実 API では未確認 |
| C | テストが一部だけ |
| D | テストがない |

## アダプター

| アダプター | 評価 | モックのテスト | 実 API | 足りないテスト | 主な未検証の前提 |
|---|---|---|---|---|---|
| openai（Responses） | B | `tests/test_openai_responses.py` | 未確認 | `response.failed` のストリーミングエラー | U1〜U3 |
| openai_chat | B | `tests/test_openai_chat.py` | 未確認 | ストリーミング中のエラー | U10、U11、U15 |
| anthropic | B | `tests/test_anthropic.py` | 未確認 | — | U8、U9 |
| gemini | B | `tests/test_gemini.py` | 未確認 | ストリーミング中のエラー | U4〜U7 |

## 共通部分

| 領域 | 評価 | テスト | メモ |
|---|---|---|---|
| Client、モデル文字列、APIキー | B | `tests/test_client.py` | |
| リトライ、例外 | B | `tests/test_client.py` | 実際の 429 / 5xx / 切断での挙動は未確認 |
| プロバイダーの切り替え | B | `tests/test_cross_provider.py` | U5、U8 |
| `@tool`、`run_tool_calls` | B | `tests/test_tools.py` | 生成したスキーマを各社が受け付けるかは実 API で確認する |
| SSE パース | B | `tests/test_client.py` | |
| 層構造、ランタイム依存 | — | `tests/test_architecture.py` | 検査そのもの |
| ドキュメント | — | `tests/test_docs.py` | リンク・索引・公開 API・プリセットの一致を検査 |

実 API での確認は [実 API 検証](exec-plans/active/real-api-verification.md) で進める。
