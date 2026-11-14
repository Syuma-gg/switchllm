# OpenAI Responses API

- アダプター: `providers/openai_responses.py`（プリセット `openai`）
- エンドポイント: `POST https://api.openai.com/v1/responses`
- 認証: `Authorization: Bearer $OPENAI_API_KEY`
- このアダプターを使う理由: [判断](../design-docs/openai-responses-api.md)

## 共通型との対応

| 共通 | Responses API |
|---|---|
| system | トップレベルの `instructions` |
| user | `{"role": "user", "content": 文字列 か [input_text / input_image]}` |
| assistant（再送しないとき） | `{"role": "assistant", "content": 文字列}` と `function_call` アイテム |
| ツール結果 | `{"type": "function_call_output", "call_id", "output"}`。エラーは `output` の先頭に `Error: ` |
| max_tokens | `max_output_tokens` |
| stop | **存在しない**ので `UnsupportedFeatureError` |
| ツール定義 | `{"type": "function", "name", "description", "parameters", "strict": false}` |
| tool_choice | `"auto"` / `"none"` / `"required"` / `{"type": "function", "name"}`。指定がなければ送らない（既定は `auto`） |
| 画像 | `{"type": "input_image", "image_url": URL か data URL}` |

## レスポンス

- `output` 配列の `message`（`output_text` / `refusal`）、`function_call`（`call_id`、arguments は JSON 文字列）、`reasoning`（`summary`）を読む
- 再送用に `output` 全体を `ProviderState` に入れる
- stop_reason: `function_call` があれば `tool_use`、`incomplete_details.reason == "max_output_tokens"` は `max_tokens`、`content_filter` か refusal は `content_filter`、`status == "completed"` は `end`

## ストリーミング

| イベント | 扱い |
|---|---|
| `response.output_text.delta` | `text` |
| `response.reasoning_summary_text.delta` | `thinking` |
| `response.completed` / `response.incomplete` | 中の `response` を `parse_response` に渡して `done` |
| `response.failed` / `error` | `StreamError` |
| その他 | 無視 |

`response.completed` が来ないまま終わったら `StreamError`。

## 注意点

- 既定ではレスポンスが保存される（`store`）。`extra={"store": False}` にする場合、reasoning アイテムの再送には `include: ["reasoning.encrypted_content"]` も必要（U1）
- `strict` は既定値に依存しないよう `false` を明示している。`true` にするには、全プロパティを required にして `additionalProperties: false` を付ける必要がある（U3）

## 出典

- [Migrate to the Responses API](https://developers.openai.com/api/docs/guides/migrate-to-responses)
- [Function calling](https://developers.openai.com/api/docs/guides/function-calling)（2026-11-15 確認: `tool_choice` は「Auto (Default)」。API リファレンスのページは自動取得を拒否したため、ガイドで確認した）

## 関連する未検証の前提

U1〜U3（[実 API 検証](../exec-plans/active/real-api-verification.md)）
