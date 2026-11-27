# Gemini API（generateContent）

- アダプター: `providers/gemini.py`（プリセット `gemini` / `google`）
- エンドポイント: `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`（ストリーミングは `:streamGenerateContent?alt=sse`）
- 認証: `x-goog-api-key: $GEMINI_API_KEY`（または `GOOGLE_API_KEY`）。`headers=` に同じ名前のヘッダーを渡すと、大文字と小文字を区別せずに置き換える（[判断](../design-docs/custom-headers.md)）

## 共通型との対応

| 共通 | generateContent |
|---|---|
| system | `systemInstruction.parts` |
| user | `contents[]`（`role: "user"`）の `parts`（`text` / `inlineData`）。画像の URL は非対応で `UnsupportedFeatureError` |
| assistant（再送しないとき） | `role: "model"` の `text` と `functionCall`（`args` はオブジェクト） |
| ツール結果 | user の `functionResponse`（`name` が必須、`response` はオブジェクト。下の一覧） |
| 続く同じ role | 1つの content にまとめる（並列の呼び出しは FC1, FC2, FR1, FR2 の順） |
| max_tokens / temperature / stop | `generationConfig` の `maxOutputTokens` / `temperature` / `stopSequences` |
| ツール定義 | `tools[].functionDeclarations[]`。スキーマは `parameters` に入れる（`schema_field="parametersJsonSchema"` で変えられる。U4） |
| tool_choice | `toolConfig.functionCallingConfig.mode` の `AUTO` / `NONE` / `ANY`（特定のツールは `ANY` と `allowedFunctionNames`）。指定がなければ `toolConfig` を送らない（既定は `AUTO`） |
| ファイル | `{"inlineData": {"mimeType", "data"}}`（`data` は base64）。種類は確かめずに送る（下の「ファイルの入力」） |

ツール結果の `response`:

- 結果が JSON オブジェクトの文字列なら、そのまま
- それ以外は `{"result": ...}`
- エラーは `{"error": ...}`

## レスポンス

- `candidates[0].content.parts` の `text`（`thought: true` は推論）と `functionCall` を読む。再送のために、`parts` 全体を `ProviderState` に入れる（`thoughtSignature` を含む）
- `functionCall` に id がなければ、仮 ID `switchllm_...` を振る。送り返すときは id を付けない（U6）
- stop_reason:
  - `STOP` → `end`
  - `MAX_TOKENS` → `max_tokens`
  - `SAFETY` などのフィルター → `content_filter`
  - プロンプトそのものがブロックされると、`candidates` が空で `promptFeedback.blockReason` が入るので `content_filter`
- usage: `promptTokenCount` と、`candidatesTokenCount + thoughtsTokenCount`（U14）
  - `promptTokenCount` は、キャッシュした内容を含む
  - `totalTokenCount` は「prompt + tool-use prompt + thoughts + response candidates」なので、candidates と thoughts は別に数えている
  - candidates が thoughts を含まないとは明記していないので、E2E テストの `test_usage_breakdown` で、total と内訳の和を比べる

## ストリーミング

- SSE の各イベントは、部分的なレスポンス。最後のチャンクに `finishReason` が入る。`finishReason` も `promptFeedback.blockReason` もないまま閉じたら `StreamError`（U16）
- 続くテキストは結合するが、**署名付きのパートどうしは結合しない**（公式の文書の注意）
- 最後に、非ストリーミングと同じ形の dict を組み立てて、`parse_response` を使い回す

## 注意点

- Gemini 3 以降、関数呼び出しの往復で `thoughtSignature` を返さないと 4xx になる。並列の呼び出しでは、署名は最初の functionCall にだけ付く
- ほかのプロバイダーの履歴を送るときは、最初の functionCall にダミー署名 `skip_thought_signature_validator` を付ける。公式は「最終手段で、性能が落ちる可能性がある」と書いている（`inject_dummy_signatures=False` で止める。U5）
- Google は generateContent を「レガシー」とし、新しい開発には Interactions API を勧めている（U7、[負債](../exec-plans/tech-debt-tracker.md)）

## 構造化出力

`response_schema=` は、`generationConfig` の `responseMimeType: "application/json"` と `responseJsonSchema` で送る（[判断](../design-docs/structured-output.md)）。以下は 2026-11-20 に文書で確かめた形式。

