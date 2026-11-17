# Gemini API（generateContent）

- アダプター: `providers/gemini.py`（プリセット `gemini` / `google`）
- エンドポイント: `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`（ストリーミングは `:streamGenerateContent?alt=sse`）
- 認証: `x-goog-api-key: $GEMINI_API_KEY`（または `GOOGLE_API_KEY`）

## 共通型との対応

| 共通 | generateContent |
|---|---|
| system | `systemInstruction.parts` |
| user | `contents[]`（`role: "user"`）の `parts`（`text` / `inlineData`）。画像 URL は非対応で `UnsupportedFeatureError` |
| assistant（再送しないとき） | `role: "model"` の `text` と `functionCall`（`args` はオブジェクト） |
| ツール結果 | user の `functionResponse`（`name` が必須、`response` はオブジェクト）。結果が JSON オブジェクトの文字列ならそのまま、それ以外は `{"result": ...}`、エラーは `{"error": ...}` |
| 連続する同じ role | 1つの content にまとめる（並列呼び出しは FC1, FC2, FR1, FR2 の順） |
| max_tokens / temperature / stop | `generationConfig` の `maxOutputTokens` / `temperature` / `stopSequences` |
| ツール定義 | `tools[].functionDeclarations[]`。スキーマは `parameters` に入れる（`schema_field="parametersJsonSchema"` で変更できる、U4） |
| tool_choice | `toolConfig.functionCallingConfig.mode` の `AUTO` / `NONE` / `ANY`（特定のツールは `ANY` と `allowedFunctionNames`）。指定がなければ `toolConfig` を送らない（既定は `AUTO`） |

## レスポンス

- `candidates[0].content.parts` の `text`（`thought: true` は推論）と `functionCall` を読む。再送用に `parts` 全体を `ProviderState` に入れる（`thoughtSignature` を含む）
- `functionCall` に id がなければ仮 ID `switchllm_...` を振り、送り返すときは id を付けない（U6）
- stop_reason: `STOP` は `end`、`MAX_TOKENS` は `max_tokens`、`SAFETY` などフィルタ系は `content_filter`。プロンプト自体がブロックされると `candidates` が空で `promptFeedback.blockReason` が入るので `content_filter`
- usage: `promptTokenCount` と、`candidatesTokenCount + thoughtsTokenCount`（U14）。`promptTokenCount` はキャッシュした内容を含む。`totalTokenCount` は「prompt + tool-use prompt + thoughts + response candidates」なので、candidates と thoughts は別に数えている（candidates が thoughts を含まないとは明記していないので、E2E テストの `test_usage_breakdown` で total と内訳の和を比べる）

## ストリーミング

- SSE の各イベントは部分的なレスポンス。最後のチャンクに `finishReason` が入る。`finishReason` も `promptFeedback.blockReason` もないまま閉じたら `StreamError`（U16）
- 連続するテキストは結合するが、**署名付きのパート同士は結合しない**（公式ドキュメントの注意）
- 最後に非ストリーミングと同じ形の dict を組み立てて `parse_response` を再利用する

## 注意点

- Gemini 3 以降、関数呼び出しの往復で `thoughtSignature` を返さないと 4xx になる。並列呼び出しでは、署名は最初の functionCall にだけ付く
- 他プロバイダーの履歴を送るときは、最初の functionCall にダミー署名 `skip_thought_signature_validator` を付ける。公式は「最終手段で、性能が落ちる可能性がある」と記載している（`inject_dummy_signatures=False` で無効化、U5）
- Google は generateContent を「レガシー」とし、新規開発には Interactions API を勧めている（U7、[負債](../exec-plans/tech-debt-tracker.md)）

## 出典

- [Thought signatures](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures)
- [Function calling](https://ai.google.dev/gemini-api/docs/generate-content/function-calling)
- [OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)
- [API reference: FunctionCallingConfig](https://ai.google.dev/api/caching#FunctionCallingConfig)（2026-11-15 確認: mode は「If unspecified, the default value will be set to AUTO」）
- [API reference: generateContent](https://ai.google.dev/api/generate-content) の UsageMetadata（2026-11-17 確認。aistudio.google.com/api-reference/generate-content へ転送される: `thoughtsTokenCount` は「Number of tokens of thoughts for thinking models」、`totalTokenCount` は「prompt + tool-use prompt + thoughts + response candidates」。U14）
- [Models](https://ai.google.dev/gemini-api/docs/models) の「Latest」（2026-11-17 確認。aistudio.google.com/docs/models へ転送される: `gemini-flash-latest` のようなエイリアスは「Points to the latest release for a specific model variation」で、指す先を変える前に「A 2-week notice will be provided through email」。今どのモデルを指すかは書かれていない。U13）

## 関連する未検証の前提

U4〜U7、U14、U16（[実 API 検証](../exec-plans/active/real-api-verification.md)）
