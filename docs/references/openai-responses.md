# OpenAI Responses API

- アダプター: `providers/openai_responses.py`（プリセット `openai`）
- エンドポイント: `POST https://api.openai.com/v1/responses`
- 認証: `Authorization: Bearer $OPENAI_API_KEY`。`headers=` に同じ名前のヘッダーを渡すと、大文字と小文字を区別せずに置き換える（[判断](../design-docs/custom-headers.md)）
- このアダプターを使う理由: [判断](../design-docs/openai-responses-api.md)

## 共通型との対応

| 共通 | Responses API |
|---|---|
| system | トップレベルの `instructions` |
| user | `{"role": "user", "content": 文字列 か [input_text / input_image / input_file]}` |
| assistant（再送しないとき） | `{"role": "assistant", "content": 文字列}` と `function_call` アイテム |
| ツール結果 | `{"type": "function_call_output", "call_id", "output"}`。エラーは `output` の先頭に `Error: ` |
| max_tokens | `max_output_tokens` |
| stop | **存在しない**ので `UnsupportedFeatureError` |
| ツール定義 | `{"type": "function", "name", "description", "parameters", "strict": false}` |
| tool_choice | `"auto"` / `"none"` / `"required"` / `{"type": "function", "name"}`。指定がなければ送らない（既定は `auto`） |
| 画像 | `{"type": "input_image", "image_url": URL か data URL}` |
| ファイル | `{"type": "input_file", "file_data": data URL, "filename"}`。`filename` は `FilePart` にあるときだけ。種類は確かめずに送る（下の「ファイルの入力」） |

## レスポンス

- `output` 配列の次のアイテムを読む
  - `message`（`output_text` / `refusal`）
  - `function_call`（`call_id`。arguments は JSON の文字列）
  - `reasoning`（`summary`）
- 再送のために、`output` 全体を `ProviderState` に入れる
- stop_reason:
  - `function_call` があれば `tool_use`
  - `incomplete_details.reason == "max_output_tokens"` は `max_tokens`
  - `content_filter` か refusal は `content_filter`
  - `status == "completed"` は `end`

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

- 既定では、レスポンスが保存される（`store`）
  - `extra={"store": False}` にしても、reasoning アイテムには `encrypted_content` が既定で付くので、`include` の指定は要らない
  - 手で状態を管理するときは、output アイテムを手を加えずに input へ戻す（U1）
- reasoning アイテムより前の履歴を変えると、API がその reasoning を捨てることがある（`res.message` を加工せずに積めば変わらない）
- usage の `output_tokens` は推論トークンを含む（`output_tokens_details.reasoning_tokens` はその内訳）
- `strict` は、既定値に頼らないよう `false` を明示している。`true` にするには、全プロパティを required にし、`additionalProperties: false` を付ける必要がある（U3）

## 構造化出力

`response_schema=` は `text.format` の `json_schema` で送る。`name` はスキーマの `title`（条件に合わなければ `"response"`）、`strict` は常に `true`（[判断](../design-docs/structured-output.md)）。以下は 2026-11-20 に文書で確かめた形式。

- 指定: `text: {"format": {"type": "json_schema", "name", "schema", "strict", "description"}}`
  - `description` だけ省ける
  - `name` は英数字・`_`・`-` で 64 文字まで
  - `strict` の既定は `false`。スキーマどおりの出力が保証されるのは `true` のときだけ
- `strict: true` の条件:
  - すべてのオブジェクトに `additionalProperties: false` を付ける
  - すべてのプロパティを `required` に入れる（省ける項目は、`["string", "null"]` のように null との union にする）
  - ルートはオブジェクトにし、`anyOf` にはしない
  - 対応しないスキーマを送ると、エラーになる（`invalid_json_schema`）
- 使えるもの:
  - 型は string・number・boolean・integer・object・array・enum・anyOf
  - string の `pattern` と `format`（date-time・time・date・duration・email・hostname・ipv4・ipv6・uuid）
  - number の `multipleOf`・`maximum`・`exclusiveMaximum`・`minimum`・`exclusiveMinimum`
  - array の `minItems`・`maxItems`
  - `$defs` と `$ref`（再帰を含む）
