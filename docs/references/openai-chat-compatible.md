# Chat Completions 互換

- アダプター: `providers/openai_chat.py`（プリセット `openai-chat` / `deepseek` / `ollama` / `lmstudio`、`register_provider()` で足したもの）
- エンドポイント: `POST {base_url}/chat/completions`
- 認証: `Authorization: Bearer <key>`。キーのないとき（ローカルのプリセット）はヘッダーを付けない。`headers=` で任意のヘッダーを足せる。同じ名前のヘッダーは、大文字と小文字を区別せずに置き換える（以前は `authorization` のように大文字と小文字が違うと2本送っていた。[判断](../design-docs/custom-headers.md)）

## 共通型との対応

| 共通 | Chat Completions |
|---|---|
| system | `{"role": "system", "content": ...}` |
| user | `{"role": "user", "content": 文字列 か [text / image_url / file]}` |
| assistant | `{"role": "assistant", "content": 文字列 か null, "tool_calls": [...]}`。同じプロバイダー・同じモデルが返した `reasoning_content` があれば、それも足す（下の「推論テキストの送り返し」） |
| ツール結果 | `{"role": "tool", "tool_call_id", "content"}`。エラーは先頭に `Error: ` |
| max_tokens | 接続先が OpenAI 本家（`https://api.openai.com/v1`）なら `max_completion_tokens`、それ以外は `max_tokens`。`max_tokens_field=` で変更できる。OpenAI は `max_tokens` を非推奨とし、o-series とは互換性がないと記載している。Ollama の文書には `max_tokens` しかない |
| stop | `stop` |
| ツール定義 | `{"type": "function", "function": {"name", "description", "parameters"}}` |
| tool_choice | `"auto"` / `"none"` / `"required"` / `{"type": "function", "function": {"name"}}`。指定がなければ送らない（ツールがあれば既定は `auto`） |
| 画像 | `{"type": "image_url", "image_url": {"url": URL か data URL}}` |
| ファイル | `{"type": "file", "file": {"file_data": data URL, "filename"}}`。PDF だけで、ほかの種類は送る前に `UnsupportedFeatureError`。DeepSeek・Ollama・LM Studio のプリセットは `file_input: False` で、送る前に `UnsupportedFeatureError`（下の「ファイルの入力」） |

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

- DeepSeek: エンドポイントは `https://api.deepseek.com/chat/completions`、認証は `Authorization: Bearer`。`/v1` を付けた URL を受け付けるかは文書にない。モデルは `deepseek-flash` と `deepseek-v4-pro`（2026-11-17 時点）で、どちらも思考モードが既定。思考モードを切るには `reasoning_effort="none"` を渡す（下の「推論の強さ」）
- DeepSeek の思考モードでは、`tool_choice` の `"required"` とツール名の指定は 400 になる。このライブラリは先回りして弾かず、API のエラーをそのまま返す（[実 API 検証](../exec-plans/active/real-api-verification.md) の判断ログ）。`temperature` はエラーにならず、効かない
- DeepSeek の `finish_reason` には、OpenAI にない `insufficient_system_resource` と `aborted` がある。どちらも `stop_reason="other"` にする
- DeepSeek の usage には `completion_tokens_details.reasoning_tokens` がある。`completion_tokens` が推論の分を含むかは文書にない（U18）
- DeepSeek の `stream_options: {"include_usage": true}` は対応している。usage は `[DONE]` の直前のチャンクに入る

- Ollama: ローカルではキー不要（クライアントがキーを要求する場合はダミー値でよく、Ollama は無視する）。Chat Completions・ストリーミング・ツールに対応。`tool_choice` は非対応と明記されているので、指定がなければ送らない（明示したときの挙動は U15）
- LM Studio: `/v1/chat/completions` に対応。既定のポートは 1234

## 構造化出力

