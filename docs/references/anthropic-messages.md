# Anthropic Messages API

- アダプター: `providers/anthropic.py`（プリセット `anthropic`）
- エンドポイント: `POST https://api.anthropic.com/v1/messages`
- 認証: `x-api-key: $ANTHROPIC_API_KEY` と `anthropic-version: 2023-06-01`（`anthropic_version=` で変更できる）。beta 機能は `beta=[...]` が `anthropic-beta` ヘッダーになる

## 共通型との対応

| 共通 | Messages API |
|---|---|
| system | トップレベルの `system`（system メッセージが複数あれば空行で連結） |
| user | `content` ブロック（`text` / `image`）。空の text ブロックは拒否されるので送らない |
| assistant（再送しないとき） | `text` と `tool_use`（`input` はオブジェクト） |
| ツール結果 | user メッセージ内の `tool_result`（`tool_use_id`。エラーは `is_error: true`） |
| 連続する同じ role | 1メッセージにまとめる（並列ツールの結果は1つの user メッセージに入れる） |
| max_tokens | `max_tokens`（**必須**。指定がなければ 4096。`default_max_tokens=` で変更できる） |
| stop | `stop_sequences` |
| ツール定義 | `{"name", "description", "input_schema"}` |
| tool_choice | `{"type": "auto"}` / `{"type": "none"}` / `{"type": "any"}` / `{"type": "tool", "name"}` |
| 画像 | `{"type": "image", "source": {"type": "base64", ...}}` か `{"type": "url", "url"}` |

## レスポンス

- `content` ブロックの `text` / `thinking` / `tool_use` を読む。再送用に `content` 全体を `ProviderState` に入れる（thinking の `signature` を含む）
- stop_reason: `end_turn`・`stop_sequence` は `end`、`tool_use` は `tool_use`、`max_tokens`・`model_context_window_exceeded` は `max_tokens`、`refusal` は `content_filter`
- usage: `input_tokens` はキャッシュ分を含まないので、`cache_creation_input_tokens` と `cache_read_input_tokens` を足す（U14）

## ストリーミング

| イベント | 扱い |
|---|---|
| `message_start` | メッセージの骨組み |
| `content_block_start` / `content_block_delta` / `content_block_stop` | ブロックを組み立てる。`text_delta` は `text`、`thinking_delta` は `thinking`、`input_json_delta` は連結して最後にパース、`signature_delta` は連結 |
| `message_delta` | `stop_reason` と usage を更新 |
| `message_stop` | 終了して `done` |
| `error` | `StreamError` |
| `ping`・その他 | 無視（Anthropic は新しいイベントを追加することがあると明言している） |

## 注意点

- thinking とツールを併用するとき、thinking ブロックを signature ごと無加工で返す必要がある（[判断](../design-docs/provider-state-resend.md)）
- OpenAI SDK 互換のレイヤーは本番向けではないとされているので使わない（[判断](../design-docs/native-apis.md)）

## 出典

- [Streaming Messages](https://docs.anthropic.com/en/api/messages-streaming)
- [Extended thinking](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking)
- [OpenAI SDK compatibility](https://docs.anthropic.com/en/api/openai-sdk)

## 関連する未検証の前提

U8、U9、U14（[実 API 検証](../exec-plans/active/real-api-verification.md)）
