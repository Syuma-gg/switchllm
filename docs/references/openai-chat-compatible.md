# Chat Completions 互換

- アダプター: `providers/openai_chat.py`（プリセット `openai-chat` / `deepseek` / `ollama` / `lmstudio`、`register_provider()` で足したもの）
- エンドポイント: `POST {base_url}/chat/completions`
- 認証: `Authorization: Bearer <key>`。キーのないとき（ローカルのプリセット）はヘッダーを付けない。`headers=` で任意のヘッダーを足せる

## 共通型との対応

| 共通 | Chat Completions |
|---|---|
| system | `{"role": "system", "content": ...}` |
| user | `{"role": "user", "content": 文字列 か [text / image_url]}` |
| assistant | `{"role": "assistant", "content": 文字列 か null, "tool_calls": [...]}`。同じプロバイダー・同じモデルが返した `reasoning_content` があれば、それも足す（下の「推論テキストの送り返し」） |
| ツール結果 | `{"role": "tool", "tool_call_id", "content"}`。エラーは先頭に `Error: ` |
| max_tokens | 接続先が OpenAI 本家（`https://api.openai.com/v1`）なら `max_completion_tokens`、それ以外は `max_tokens`。`max_tokens_field=` で変更できる。OpenAI は `max_tokens` を非推奨とし、o-series とは互換性がないと記載している。Ollama の文書には `max_tokens` しかない |
| stop | `stop` |
| ツール定義 | `{"type": "function", "function": {"name", "description", "parameters"}}` |
| tool_choice | `"auto"` / `"none"` / `"required"` / `{"type": "function", "function": {"name"}}`。指定がなければ送らない（ツールがあれば既定は `auto`） |
| 画像 | `{"type": "image_url", "image_url": {"url": URL か data URL}}` |

## レスポンス

- `choices[0].message` の `content`（配列で返すサーバーもあるので連結する）と `tool_calls`（arguments は JSON 文字列）
- 推論テキストは `reasoning_content`（DeepSeek の形式）か `reasoning`（OpenRouter の形式）から読み、`Response.thinking` に入れる
- `reasoning_content` を返したときだけ、`ProviderState` に `choices[0].message` を丸ごと入れる。`reasoning` だけのときは付けない
- usage は `prompt_tokens` / `completion_tokens`

## 推論テキストの送り返し

DeepSeek の思考モードは、ツールを使う会話で、過去の `reasoning_content` を送り返すことを求める（U11）。

- `tools` を付けたリクエストでは、それより前のすべての assistant メッセージの `reasoning_content` を送り返す必要がある。ツールを呼ばなかったターンの分も含む。送り返さないと 400 になる
- `tools` のないリクエストでは送り返さなくてよく、送っても無視される
- 送る形は、assistant メッセージの `content`・`tool_calls` と並べた文字列のフィールド `reasoning_content`

このアダプターの扱い:

- 同じプロバイダーキー・同じモデルが返したメッセージ（`native_raw()` が生データを返すもの）にだけ、`reasoning_content` を足す。`tools` の有無にかかわらず足す（ないときは DeepSeek が無視する）
- 生データ全体は送らない。返ってきた message には、リクエストでは受け付けられないフィールドがありうるため
- `reasoning`（OpenRouter の形式）は送り返さない。送り返せとする文書を確認していない
- `reasoning_content` を返すほかの互換サーバー（LM Studio など）にも同じく送り返す。受け付けるかは実 API で確かめる（U11）
- 別のプロバイダーや別のモデルの履歴、手で作ったメッセージには `reasoning_content` がない。DeepSeek がそれを 400 にするかは文書にない（U17）

## ストリーミング

- `finish_reason` の入ったチャンクのあと、`data: [DONE]` で終わる。どちらも来ないまま閉じたら `StreamError`（U16）
- 既定で `stream_options: {"include_usage": true}` を送る。拒否するサーバーには `stream_usage=False`（U10）
- `delta.tool_calls` は `index` ごとに引数の断片を連結する
- 最後に非ストリーミングと同じ形の dict を組み立てて `parse_response` を再利用する

## プリセット