`response_schema=` は、OpenAI 本家と同じ `response_format` の `json_schema`（`strict: true`）で送る。オプション `json_schema` を `False` にした接続先（`deepseek` のプリセット）では、送る前に `UnsupportedFeatureError` にする（[判断](../design-docs/structured-output.md)）。以下は 2026-11-20 に文書で確かめた形式で、接続先によって受け付ける形が違う。

| 接続先 | JSON Schema | JSON モード | 文書の書き方 |
|---|---|---|---|
| OpenAI 本家 | `response_format: {"type": "json_schema", "json_schema": {"name", "description", "schema", "strict"}}` | `{"type": "json_object"}` | リファレンスに項目と制約がある |
| DeepSeek | **ない**（`type` は `text` か `json_object` だけ） | `{"type": "json_object"}` | リファレンスに「Must be one of `text` or `json_object`」 |
| Ollama | `response_format` に対応とあるが、形の例はない | 「JSON mode」に対応とあるが、形の例はない | 対応表にチェックがあるだけ（U21） |
| LM Studio | OpenAI と同じ形の例がある | 書かれていない | 構造化出力のページに例がある（U21） |

- OpenAI 本家: `name` は英数字・`_`・`-` で 64 文字まで（必須）。`strict` は既定で `false`。`true` にしたときのスキーマの条件は Responses と同じ（[OpenAI Responses](openai-responses.md) の「構造化出力」）。公式は「Using `json_schema` is preferred for models that support it」とする
- DeepSeek の JSON モード: プロンプトに「json」の語と、出したい JSON の例を入れる必要がある。`content` が空白だけで返ることがあり（`finish_reason` が `stop` でも）、公式は再試行を勧めている。`finish_reason` が `length` なら途中で切れている。思考モードやツールと一緒に使えるかは書かれていない。スキーマで縛れるのは、beta の接続先（`https://api.deepseek.com/beta`）での strict なツール呼び出しだけで、答えの形を縛る指定はない
- Ollama: OpenAI 互換の API でも `response_format` で構造化出力が使えるとあるが、例は独自 API の `format` だけ。「Ollama's cloud currently does not support structured outputs」。使えるキーワードは書かれていない
- LM Studio: 例では `strict` を文字列の `"true"` で送っている。GGUF のモデルは llama.cpp の文法、MLX のモデルは Outlines で縛る。「Not all models are capable of structured output, particularly LLMs below 7B parameters」。一方、Chat Completions のページの、受け付けるパラメータの一覧には `response_format` がない
- ストリーミングやツールとの併用は、どの互換サーバーの文書にも書かれていない

## 推論の強さ

`reasoning_effort=` は、どの接続先にもトップレベルの `reasoning_effort` で、段階の名前を変えずに送る（[判断](../design-docs/reasoning-effort.md)）。以下は 2026-11-21 に文書で確かめた形式。

`reasoning_summary=True` では何も足さない。Chat Completions には要約を頼む標準の指定がなく、DeepSeek は頼まなくても `reasoning_content` を返す。Ollama・LM Studio が互換 API で推論テキストを返すかは文書にない（U35）。接続先が OpenAI 本家なら、推論の本文を返さないので送る前に `UnsupportedFeatureError`（[判断](../design-docs/reasoning-summary.md)）。

| 接続先 | 受け付ける段階 | 推論を切る | 文書にない段階 |
|---|---|---|---|
| OpenAI 本家 | `none`・`minimal`・`low`・`medium`・`high`・`xhigh`・`max`（モデルによって一部だけ。[OpenAI Responses](openai-responses.md) の「推論の強さ」） | `none`（受け付けないモデルは 400） | 書かれていない（U23） |
| DeepSeek | `none`・`low`・`high`・`max`。`minimal` は `low`、`medium` と `xhigh` は `high` として受け付ける | `none` | 書かれていない |
| Ollama | モデルが決める名前と、互換のための別名（`/api/show` で分かる） | `none`（真偽値だけのモデルでは `false` になる。gpt-oss は切れず `low` になる） | モデルの既定の段階になる（黙って置き換える） |
| LM Studio | **書かれていない**（受け付けるパラメータの一覧にない） | 書かれていない | 書かれていない（U27） |