- 使えないもの: `allOf`・`not`・`dependentRequired`・`dependentSchemas`・`if`・`then`・`else`
- 上限:
  - プロパティは合わせて 5000 個まで、入れ子は 10 段まで、enum の値は合わせて 1000 個まで
  - プロパティ名・定義名・enum と const の値の文字列は、合わせて 120,000 文字まで
- 出力のキーは、スキーマのキーの順に並ぶ
- 結果:
  - `output` の `message` の `output_text` に、JSON の文字列が入る。HTTP の応答に、読んだ値はない（`output_parsed` は SDK の機能）
  - 拒否は content の `{"type": "refusal", "refusal": "..."}` で、`status` は `completed` のまま
  - `max_output_tokens` や `content_filter` で止まると `status: "incomplete"` になり、JSON が途中までのことがある
- ストリーミング: いつもと同じ `response.output_text.delta`（拒否は `response.refusal.delta`）。公式は「Text deltas and `response.output_text.done` do not guarantee completion」とする
- JSON モード: `text.format: {"type": "json_object"}`
  - JSON として読めることだけを保証し、形は保証しない
  - 文脈に「JSON」の語がないとエラーになる
  - 公式は、使えるなら構造化出力を勧める
- ツールと一緒に使うとき: 文書は「ツールにつなぐなら function calling、利用者への答えの形なら `text.format`」という使い分けを書くが、同じリクエストで両方を使えるかは書いていない（U22）
- 速さ: あるスキーマを初めて使うリクエストは、スキーマを処理するぶん遅くなる。同じスキーマなら、2回目からは遅くならない

## 推論の強さ

`reasoning_effort=` は `reasoning: {"effort": ...}` で、段階の名前を変えずに送る（[判断](../design-docs/reasoning-effort.md)）。以下は 2026-11-21 に文書で確かめた形式。

- 指定: `reasoning: {"effort": "low"}`
  - 段階は `none`・`minimal`・`low`・`medium`・`high`・`xhigh`・`max` の7つ
  - リファレンスの既定は `medium` だが、ガイドは「Defaults are also model-dependent rather than universal」とする
- モデルごとに使える段階が違う（「Some models support only a subset of these values」）
  - gpt-5.5 は `none`・`low`・`medium`（既定）・`high`・`xhigh` で、`minimal` と `max` は挙がっていない
  - `none` を受け付けないモデル（GPT-6 Astra、GPT-6.2 Astra・Sol）は 400 を返す。GPT-6.1 Sol は `none` も `minimal` も受け付けない
  - 挙がっていない段階を送ったときに、400 になるか、黙って置き換えるかは、`none` 以外は書かれていない（U23）
- 数で予算を決める指定はない。段階だけ
- `max_output_tokens` は推論トークンを含む
  - 公式は、試し始めるときは推論と出力に 25,000 トークン以上を取ることを勧める
  - 上限で止まると `status: "incomplete"`・`reason: "max_output_tokens"` で、見える出力がないこともある
- `temperature`・`top_p` を推論モデルで使えるか、段階によるかは書かれていない（U23）
- GPT-6 以降の一部のモデルは、会話の途中で `configuration_update` のアイテムで段階を変えられる。switchllm は使わない
- ほかの項目: `reasoning.mode`（`standard`・`pro`。GPT-5.6 と GPT-6）と `reasoning.context` もある。共通化しないので、使うなら `extra=` で渡す（深いマージなので、`reasoning_effort=` と一緒に使える）

### 推論の要約（`reasoning.summary`）

switchllm は、`reasoning_summary=True` のとき `summary: "auto"` を送る（[判断](../design-docs/reasoning-summary.md)。U32）。

- 値は `auto`・`concise`・`detailed`。指定しなければ、要約は返らない（「This output will not be included unless you explicitly opt in」）
- `auto` は、多くのモデルで `detailed` と同じ
- 最新の推論モデルでは、要約を返させるのに組織の認証が要ることがある（「you may need to complete organization verification」）。認証していないときに、エラーになるか、要約が返らないだけかは書かれていない
- 推論しないモデルに送ったときの挙動も、書かれていない
- `reasoning.generate_summary` は非推奨で、`summary` を使う

