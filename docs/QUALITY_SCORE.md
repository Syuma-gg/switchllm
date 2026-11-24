# 品質評価

領域ごとの状態。テストや検証の状況が変わったら更新する（最終更新: 2026-11-23）。

テストは GitHub Actions（`.github/workflows/test.yml`）で、Ubuntu の Python 3.10〜3.15 と Windows の 3.10・3.15 で回す（2026-11-14 に、全ジョブが通ることを確認した）。

カバレッジ（分岐を含む）は 99.15%（2026-11-24、Python 3.12。テスト 958 件）。基準は `pyproject.toml` の `fail_under = 99` で、上がったら引き上げる（[品質検査の方針](quality-gates.md)）。到達していない行は1つだけで、`types.py` の `_part_to_dict` の、知らないパーツを変えずに返す行（2026-11-18 の履歴の保存で足した。テストがない）。残りは分岐の一部で、多くはパーツの型で分ける `isinstance` の連鎖の、どれにも当たらない側（送る前の検査で `TypeError` にするので、今は通らない）。

実 API に対する E2E テストは `e2e/` にあり、`--models` を渡したときだけ実 API を呼ぶ（料金が発生するため。[E2E テスト](e2e-tests.md)）。渡さなければ、実 API で記録したやりとりを再生し、既定の実行と CI にも入る（[テストの方針](testing.md)）。まだ実 API では回しておらず、記録は 0 件。

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
| openai（Responses） | B | `tests/test_openai_responses.py` | 未確認 | — | U1〜U3、U22、U23 |
| openai_chat | B | `tests/test_openai_chat.py` | 未確認 | — | U10、U11、U15〜U18、U21、U23、U27 |
| anthropic | B | `tests/test_anthropic.py` | 未確認 | — | U8、U9、U19、U24、U25 |
| gemini | B | `tests/test_gemini.py` | 未確認 | — | U4〜U7、U16、U20、U26 |

## 共通部分

| 領域 | 評価 | テスト | メモ |
|---|---|---|---|
| Client、モデル文字列、APIキー、オプション | B | `tests/test_client.py`、`tests/test_consistency.py` | 知らないオプション名・`tool_choice` の誤り・読めないパーツ・`headers=` の誤りを、送る前に例外にする。`headers=` は4アダプターで足され、同じ名前（大文字と小文字を区別しない）のヘッダーを置き換えて2本送らないこと、認証のヘッダーを渡せばキーが要らないこと、値の `None` でライブラリのヘッダー（認証のヘッダーを含む）を止められること。実 API では未確認（E2E の `test_e2e_headers.py`） |
| リトライ、例外 | B | `tests/test_http.py` | 接続エラー、`Retry-After`、エラー本体の解釈、ストリームの途中の切断。実際の 429 / 5xx / 切断での挙動は未確認 |
| プロバイダーの切り替え | B | `tests/test_cross_provider.py`、`tests/test_consistency.py`、`tests/test_client.py` | 履歴の変換、4アダプターで同じ振る舞いになること（同じクライアントで読む複数のストリームが混ざらないことを含む）、`with_model()`、`extra_by_provider`。U5、U8、U16、U17 |
| 共通型 | B | `tests/test_types.py` | `Message.from_dict`、`ToolCall.arguments_json`、`ImagePart` |
| 履歴の保存・復元 | B | `tests/test_history.py` | `to_dict` / `from_dict` の往復、4アダプターで復元した履歴から同じリクエストを送れること。実 API では未確認（E2E の `test_restored_history_continues`） |
| 構造化出力（`response_schema=`） | B | `tests/test_structured_output.py` | 4アダプター（Ollama・LM Studio を含む）で各社の形式になること、`chat()` と `stream()` で同じ `parsed`、拒否・上限で `None`、`additionalProperties` の足し方、DeepSeek は送る前に例外。実 API では未確認（E2E の `test_e2e_structured.py`。U19〜U22） |
| 推論の強さ（`reasoning_effort=`） | B | `tests/test_reasoning_effort.py` | 4アダプター（DeepSeek・Ollama を含む）と `stream()` で各社の指定になること、7段階の名前を変えずに送ること、名前のない段階と打ち間違いを送る前に例外にすること、Anthropic の `none` は thinking を切ること、`response_schema=`・`extra=` との並び方。実 API では未確認（E2E の `test_e2e_reasoning.py`。U23〜U27） |
| Anthropic の API の版 | B | `tests/test_anthropic.py` | 既定は `2026-10-21`、この版以降で `temperature=` を送る前に例外にすること、古い版なら送ること。実 API では未確認（U25） |
| `@tool`、`run_tool_calls`、`arun_tool_calls` | B | `tests/test_tools.py` | `async def` のツールの await、並行の実行と結果の順序、同期の `run_tool_calls` が `async def` を実行せずにエラーの結果を返すこと。生成したスキーマを各社が受け付けるかは実 API で確認する |
| 画像入力（`ImagePart`） | B | 各 `tests/test_<adapter>.py`、`tests/test_file_input.py` | 4アダプターのリクエスト形状、Gemini の URL 拒否、`from_file` の形式判定、user 以外のメッセージの画像と中身のない画像を送る前に止めること。実 API では未確認 |
| ファイルの入力（`FilePart`） | B | `tests/test_file_input.py`、`tests/test_history.py` | 4アダプターのリクエスト形状、受け付けないと分かっている種類とプリセットを送る前に止めること、Anthropic がテキストを文字に戻すこと、画像・音声・動画と中身のないファイルを止めること、`from_file` の種類の判定（OS の `mimetypes` に頼らない）、保存の形。実 API では未確認（E2E の `test_e2e_files.py`、U28〜U31） |
| SSE パース | B | `tests/test_client.py`、`tests/test_async_client.py` | 同期と非同期で同じに分解すること |
| 非同期のクライアント（`AsyncClient`） | B | `tests/test_async_client.py`。`api` を使うテストはすべて非同期でも回る | 引数が `Client` と同じこと、並行の送信、リトライの待ちでイベントループを止めないこと、取り消したら再送せずに応答を閉じること、途中で抜けたら閉じること（`aclosing()` を使っても使わなくても）、`http_client=` の取り違え。非同期の経路にわざと誤りを入れて、テストが見つけることを確かめた（2026-11-23）。実 API では未確認（E2E の `test_e2e_async.py`） |
| 実 API の記録と再生 | B | `tests/test_recording.py` | 秘密を残さない（ヘッダー、URL の `key`、キーの値）、記録と違うリクエストで失敗する、gzip の応答、仮 ID の扱い。実 API の記録はまだない |
| 層構造、ランタイム依存 | — | `tests/test_architecture.py` | 検査そのもの |
| ドキュメント | — | `tests/test_docs.py` | リンク・索引・公開 API・プリセット・オプションの一致を検査 |

実 API での確認は [実 API 検証](exec-plans/active/real-api-verification.md) で進める。
