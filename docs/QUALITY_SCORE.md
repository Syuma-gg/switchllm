# 品質評価

領域ごとの状態。テストや検証の状況が変わったら更新する（最終更新: 2026-11-28）。

- CI: GitHub Actions（`.github/workflows/test.yml`）で、Ubuntu の Python 3.10〜3.15 と Windows の 3.10・3.15 で回す（2026-11-14 に、全ジョブが通ることを確かめた）
- カバレッジ（分岐を含む）: 99.15%（2026-11-28、Python 3.12。テスト 1558 件）。基準は `pyproject.toml` の `fail_under = 99` で、上がったら引き上げる（[品質検査の方針](quality-gates.md)）
  - 到達していない行は3つ。`types.py` の `_part_to_dict` の、知らないパーツを変えずに返す行（2026-11-18 の履歴の保存で足した）と、`providers/base.py` の `error_hints` の、エラーの本文を JSON にできないときの2行（2026-11-28 のエラーの案内で足した）。どちらもテストがない
  - 残りは分岐の一部で、多くは、パーツの型で分ける `isinstance` の連鎖の、どれにも当たらない側（送る前の検査で `TypeError` にするので、今は通らない）
- 実 API に対する E2E テストは `e2e/` にあり、`--models` を渡したときだけ実 API を呼ぶ（料金が発生するため。[E2E テスト](e2e-tests.md)）。渡さなければ、実 API で記録したやりとりを再生し、既定の実行と CI にも入る（[テストの方針](testing.md)）。まだ実 API では回しておらず、記録は 0 件

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
| openai（Responses） | B | `tests/test_openai_responses.py` | 未確認 | — | U1〜U3、U22、U23、U28、U32 |
| openai_chat | B | `tests/test_openai_chat.py` | 未確認 | — | U10、U11、U15〜U18、U21、U23、U27、U29、U35 |
| anthropic | B | `tests/test_anthropic.py` | 未確認 | — | U8、U9、U19、U24、U25、U30、U33 |
| gemini | B | `tests/test_gemini.py` | 未確認 | — | U4〜U7、U16、U20、U26、U31、U34 |

## 共通部分

何を確かめているかの細かい一覧は、各テストのファイルにある。E2E のケースは [E2E テスト](e2e-tests.md) にある。ここには、状態と、まだ確かめていないことだけを書く。

| 領域 | 評価 | テスト | まだ確かめていないこと |
|---|---|---|---|
| Client、モデル文字列、APIキー、オプション、`options_by_provider=`、`headers=` | B | `tests/test_client.py`（`options_by_provider=` のテストは、実装にわざと入れた誤り 10 通りを、どれも見つけた。2026-11-28）、`tests/test_consistency.py` | 実 API（E2E の `test_e2e_headers.py`） |
| リトライ、例外 | B | `tests/test_http.py`、`tests/test_error_hints.py`（エラーの案内。実装にわざと入れた誤り 15 通りを、どれも見つけた。2026-11-28） | 実際の 429 / 5xx / 切断での挙動 |
| プロバイダーの切り替え | B | `tests/test_cross_provider.py`、`tests/test_consistency.py`、`tests/test_client.py` | 実 API（U5、U8、U16、U17） |
| 共通型 | B | `tests/test_types.py` | — |
| 履歴の保存・復元 | B | `tests/test_history.py` | 実 API（E2E の `test_restored_history_continues`） |
| 構造化出力（`response_schema=`） | B | `tests/test_structured_output.py` | 実 API（E2E の `test_e2e_structured.py`。U19〜U22） |
| 推論の強さ（`reasoning_effort=`） | B | `tests/test_reasoning_effort.py` | 実 API（E2E の `test_e2e_reasoning.py`。U23〜U27） |
| 推論の要約（`reasoning_summary=`） | B | `tests/test_reasoning_summary.py`（実装にわざと入れた誤り 16 通りを、どれも見つけた。2026-11-26） | 実 API（E2E の `test_e2e_reasoning_summary.py`。U32〜U35） |
| Anthropic の API の版、`max_tokens` の既定、`temperature` を捨てる警告 | B | `tests/test_anthropic.py`（実装にわざと入れた誤りを、既定の `max_tokens` は 4 通り、`temperature` の警告は 8 通り、どれも見つけた。2026-11-28） | 実 API（U25、U36、U37） |
| `@tool`、`run_tool_calls`、`arun_tool_calls` | B | `tests/test_tools.py` | 生成したスキーマを各社が受け付けるか |
| 画像の入力（`ImagePart`） | B | 各 `tests/test_<adapter>.py`、`tests/test_file_input.py` | 実 API（Gemini の URL は U39） |
| クラウドの経路のプリセット（`azure`・`azure-chat`・`foundry`・`bedrock`・`vertex`） | B | `tests/test_cloud_presets.py`（実装にわざと入れた誤り 17 通りを、どれも見つけた。2026-11-28）、`tests/test_support_table.py` | 実 API（U40〜U44） |
| プリセットごとに送れるもの（対応表） | B | `tests/test_support_table.py`（表の全セルを各プリセットで呼ぶ）。Gemini の画像の URL と Ollama の tool_choice・画像の URL は、実装にわざと入れた誤り 14 通りを、どれも見つけた（2026-11-28） | 実 API |
| ファイルの入力（`FilePart`） | B | `tests/test_file_input.py`、`tests/test_history.py` | 実 API（E2E の `test_e2e_files.py`。U28〜U31） |
| SSE の解析 | B | `tests/test_client.py`、`tests/test_async_client.py` | — |
| 非同期のクライアント（`AsyncClient`） | B | `tests/test_async_client.py`。`api` を使うテストはすべて非同期でも回る（非同期の経路にわざと入れた誤りを見つけることを確かめた。2026-11-23） | 実 API（E2E の `test_e2e_async.py`） |
| 実 API の記録と再生 | B | `tests/test_recording.py` | 実 API の記録はまだない |
| 層構造、ランタイム依存 | — | `tests/test_architecture.py` | 検査そのもの |
| ドキュメント | — | `tests/test_docs.py` | 検査そのもの |

実 API での確認は [実 API 検証](exec-plans/active/real-api-verification.md) で進める。