## ファイルの入力

`FilePart` は `input_file` の `file_data` で送る（[判断](../design-docs/file-input.md)）。以下は 2026-11-23 に文書で確かめた形式。

- 形: `{"type": "input_file", "file_data": ..., "filename": ...}`
  - ほかに `file_id`（Files API でアップロードしたもの）、`file_url`（外部の URL）、`detail`（PDF のページの画像の細かさ。`auto`・`low`・`high`）がある
  - スキーマではどれも省け、`filename` が要るとは書かれていない（ガイドの例はいつも付けている）
  - switchllm は `file_data` と `filename` だけを使う
- `file_data` の形: ガイドのコードの例は data URL（`data:application/pdf;base64,...`）。curl の例は「...base64 encoded PDF bytes here...」で、スキーマも base64 とだけ書く。switchllm は、例に合わせて data URL で送る（U28）
- 種類:
  - PDF、テキストとコード（.txt・.md・.json・.html・.xml など）、文書（.doc・.docx・.rtf・.odt）、プレゼンテーション（.ppt・.pptx）、表計算（.csv・.xls・.xlsx など）
  - 一覧が長く、拡張子で書かれているので、switchllm は種類を確かめずに送り、受け付けないものは API のエラーにする
  - PDF 以外の種類を、`filename` の拡張子と data URL の種類のどちらで見分けるかは書かれていない（U28）
- 読み方:
  - PDF は「the API extracts both text and page images」で、画像を読めるモデルには、文字とページの画像の両方を渡す。そのぶんトークンが増える
  - PDF 以外は文字だけを取り出し、埋め込まれた画像や図は渡さない
  - 表計算は、シートごとに先頭の 1,000 行まで読み、モデルが作った要約と見出しを足す
- 上限: 「each file must be under 50 MB. The combined limit across all files in the request is 50 MB」。ページ数の上限は書かれていない

## エラー

- 本文の `error` に `message`・`type`・`param`・`code` がある。Error codes のガイドは、`error.param` が原因の引数を指す例（`service_tier`）と、`error.code` で原因を見分けることを書く。本文の全体の例はない
- モデルが受け付けない値（`reasoning.effort` の段階など）のエラーの文言は、文書にない
- switchllm は、送った共通の引数のフィールド名がエラーに出てきたら、`str(e)` に案内を足す（[判断](../design-docs/error-hints.md)）。`param` か文言に `reasoning.effort`・`tool_choice`・`temperature` などが出てくれば当たる（U38）

## 出典

同じページを何度か確かめたものは、日付ごとに分けて書く。

### リクエストの形

