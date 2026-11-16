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
| tool_choice | `{"type": "auto"}` / `{"type": "none"}` / `{"type": "any"}` / `{"type": "tool", "name"}`。指定がなければ送らない（ツールがあれば既定は `auto`） |
| 画像 | `{"type": "image", "source": {"type": "base64", ...}}` か `{"type": "url", "url"}` |

## レスポンス

- `content` ブロックの `text` / `thinking` / `tool_use` を読む。再送用に `content` 全体を `ProviderState` に入れる（thinking の `signature` を含む）
- stop_reason: `end_turn`・`stop_sequence` は `end`、`tool_use` は `tool_use`、`max_tokens`・`model_context_window_exceeded` は `max_tokens`、`refusal` は `content_filter`
- usage: `input_tokens` はキャッシュ分を含まないので、`cache_creation_input_tokens` と `cache_read_input_tokens` を足す（U14）

## ストリーミング

| イベント | 扱い |
|---|---|
| `message_start` | メッセージの骨組み |
| `content_block_start` / `content_block_delta` / `content_block_stop` | ブロックを組み立てる。`text_delta` は `text`、`thinking_delta` は `thinking`、`input_json_delta` は連結して最後にパース（JSON として読めなければ `arguments={}` にして原文を `raw_arguments` に残す。max_tokens で途中で切れたときなど）、`signature_delta` は連結 |
| `message_delta` | `stop_reason` と usage を更新 |
| `message_stop` | 終了して `done`。届かないまま閉じたら `StreamError` |
| `error` | `StreamError` |
| `ping`・その他 | 無視（Anthropic は新しいイベントを追加することがあると明言している） |

## 注意点

- thinking とツールを併用するとき、thinking ブロックを signature ごと無加工で返す必要がある（[判断](../design-docs/provider-state-resend.md)）
- OpenAI SDK 互換のレイヤーは本番向けではないとされているので使わない（[判断](../design-docs/native-apis.md)）
- ツールの強制（`tool_choice` の `any` と `tool`。共通の `"required"` とツール名）は、Claude Fable 5.2・Mythos 5.2・Opus 5.5・Sonnet 5.5・Fable 5.1・Mythos 5.1 と、thinking を予算で指定するモード（`thinking: {"type": "enabled"}`）では 400 になる。エラーの文言は `tool_choice: type "tool" and "any" are not supported for this model.`。`auto` と `none` はどれでも使える。それ以外のモデルは強制を受け付けるが、強制した応答は thinking を飛ばして `tool_use` から始まる。switchllm は先回りして弾かず、黙って `auto` に替えもせず、API のエラーをそのまま `BadRequestError` で返す（[RELIABILITY.md](../RELIABILITY.md)）。公式は代わりに、`auto` のままプロンプトでツールを使う場面を書くことを勧めている

## 出典

- [Streaming Messages](https://docs.anthropic.com/en/api/messages-streaming)
- [Extended thinking](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking)
- [OpenAI SDK compatibility](https://docs.anthropic.com/en/api/openai-sdk)
- [Implement tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use)（2026-11-15 確認: `tool_choice` の `auto` は「This is the default value when `tools` are provided」）
- [Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)（2026-11-15 確認: イベントの流れの最後は「A final `message_stop` event」）
- [Implement tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use) の「Forcing tool use」（2026-11-16 確認: 「Not every model and setting supports forced tool use」。対応しないモデルの一覧と、強制すると「the response starts with the `tool_use` block and has no `thinking` block」。`{"type": "none"}` は「prevents Claude from using any tools」）
- [Errors](https://platform.claude.com/docs/en/api/errors) の「Forced tool use not supported」（2026-11-16 確認: 上のエラーの文言と、`auto` と `none` は受け付けること）
- [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking) の「Thinking with tool use」（2026-11-17 確認: 強制は「incompatible with manual extended thinking」。6つのモデルで拒否する理由は、どの文書にも書かれていない）

## 関連する未検証の前提

U8、U9、U14（[実 API 検証](../exec-plans/active/real-api-verification.md)）