| プリセット | base_url | APIキーの環境変数 | 確認日 | 出典 |
|---|---|---|---|---|
| `deepseek` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` | 2026-11-17 | [DeepSeek: Your First API Call](https://api-docs.deepseek.com/) |
| `ollama` | `http://localhost:11434/v1` | 不要 | 2026-11-09 | [Ollama: OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility) |
| `lmstudio` | `http://localhost:1234/v1` | 不要 | 2026-11-09 | [LM Studio: OpenAI 互換エンドポイント](https://lmstudio.ai/docs/developer/openai-compat) |

- DeepSeek: エンドポイントは `https://api.deepseek.com/chat/completions`、認証は `Authorization: Bearer`。`/v1` を付けた URL を受け付けるかは文書にない。モデルは `deepseek-flash` と `deepseek-v4-pro`（2026-11-17 時点）で、どちらも思考モードが既定。思考モードを切るには `extra={"thinking": {"type": "disabled"}}`（または `reasoning_effort: "none"`）を送る
- DeepSeek の思考モードでは、`tool_choice` の `"required"` とツール名の指定は 400 になる。このライブラリは先回りして弾かず、API のエラーをそのまま返す（[実 API 検証](../exec-plans/active/real-api-verification.md) の判断ログ）。`temperature` はエラーにならず、効かない
- DeepSeek の `finish_reason` には、OpenAI にない `insufficient_system_resource` と `aborted` がある。どちらも `stop_reason="other"` にする
- DeepSeek の usage には `completion_tokens_details.reasoning_tokens` がある。`completion_tokens` が推論の分を含むかは文書にない（U18）
- DeepSeek の `stream_options: {"include_usage": true}` は対応している。usage は `[DONE]` の直前のチャンクに入る

- Ollama: ローカルではキー不要（クライアントがキーを要求する場合はダミー値でよく、Ollama は無視する）。Chat Completions・ストリーミング・ツールに対応。`tool_choice` は非対応と明記されているので、指定がなければ送らない（明示したときの挙動は U15）
- LM Studio: `/v1/chat/completions` に対応。既定のポートは 1234

## 出典

- [Chat Completions API reference](https://developers.openai.com/api/docs/api-reference/chat/create)（2026-11-15 確認: `max_tokens` は「`max_completion_tokens` に置き換えられて非推奨で、o-series とは互換性がない」。`tool_choice` は「ツールがあれば `auto` が既定」）
- [Ollama: OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)（2026-11-15 確認: `tool_choice` は非対応、`max_tokens` は対応、`max_completion_tokens` は記載なし）
- [DeepSeek: Your First API Call](https://api-docs.deepseek.com/)（2026-11-17 確認: OpenAI 形式の base_url は `https://api.deepseek.com`、キーは `DEEPSEEK_API_KEY` から `Authorization: Bearer` で送る、モデル名は `deepseek-flash` / `deepseek-v4-pro`）
- [DeepSeek: Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)（2026-11-17 確認: 「for requests carrying the `tools` parameter, the `reasoning_content` must be fully passed back to the API in all subsequent requests」「If your code does not correctly pass back `reasoning_content`, the API will return a 400 error」、ツールがなければ「`reasoning_content` does not need to be passed back」で、送っても無視される。思考モードは既定で有効。`temperature` などは「will not trigger an error but will also have no effect」。別の履歴やモデルの切り替えについては記載なし）
- [DeepSeek: Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion)（2026-11-17 確認: 出力の上限は `max_tokens`（`max_completion_tokens` は記載なし）、`stop` は文字列か配列、`tool_choice` は `none` / `auto` / `required` / `{"type": "function", "function": {"name"}}` で「`required` and named tool choices are not supported in thinking mode; the API returns a `400` error」、`finish_reason` は `stop` / `length` / `content_filter` / `tool_calls` / `insufficient_system_resource` / `aborted`、`stream_options.include_usage` に対応。リクエストの `reasoning_content` は「Chat Prefix Completion（Beta）用」とだけ書かれていて、Thinking Mode の文書の例（`prefix` なしで送る）と食い違う）
- [DeepSeek: Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing)（2026-11-17 確認: `deepseek-flash`（DeepSeek-V4.1-Flash-1017）と `deepseek-v4-pro`（DeepSeek-V4-Pro-0813）。どちらも思考モードが既定でツールに対応）

## 関連する未検証の前提

U10、U11、U12、U15、U16、U17、U18（[実 API 検証](../exec-plans/active/real-api-verification.md)）
