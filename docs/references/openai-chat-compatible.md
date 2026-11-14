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
| max_tokens | `max_tokens`（`max_tokens_field="max_completion_tokens"` で変更できる） |
| stop | `stop` |
| ツール定義 | `{"type": "function", "function": {"name", "description", "parameters"}}` |
| tool_choice | `"auto"` / `"none"` / `"required"` / `{"type": "function", "function": {"name"}}` |
| 画像 | `{"type": "image_url", "image_url": {"url": URL か data URL}}` |

## レスポンス

- `choices[0].message` の `content`（配列で返すサーバーもあるので連結する）と `tool_calls`（arguments は JSON 文字列）
- 推論テキストは `reasoning_content`（DeepSeek の形式）か `reasoning`（OpenRouter の形式）から読む。送り返しはしない（U11）
- 保持すべき推論情報がないので `ProviderState` は付けない
- usage は `prompt_tokens` / `completion_tokens`

## ストリーミング

- `data: [DONE]` で終わる
- 既定で `stream_options: {"include_usage": true}` を送る。拒否するサーバーには `stream_usage=False`（U10）
- `delta.tool_calls` は `index` ごとに引数の断片を連結する
- 最後に非ストリーミングと同じ形の dict を組み立てて `parse_response` を再利用する

## ローカル実行のプリセット

| プリセット | base_url | 確認日 | 出典 |
|---|---|---|---|
| `ollama` | `http://localhost:11434/v1` | 2026-11-09 | [Ollama: OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility) |
| `lmstudio` | `http://localhost:1234/v1` | 2026-11-09 | [LM Studio: OpenAI 互換エンドポイント](https://lmstudio.ai/docs/developer/openai-compat) |

- Ollama: ローカルではキー不要（クライアントがキーを要求する場合はダミー値でよく、Ollama は無視する）。Chat Completions・ストリーミング・ツールに対応。`tool_choice` は非対応と明記されている（U15）
- LM Studio: `/v1/chat/completions` に対応。既定のポートは 1234

## 関連する未検証の前提

U10、U11、U12、U15（[実 API 検証](../exec-plans/active/real-api-verification.md)）