- 指定: `generationConfig` に `responseMimeType: "application/json"` と `responseJsonSchema`（JSON Schema）
  - generateContent の構造化出力のページの、Python・JavaScript・Go の例はこの形
  - 同じページの REST の例は、`generationConfig.responseFormat.text: {"mimeType": "APPLICATION_JSON", "schema": {...}}` という別の形
  - API リファレンスの `responseJsonSchema` の説明は「An internal detail. Use `responseJsonSchema` rather than this field.」で、自分自身を指している
  - どちらの形を受け付けるかは、実 API で確かめる（U20）
- 別に `responseSchema`（OpenAPI 3.0 のサブセットの Schema オブジェクト）もある。どちらを勧めるか、両方を指定できるかは書かれていない
- 使えるもの:
  - 型は string・number・integer・boolean・object・array・null（null との union は `{"type": ["string", "null"]}`）
  - object の `properties`・`required`・`additionalProperties`（真偽値かスキーマ）
  - string の `enum`・`format`
  - number と integer の `enum`・`minimum`・`maximum`
  - array の `items`・`prefixItems`・`minItems`・`maxItems`
  - 説明用の `title`・`description`
  - 例では `anyOf` と `$defs`・`$ref` も使っている（再帰するなら、再帰する参照を、省ける項目か null にできる項目にする）
- 使えないキーワードは「The model ignores unsupported properties」（エラーにならず、効かない）
- 大きすぎるスキーマや入れ子の深いスキーマは、400 `INVALID_ARGUMENT`。数の上限は書かれていない
- 出力のキーは、スキーマのキーの順に並ぶ。Gemini 2.0 だけは、`propertyOrdering` で順を指定する必要がある
- 対応するモデル: 表には Gemini 4 の各モデル、3.x、2.5、2.0 が載っていて、どれも対応している
- ツールと一緒に使うとき: 「Gemini 4 and Gemini 3 let you combine Structured Outputs with built-in tools, including ... Function Calling」。プレビューの機能で、Gemini 4 と 3 のモデルだけ
- 結果: 「a syntactically valid JSON string matching the provided schema」
  - 値の意味までは保証しないので、アプリでも確かめる
  - `MAX_TOKENS` で止まったときの JSON の扱いは書かれていない
- ストリーミング: 「The streamed chunks will be valid partial JSON strings, which can be concatenated to form the final, complete JSON object」

## 推論の強さ

- `reasoning_effort=` は `generationConfig.thinkingConfig.thinkingLevel` で、段階の名前を変えずに送る。`none` と `max` は、送る前に `UnsupportedFeatureError` にする（[判断](../design-docs/reasoning-effort.md)）
- `reasoning_summary=True` は、同じ `thinkingConfig` に `includeThoughts: true` を足す（[判断](../design-docs/reasoning-summary.md)。U34）

以下は 2026-11-21 に文書で確かめた形式（要約は 2026-11-26）。

- 指定: `generationConfig: {"thinkingConfig": {"thinkingLevel": "low"}}`。`thinkingConfig` の項目は `thinkingLevel`・`thinkingBudget`・`includeThoughts` の3つ
- 段階:
  - API リファレンスの列挙は `MINIMAL`・`LOW`・`MEDIUM`・`HIGH`・`XHIGH`（と `THINKING_LEVEL_UNSPECIFIED`）。`none` と `max` はない
  - ガイドの例は小文字（`"low"`、`"xhigh"`）で送っていて、switchllm も小文字で送る。大文字・小文字のどちらも受け付けるかは書かれていない（U26）
- `thinkingLevel` は「required for Gemini 4 and recommended for Gemini 3 models」。指定しなければ、モデルの既定の段階
- `maxOutputTokens` は推論トークンを含む（「including thought tokens」）。推論の途中で上限に達すると `MAX_TOKENS` で止まり、推論の分も課金される
- `temperature`: 「This field is no longer supported and is silently ignored on Gemini 3.6 Flash and newer models」。エラーにはならず、効かない

### モデルごとの段階

| モデル | 既定 | 使える段階 |
|---|---|---|
| gemini-4-carbon・argon | medium | low・medium・high |
| gemini-4-neon・hydrogen | high | low・medium・high・xhigh |
| gemini-3.8-flash | medium | low・medium・high |
| gemini-3.6-flash | medium | minimal・low・medium・high |
| gemini-3.5-flash-lite・gemini-3.1-flash-lite | minimal | minimal・low・medium・high |
| gemini-3.1-pro-preview | high | low・medium・high |

- `minimal` は「Not supported on Gemini 4 models or `gemini-3.8-flash`」
- 使えない段階を送ったときの挙動は書かれていない（U26）