- DeepSeek: 思考モードは既定で有効で、既定の段階は `high`。`thinking: {"type": "enabled" | "disabled"}` もあり、送ったときは `reasoning_effort` より優先する（「takes precedence over `reasoning_effort` for selecting the mode」）。`thinking` を送らなければ `reasoning_effort` で決まり、`none` なら思考モードを切る。Thinking Mode のガイドの表は `low`・`high`・`max` だけを挙げ、`ultra` を `max` の別名として挙げるが、API リファレンスは `none` を含め、`ultra` を挙げない。`max_tokens` の既定は思考モードで 64K（`max` なら 128K）、思考モードでないとき 8K。`max_tokens` が推論の分を含むかは書かれていない（U18）。思考モードでは `temperature` は効かない（エラーにならない）。`top_p` は、`deepseek-flash` では 0.95 未満を 0.95 として扱い、`deepseek-v4-pro` では渡した値を無視する
- Ollama: `/v1/chat/completions` は `reasoning_effort` と `reasoning: {"effort"}` を受け付ける。メタデータのないモデルでは `minimal` を `low`、`xhigh` と `ultra` を `max` として扱う。`think`（Ollama 独自）は `/v1/responses` の一覧にだけある。推論テキストを返すフィールドの名前は、互換の API については書かれていない（独自 API は `message.thinking`）
- LM Studio: Chat Completions のページが挙げるパラメータは `model`・`top_p`・`top_k`・`messages`・`temperature`・`max_tokens`・`stream`・`stop`・`presence_penalty`・`frequency_penalty`・`logit_bias`・`repeat_penalty`・`seed` だけ。`reasoning_effort` を送ると、受け付けるのか、無視するのか、拒否するのかは分からない（U27）
- switchllm は、どの接続先でも名前を置き換えない。DeepSeek と Ollama は、文書にない段階を接続先の側で近い段階に置き換える

## ファイルの入力

`FilePart` は OpenAI の形（`{"type": "file", "file": {...}}`）で送る（[判断](../design-docs/file-input.md)）。以下は 2026-11-23 に文書で確かめた。

- OpenAI: `file` の中に `file_data`・`file_id`・`filename`（どれも optional）。`file_data` は「The base64 encoded file data」とだけ書かれ、Chat Completions の例はない。switchllm は Responses の例に合わせて data URL で送る（U29）。種類は「Chat Completions accepts only PDF files as `file` content parts」なので、PDF 以外は送る前に `UnsupportedFeatureError` にする。`file_url` と `detail` はない
- DeepSeek: `file` のパーツはあるが形が違い、`file_id`・`file_data`・`filename` をパーツに直接置く（`file` で包まない）。文書を読ませるのは「can be referenced only by `file_id`. Passing the file content or a file URL in the request is not supported」で、Files API でのアップロードが要る。switchllm はアップロードを扱わないので、プリセットを `file_input: False` にした
- Ollama: OpenAI 互換の文書の対応表は、本文が文字列・画像（base64。URL は非対応）・パーツの配列だけで、ファイルの記述がない。プリセットを `file_input: False` にした
- LM Studio: 文書は Chat Completions を「text and images」とし、ファイルの記述がない。プリセットを `file_input: False` にした
- ほかの互換サーバー（`register_provider()` や `base_url=`）には OpenAI の形で送る。受け付けないサーバーなら API のエラーになる。受け付けないと分かっているなら `file_input=False` を渡すと、送る前に `UnsupportedFeatureError` にする

## 出典