- [Create a response](https://developers.openai.com/api/docs/api-reference/responses/create)
  - 2026-11-20: `text.format` の json_schema の項目。自動取得では該当箇所を要約でしか読めなかったので、`name` の制約と `strict` の既定値は、Chat Completions のリファレンスの原文でも確かめた
  - 2026-11-21: `reasoning.effort` は「Currently supported values are `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, and `max`」で、既定は `"medium"`。`summary` は「One of `auto`, `concise`, or `detailed`」。`max_output_tokens` は「including visible output tokens and reasoning tokens」。`temperature` は「We generally recommend altering this or `top_p` but not both」だけで、推論との関係はない
  - 2026-11-26（`reasoning`）: `summary` は「One of `auto`, `concise`, or `detailed`」。「`concise` is supported for `computer-use-preview` models and all reasoning models after `gpt-5`」。`generate_summary` は「**Deprecated:** use `summary` instead.」。`summary` の既定は書かれていない
- [Create a response](https://developers.openai.com/api/reference/resources/responses/methods/create)（別の URL）
  - 2026-11-23: `input_file` の `file_data`・`file_id`・`file_url`・`filename`・`detail`。どれも省ける
- [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
  - 2026-11-15: `tool_choice` は「Auto (Default)」。API リファレンスのページは自動取得を拒否したので、ガイドで確かめた

### 会話の状態・usage

- [Migrate to the Responses API](https://developers.openai.com/api/docs/guides/migrate-to-responses)
  - 2026-11-17: 「Pass prior `output` Items back into the next request when you need to manage or trim context yourself」「Responses are stored by default」
- [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)
  - 2026-11-17: 過去の会話を手で組み立てる例が、assistant を文字列の content で input に入れている（U2）
- [Text generation](https://developers.openai.com/api/docs/guides/text)
  - 2026-11-20: 「It is not safe to assume that the model's text output is present at `output[0].content[0].text`」。`output_text` は SDK の便宜の属性
- [Token counting](https://developers.openai.com/api/docs/guides/token-counting)
  - 2026-11-17: 「Reported output token usage includes all tokens generated by the model, not only the text visible in a response」
- [GPT-5.5](https://developers.openai.com/api/docs/models/gpt-5.5)
  - 2026-11-17: モデル ID `gpt-5.5`（U13）
  - 2026-11-21: 「Reasoning.effort supports: none, low, medium (default), high and xhigh」

### 推論

- [Reasoning models](https://developers.openai.com/api/docs/guides/reasoning)
  - 2026-11-17（「Preserve reasoning without stored responses」）: 「reasoning items in the response's `output` array include an `encrypted_content` property by default」。`include` の `reasoning.encrypted_content` は、互換のために受け付けるが要らない。「changes to the history preceding a reasoning item can cause the API to drop that reasoning」。usage の例で、`output_tokens` が `reasoning_tokens` を含む
  - 2026-11-21: 「Supported values are model-dependent and can include `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, and `max`」「Some models support only a subset of these values, so check the relevant model page before choosing a setting」。GPT-6 Astra は「Setting `reasoning.effort` (Responses) or `reasoning_effort` (Chat Completions) to `none` returns HTTP 400」。GPT-6.1 Sol は「does not support `none` or `minimal`」。要約は opt-in。「OpenAI recommends reserving at least 25,000 tokens for reasoning and outputs」。`mode` と `context`
  - 2026-11-26（「Reasoning summaries」）: 「This output will not be included unless you explicitly opt in to including reasoning summaries.」「`auto` will be equivalent to `detailed` for most reasoning models today」「Before using summarizers with our latest reasoning models, you may need to complete organization verification」「Reasoning summary output is part of the `summary` array in the `reasoning` output item.」。どのモデルが要約に対応するかは、モデルのページを見るように書く

### 構造化出力・ファイル

- [Structured model outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
  - 2026-11-20: 「all fields or function parameters must be specified as `required`」「we require developers to set `additionalProperties: false` to opt into Structured Outputs」「the root level object of a schema must be an object, and not use `anyOf`」「If you turn on Structured Outputs by supplying `strict: true` and call the API with an unsupported JSON Schema, you will receive an error」。上限と使えるキーワードの一覧。拒否の例（`"type": "refusal"` で `"status": "completed"`）。JSON モードの「the API will throw an error if the string "JSON" does not appear somewhere in the context」。初回の遅れ
- [File inputs](https://developers.openai.com/api/docs/guides/pdf-files)
  - 2026-11-23: 対応する種類の表。「For **PDF files**, the API extracts both text and page images」。PDF 以外は文字だけ。表計算の 1,000 行。「each file must be under 50 MB」。`file_data` のコードの例が data URL で、curl の例は base64 とだけ書く。Chat Completions は「accepts only PDF files as `file` content parts」

### エラー

- [Error codes](https://developers.openai.com/api/docs/guides/error-codes)
  - 2026-11-28: 「The API returns the message "Invalid `service_tier` argument: The requested service tier is not allowed for this project." as an `invalid_request_error` with `error.param` set to `service_tier`」。「inspect `error.code` to identify the specific cause」。本文の全体の例と、`unsupported_value` などの code は書かれていない

## 関連する未検証の前提

U1〜U3、U22、U23、U28、U32、U38（[実 API 検証](../exec-plans/active/real-api-verification.md)）