### 推論を切る・予算で決める

- `thinkingLevel` には、切る段階がない
  - Gemini 4 は「You cannot disable thinking」
  - Gemini 3 の Flash 系の `minimal` は「the model likely will not think (though it still potentially can)」で、切るのとは違う
- `thinkingBudget`（数で予算を決める）は、Gemini 2.5 のためのもの
  - `0` で切り（2.5 Pro は切れない）、`-1` で動的
  - 「For Gemini 4 and Gemini 3 models, use thinkingLevel instead of thinkingBudget」
  - Gemini 4 では、`thinkingBudget` だけを送ると、近い `thinkingLevel` に置き換えて警告を返す。両方を送ると 400
  - Gemini 2.5 は `thinkingLevel` を受け付けない（「Gemini 2.5 series models don't support `thinkingLevel`; use `thinkingBudget` instead」）。API リファレンスは、要約でしか読めなかったが、エラーになるとする
- Gemini 2.5:
  - `gemini-2.5-flash` と `gemini-2.5-pro` は非推奨で、2026-12-01 から、それぞれ `gemini-3.5-flash-lite` と `gemini-3.8-flash` へ自動で回される
  - 2.5 で推論の強さを決めるなら、`extra={"generationConfig": {"thinkingConfig": {"thinkingBudget": N}}}` を渡す

### 推論の本文（`includeThoughts`）

- `includeThoughts: true` を送ると、`thought: true` のパートで要約が返る。生の推論ではない（「Thought summaries are summarized versions of the model's raw thoughts」）
- 既定の値は書かれていないが、例はどれも明示している
- 「thoughts are returned only when available」なので、頼んでも返らないことがある
- ストリーミングでは、要約が少しずつ返る
- 推論しないモデルに送ると、エラーになる（API リファレンスの要約でしか読めていない）
- 料金は、要約ではなく推論の全体で決まる

## ファイルの入力

`FilePart` は、画像と同じ `inlineData` で送る（[判断](../design-docs/file-input.md)）。以下は 2026-11-23 に文書で確かめた。

- 形: Part の `inlineData`（Blob）は `mimeType` と `data`（「Raw bytes for media formats」。JSON では base64 の文字列で、data URL にしない）。API リファレンスの `mimeType` の例に `application/pdf` がある
- 今の文書の例（Document understanding・File input methods）は、どれも Interactions API（`/v1beta/interactions` の `{"type": "document", "data", "mime_type"}`）の形で、generateContent で PDF を送る例はない。switchllm は、Part のスキーマから推して `inlineData` で送る（U31）
- 種類:
  - 「Gemini models can process documents in PDF format, using native vision」
  - TXT・Markdown・HTML などは「processed as plain text」
  - 対応する種類の一覧（「not comprehensive」とされる）は、text/html・text/css・text/plain・text/xml・text/csv・text/rtf・text/javascript・application/json・application/pdf と、画像・音声・動画
  - Office と OpenDocument のファイルも渡せると書かれている
  - 一覧が網羅的でないので、switchllm は種類を確かめずに送る
- 上限: インラインは「100 MB per request or payload (50 MB for PDFs)」。それより大きいときは Files API（switchllm は扱わない）。1つのリクエストで、合わせて 1000 ページまでの PDF を読める（文脈の長さに収まる限り）
- URL: `fileData` の `fileUri` は Files API の URI。公開された HTTPS の URL を渡す方法は Interactions の形の例しかなく、generateContent で使えるかは書かれていない（switchllm はファイルの URL を扱わない）

## 出典

同じページを何度か確かめたものは、日付ごとに分けて書く。Gemini の文書（ai.google.dev）は、aistudio.google.com の同じパスへ転送されるようになった（2026-11-17 に気づいた）。リンクは転送されるので、元の URL のままにしている。

### リクエストの形・API リファレンス