- [Chat Completions API reference](https://developers.openai.com/api/docs/api-reference/chat/create)（2026-11-20 確認: `response_format` の `json_schema` は `name`（「Must be a-z, A-Z, 0-9, or contain underscores and dashes, with a maximum length of 64」）・`description`・`schema`・`strict`（「default: false」）。「Using `json_schema` is preferred for models that support it」）
- [DeepSeek: JSON Output](https://api-docs.deepseek.com/guides/json_mode)（2026-11-20 確認: 「Include the word "json" in the system or user prompt, and provide an example of the desired JSON format」、`content` が空白だけで返ることがある、`max_tokens` を十分に取る）
- [DeepSeek: Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion)（2026-11-20 確認: `response_format.type` は「Must be one of `text` or `json_object`」）
- [DeepSeek: Tool Calls](https://api-docs.deepseek.com/guides/tool_calls)（2026-11-20 確認: strict モード（beta）は `base_url="https://api.deepseek.com/beta"` で、ツールの引数にだけ効く）
- [Ollama: OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)（2026-11-20 確認: `/v1/chat/completions` の対応表に「JSON mode」と `response_format` のチェックがある。形の例はない）
- [Ollama: Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs)（2026-11-20 確認: 「Structured outputs work through the OpenAI-compatible API via `response_format`」、「Ollama's cloud currently does not support structured outputs」）
- [LM Studio: Structured Output](https://lmstudio.ai/docs/developer/openai-compat/structured-output)（2026-11-20 確認: `response_format: {"type": "json_schema", "json_schema": {"name", "strict": "true", "schema"}}` の例、7B 未満のモデルの注意、GGUF と MLX での縛り方）
- [LM Studio: Chat Completions](https://lmstudio.ai/docs/developer/openai-compat/chat-completions)（2026-11-20 確認: 受け付けるパラメータの一覧に `response_format` がない）
- [Chat Completions API reference](https://developers.openai.com/api/docs/api-reference/chat/create)（2026-11-15 確認: `max_tokens` は「`max_completion_tokens` に置き換えられて非推奨で、o-series とは互換性がない」。`tool_choice` は「ツールがあれば `auto` が既定」）
- [Ollama: OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)（2026-11-15 確認: `tool_choice` は非対応、`max_tokens` は対応、`max_completion_tokens` は記載なし）
- [DeepSeek: Your First API Call](https://api-docs.deepseek.com/)（2026-11-17 確認: OpenAI 形式の base_url は `https://api.deepseek.com`、キーは `DEEPSEEK_API_KEY` から `Authorization: Bearer` で送る、モデル名は `deepseek-flash` / `deepseek-v4-pro`）
- [DeepSeek: Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)（2026-11-17 確認: 「for requests carrying the `tools` parameter, the `reasoning_content` must be fully passed back to the API in all subsequent requests」「If your code does not correctly pass back `reasoning_content`, the API will return a 400 error」、ツールがなければ「`reasoning_content` does not need to be passed back」で、送っても無視される。思考モードは既定で有効。`temperature` などは「will not trigger an error but will also have no effect」。別の履歴やモデルの切り替えについては記載なし）
- [DeepSeek: Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion)（2026-11-17 確認: 出力の上限は `max_tokens`（`max_completion_tokens` は記載なし）、`stop` は文字列か配列、`tool_choice` は `none` / `auto` / `required` / `{"type": "function", "function": {"name"}}` で「`required` and named tool choices are not supported in thinking mode; the API returns a `400` error」、`finish_reason` は `stop` / `length` / `content_filter` / `tool_calls` / `insufficient_system_resource` / `aborted`、`stream_options.include_usage` に対応。リクエストの `reasoning_content` は「Chat Prefix Completion（Beta）用」とだけ書かれていて、Thinking Mode の文書の例（`prefix` なしで送る）と食い違う）
- [DeepSeek: Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing)（2026-11-17 確認: `deepseek-flash`（DeepSeek-V4.1-Flash-1017）と `deepseek-v4-pro`（DeepSeek-V4-Pro-0813）。どちらも思考モードが既定でツールに対応）
- [Chat Completions API reference](https://developers.openai.com/api/docs/api-reference/chat/create)（2026-11-21 確認: `reasoning_effort` は既定 `"medium"` で「Currently supported values are `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, and `max`」「Not all reasoning models support every value」）
- [DeepSeek: Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion)（2026-11-21 確認: `reasoning_effort` は「`none` disables thinking mode; `low` / `high` / `max` enable thinking mode」「The default effort is `high`」「`minimal` is accepted and mapped to `low`, and `medium` / `xhigh` are accepted and mapped to `high`」、`thinking` は「When this object is provided, `type` is required and takes precedence over `reasoning_effort` for selecting the mode」「When `thinking` is omitted or `null`, the mode follows `reasoning_effort`; if `reasoning_effort` is also omitted, thinking is enabled」、`temperature` は「Has no effect in thinking mode」、`max_tokens` の既定）
- [DeepSeek: Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)（2026-11-21 確認: 「Thinking mode is enabled by default, with the default effort being `high`」、`{"thinking": {"type": "enabled/disabled"}}`、段階の表は `low/high/max` で `ultra` を `max` の別名に挙げる）
- [Ollama: OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)（2026-11-21 確認: `/v1/chat/completions` に「`reasoning_effort` (model-defined names and compatibility aliases)」と `reasoning` の `effort`、「Boolean-only models that support enabling thinking map recognized compatibility efforts to `true`, while `"none"` requests `false`」「`"minimal"` maps to `"low"`, and `"xhigh"` or `"ultra"` map to `"max"`」「`"none"` uses `"low"` because GPT-OSS cannot disable thinking」「supported names are applied exactly and unsupported names resolve to the model default」）
- [Ollama: Thinking](https://docs.ollama.com/capabilities/thinking)（2026-11-21 確認: 独自 API の `think` は `true`・`false`・段階の文字列。「Models that cannot disable thinking use their lowest supported level instead」）
- [LM Studio: Chat Completions](https://lmstudio.ai/docs/developer/openai-compat/chat-completions)（2026-11-21 確認: 受け付けるパラメータの一覧に `reasoning_effort` がない）
- [Ollama: OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)・[Ollama: Thinking](https://docs.ollama.com/capabilities/thinking)・[LM Studio: Chat Completions](https://lmstudio.ai/docs/developer/openai-compat/chat-completions)（2026-11-26 確認: どれも、互換 API が推論テキストを返すか、返すならどのフィールドかを書いていない。推論テキストを頼む・断るパラメータも互換 API にはない。Ollama の独自 API は `message.thinking`、LM Studio の独自 API `/api/v1/chat` は `type: "reasoning"` の出力アイテムで返す）
- [OpenAI: File inputs](https://developers.openai.com/api/docs/guides/pdf-files)（2026-11-23 確認: 「Chat Completions accepts only PDF files as `file` content parts」、表で `file_url` は Chat Completions では「Not supported」、Chat Completions の例はない）
- [OpenAI: Create chat completion](https://developers.openai.com/api/reference/resources/chat)（2026-11-23 確認: `file: object { file_data, file_id, filename }`、`file_data` は「The base64 encoded file data, used when passing the file to the model as a string」）
- [DeepSeek: Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion)・[File Parsing](https://api-docs.deepseek.com/guides/document_parsing/)（2026-11-23 確認: パーツは text・image_url・file。file は `file_id` か `file_data`（と `filename`）を直接置く。「A document that needs parsing can be referenced only by `file_id`. Passing the file content or a file URL in the request is not supported.」）
- [Ollama: OpenAI compatibility](https://docs.ollama.com/openai)（2026-11-23 確認: messages は Text `content`、Image `content`（Base64 encoded image は対応、Image URL は非対応）、Array of `content` parts。ファイルの記述はない）
- [LM Studio: OpenAI Compatibility Endpoints](https://lmstudio.ai/docs/developer/openai-compat)（2026-11-23 確認: 「Chat Completions (text and images)」。ファイルの記述はない）

## 関連する未検証の前提

U10、U11、U12、U15、U16、U17、U18、U21、U23、U27、U29（[実 API 検証](../exec-plans/active/real-api-verification.md)）
