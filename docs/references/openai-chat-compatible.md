# Chat Completions 互換

- アダプター: `providers/openai_chat.py`（プリセット `openai-chat` / `ollama` / `lmstudio`、`register_provider()` で足したもの）
- エンドポイント: `POST {base_url}/chat/completions`
- 認証: `Authorization: Bearer <key>`。キーのないとき（ローカルのプリセット）はヘッダーを付けない。`headers=` で任意のヘッダーを足せる

## 共通型との対応

| 共通 | Chat Completions |
|---|---|
| system | `{"role": "system", "content": ...}` |
| user | `{"role": "user", "content": 文字列 か [text / image_url]}` |
| assistant | `{"role": "assistant", "content": 文字列 か null, "tool_calls": [...]}` |
| ツール結果 | `{"role": "tool", "tool_call_id", "content"}`。エラーは先頭に `Error: ` |
| max_tokens | 接続先が OpenAI 本家（`https://api.openai.com/v1`）なら `max_completion_tokens`、それ以外は `max_tokens`。`max_tokens_field=` で変更できる。OpenAI は `max_tokens` を非推奨とし、o-series とは互換性がないと記載している。Ollama の文書には `max_tokens` しかない |
| stop | `stop` |
| ツール定義 | `{"type": "function", "function": {"name", "description", "parameters"}}` |
| tool_choice | `"auto"` / `"none"` / `"required"` / `{"type": "function", "function": {"name"}}`。指定がなければ送らない（ツールがあれば既定は `auto`） |
| 画像 | `{"type": "image_url", "image_url": {"url": URL か data URL}}` |

## レスポンス

- `choices[0].message` の `content`（配列で返すサーバーもあるので連結する）と `tool_calls`（arguments は JSON 文字列）
- 推論テキストは `reasoning_content`（DeepSeek の形式）か `reasoning`（OpenRouter の形式）から読む。送り返しはしない（U11）
- 保持すべき推論情報がないので `ProviderState` は付けない
- usage は `prompt_tokens` / `completion_tokens`

## ストリーミング

- `finish_reason` の入ったチャンクのあと、`data: [DONE]` で終わる。どちらも来ないまま閉じたら `StreamError`（U16）
- 既定で `stream_options: {"include_usage": true}` を送る。拒否するサーバーには `stream_usage=False`（U10）
- `delta.tool_calls` は `index` ごとに引数の断片を連結する
- 最後に非ストリーミングと同じ形の dict を組み立てて `parse_response` を再利用する

## ローカル実行のプリセット

| プリセット | base_url | 確認日 | 出典 |
|---|---|---|---|
| `ollama` | `http://localhost:11434/v1` | 2026-11-09 | [Ollama: OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility) |
| `lmstudio` | `http://localhost:1234/v1` | 2026-11-09 | [LM Studio: OpenAI 互換エンドポイント](https://lmstudio.ai/docs/developer/openai-compat) |

- Ollama: ローカルではキー不要（クライアントがキーを要求する場合はダミー値でよく、Ollama は無視する）。Chat Completions・ストリーミング・ツールに対応。`tool_choice` は非対応と明記されているので、指定がなければ送らない（明示したときの挙動は U15）
- LM Studio: `/v1/chat/completions` に対応。既定のポートは 1234

## 出典

- [Chat Completions API reference](https://developers.openai.com/api/docs/api-reference/chat/create)（2026-11-15 確認: `max_tokens` は「`max_completion_tokens` に置き換えられて非推奨で、o-series とは互換性がない」。`tool_choice` は「ツールがあれば `auto` が既定」）
- [Ollama: OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)（2026-11-15 確認: `tool_choice` は非対応、`max_tokens` は対応、`max_completion_tokens` は記載なし）

## 関連する未検証の前提

U10、U11、U12、U15、U16（[実 API 検証](../exec-plans/active/real-api-verification.md)）
