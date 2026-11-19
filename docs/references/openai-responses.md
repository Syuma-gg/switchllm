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

- 既定ではレスポンスが保存される（`store`）。`extra={"store": False}` にしても、reasoning アイテムには `encrypted_content` が既定で付くので、`include` の指定は要らない。手で状態を管理するときは、output アイテムを手を加えずに input へ戻す（U1）
- reasoning アイテムより前の履歴を変えると、API がその reasoning を捨てることがある（`res.message` を加工せずに積めば変わらない）
- usage の `output_tokens` は推論トークンを含む（`output_tokens_details.reasoning_tokens` はその内訳）
- `strict` は既定値に依存しないよう `false` を明示している。`true` にするには、全プロパティを required にして `additionalProperties: false` を付ける必要がある（U3）

## 構造化出力

`response_schema=` は `text.format` の `json_schema` で送る。`name` はスキーマの `title`（条件に合わなければ `"response"`）、`strict` は常に `true`（[判断](../design-docs/structured-output.md)）。以下は 2026-11-20 に文書で確かめた形式。

- 指定: `text: {"format": {"type": "json_schema", "name", "schema", "strict", "description"}}`。`description` だけ省略できる。`name` は英数字・`_`・`-` で 64 文字まで。`strict` の既定は `false` で、スキーマどおりの出力が保証されるのは `true` のときだけ
- `strict: true` の条件: すべてのオブジェクトに `additionalProperties: false` を付け、すべてのプロパティを `required` に入れる（省略できる項目は `["string", "null"]` のように null との union にする）。ルートはオブジェクトにし、`anyOf` にはしない。対応しないスキーマを送るとエラーになる（`invalid_json_schema`）
- 使えるもの: 型は string・number・boolean・integer・object・array・enum・anyOf。string の `pattern` と `format`（date-time・time・date・duration・email・hostname・ipv4・ipv6・uuid）、number の `multipleOf`・`maximum`・`exclusiveMaximum`・`minimum`・`exclusiveMinimum`、array の `minItems`・`maxItems`。`$defs` と `$ref`（再帰を含む）
- 使えないもの: `allOf`・`not`・`dependentRequired`・`dependentSchemas`・`if`・`then`・`else`
- 上限: プロパティは合わせて 5000 個まで、入れ子は 10 段まで、enum の値は合わせて 1000 個まで。プロパティ名・定義名・enum と const の値の文字列は合わせて 120,000 文字まで
- 出力のキーは、スキーマのキーの順に並ぶ
- 結果: `output` の `message` の `output_text` に、JSON の文字列が入る。HTTP の応答に読んだ値はない（`output_parsed` は SDK の機能）。拒否は content の `{"type": "refusal", "refusal": "..."}` で、`status` は `completed` のまま。`max_output_tokens` や `content_filter` で止まると `status: "incomplete"` になり、JSON が途中までのことがある
- ストリーミング: いつもと同じ `response.output_text.delta`（拒否は `response.refusal.delta`）。公式は「Text deltas and `response.output_text.done` do not guarantee completion」とする
- JSON モード: `text.format: {"type": "json_object"}`。JSON として読めることだけを保証し、形は保証しない。文脈に「JSON」の語がないとエラーになる。公式は、使えるなら構造化出力を勧める
- ツールとの併用: 文書は「ツールにつなぐなら function calling、利用者への答えの形なら `text.format`」という使い分けを書くが、同じリクエストで両方を使えるかは書いていない（U22）
- あるスキーマを初めて使うリクエストは、スキーマを処理するぶん遅くなる。同じスキーマなら2回目からは遅くならない

## 出典

- [Structured model outputs](https://developers.openai.com/api/docs/guides/structured-outputs)（2026-11-20 確認: 「all fields or function parameters must be specified as `required`」「we require developers to set `additionalProperties: false` to opt into Structured Outputs」「the root level object of a schema must be an object, and not use `anyOf`」「If you turn on Structured Outputs by supplying `strict: true` and call the API with an unsupported JSON Schema, you will receive an error」、上限と使えるキーワードの一覧、拒否の例（`"type": "refusal"` で `"status": "completed"`）、JSON モードの「the API will throw an error if the string "JSON" does not appear somewhere in the context」、初回の遅れ）
- [Create a response](https://developers.openai.com/api/docs/api-reference/responses/create)（2026-11-20 確認: `text.format` の json_schema の項目。自動取得では該当箇所を要約でしか読めなかったので、`name` の制約と `strict` の既定値は Chat Completions のリファレンスの原文でも確かめた）
- [Text generation](https://developers.openai.com/api/docs/guides/text)（2026-11-20 確認: 「It is not safe to assume that the model's text output is present at `output[0].content[0].text`」、`output_text` は SDK の便宜の属性）
- [Migrate to the Responses API](https://developers.openai.com/api/docs/guides/migrate-to-responses)
- [Function calling](https://developers.openai.com/api/docs/guides/function-calling)（2026-11-15 確認: `tool_choice` は「Auto (Default)」。API リファレンスのページは自動取得を拒否したため、ガイドで確認した）
- [Reasoning models](https://developers.openai.com/api/docs/guides/reasoning) の「Preserve reasoning without stored responses」（2026-11-17 確認: 「reasoning items in the response's `output` array include an `encrypted_content` property by default」、`include` の `reasoning.encrypted_content` は互換のために受け付けるが要らない、「changes to the history preceding a reasoning item can cause the API to drop that reasoning」、usage の例で `output_tokens` が `reasoning_tokens` を含む）
- [Migrate to the Responses API](https://developers.openai.com/api/docs/guides/migrate-to-responses)（2026-11-17 確認: 「Pass prior `output` Items back into the next request when you need to manage or trim context yourself」「Responses are stored by default」）
- [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)（2026-11-17 確認: 過去の会話を手で組み立てる例が、assistant を文字列の content で input に入れている。U2）
- [Token counting](https://developers.openai.com/api/docs/guides/token-counting)（2026-11-17 確認: 「Reported output token usage includes all tokens generated by the model, not only the text visible in a response」）
- [GPT-5.5](https://developers.openai.com/api/docs/models/gpt-5.5)（2026-11-17 確認: モデル ID `gpt-5.5`。U13）

## 関連する未検証の前提

U1〜U3、U22（[実 API 検証](../exec-plans/active/real-api-verification.md)）