- [API reference: generateContent](https://ai.google.dev/api/generate-content)
  - 2026-11-17（UsageMetadata。aistudio.google.com/api-reference/generate-content へ転送される）: `thoughtsTokenCount` は「Number of tokens of thoughts for thinking models」。`totalTokenCount` は「prompt + tool-use prompt + thoughts + response candidates」（U14）
  - 2026-11-20: `responseMimeType` は `text/plain`（既定）か `application/json`。`responseJsonSchema` の説明は、上の自己参照の文。Schema オブジェクトは「Represents a select subset of an OpenAPI 3.0 schema object」
  - 2026-11-21（GenerationConfig）: `temperature` は「This field is no longer supported and is silently ignored on Gemini 3.6 Flash and newer models」。`maxOutputTokens` は「including thought tokens」。ThinkingConfig の項目と列挙は、自動取得の要約でしか読めなかった
  - 2026-11-26（ThinkingConfig。自動取得の要約でしか読めなかった）: `includeThoughts` は「If true, thoughts are returned only when available」。「An error will be returned if this field is set for models that don't support thinking」。既定の値は書かれていない
- [Generating content](https://aistudio.google.com/api-reference/generate-content)
  - 2026-11-23: Part の `inlineData`（Blob の `mimeType`・`data`）。`mimeType` の例に `application/pdf`。リクエストの例はない
- [API reference: FunctionCallingConfig](https://ai.google.dev/api/caching#FunctionCallingConfig)
  - 2026-11-15: mode は「If unspecified, the default value will be set to AUTO」
- [Thought signatures](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures)（日付の記録なし）
- [Function calling](https://ai.google.dev/gemini-api/docs/generate-content/function-calling)（日付の記録なし）
- [OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)（日付の記録なし）

### モデル

- [Models](https://ai.google.dev/gemini-api/docs/models)
  - 2026-11-17（「Latest」。aistudio.google.com/docs/models へ転送される）: `gemini-flash-latest` のようなエイリアスは「Points to the latest release for a specific model variation」。指す先を変える前に「A 2-week notice will be provided through email」。今どのモデルを指すかは書かれていない（U13）
  - 2026-11-21: `gemini-2.5-flash` と `gemini-2.5-pro` は「Effective December 1, 2026, all API requests ... will automatically route to」それぞれ `gemini-3.5-flash-lite` と `gemini-3.8-flash`。`gemini-3-pro-preview` は提供終了

### 推論

- [Thinking（generateContent）](https://ai.google.dev/gemini-api/docs/generate-content/thinking)（aistudio.google.com/docs/thinking?generate-content=true へ転送される）
  - 2026-11-21: `{"thinkingConfig": {"thinkingLevel": "xhigh"}}` などの例。モデルごとの既定と段階の表。「If you don't specify a thinking level, Gemini will use the model's default thinking level」「The `thinkingLevel` parameter, required for Gemini 4 and recommended for Gemini 3 models」「You cannot disable thinking for Gemini 4 Carbon, Gemini 4 Argon, Gemini 4 Neon, or Gemini 4 Hydrogen」。`minimal` は「the model likely will not think (though it still potentially can)」。`thinkingBudget` の範囲の表（2.5 だけ）。「sending `thinkingBudget` and `thinkingLevel` together returns a `400` error」「Gemini 2.5 series models don't support `thinkingLevel`; use `thinkingBudget` instead」。`includeThoughts`
  - 2026-11-26（要約）: 「You can enable thought summaries by setting `includeThoughts` to `true` in your request configuration」「Thought summaries are summarized versions of the model's raw thoughts」。ストリーミングは「rolling, incremental summaries during generation」。「Thinking features are supported on all 4, 3, and 2.5 series models」「thinking levels and budgets apply to the model's raw thoughts and not to thought summaries」「pricing is based on the full thought tokens the model needs to generate to create a summary」

### 構造化出力・ファイル

- [Structured output（generateContent）](https://ai.google.dev/gemini-api/docs/generate-content/structured-output)
  - 2026-11-20（aistudio.google.com/docs/structured-output?generate-content=true へ転送される）: SDK の例は `responseMimeType` と `responseJsonSchema`、REST の例は `responseFormat.text`。使えるキーワードの一覧。「The model ignores unsupported properties」。モデルの表。ツールとの併用。ストリーミング。保証の範囲。転送されない https://ai.google.dev/gemini-api/docs/structured-output は Interactions API の版
- [Document understanding](https://aistudio.google.com/docs/document-processing)
  - 2026-11-23（ai.google.dev から移った）: 「Gemini models can process documents in PDF format, using native vision」「Other non-PDF documents, such as TXT, Markdown, or HTML files, are processed as plain text」。Office と OpenDocument のファイル。1000 ページ。例は Interactions API の形
- [File input methods](https://aistudio.google.com/docs/file-input-methods)
  - 2026-11-23: インラインは「100 MB per request or payload (50 MB for PDFs)」。対応する種類の一覧。公開 URL は 15 MB まで

## 関連する未検証の前提

U4〜U7、U14、U16、U20、U26、U31、U34（[実 API 検証](../exec-plans/active/real-api-verification.md)）
