# 各社 API の仕様メモ

アダプターを直すときに読む。各文書に、共通型との対応・ストリーミングの扱い・注意点・出典（一次情報）をまとめている。仕様を一次情報で確認したら、該当する文書の「出典」に URL と確認日を足す。

| 文書 | アダプター |
|---|---|
| [openai-responses.md](openai-responses.md) | `openai`（OpenAI Responses API） |
| [openai-chat-compatible.md](openai-chat-compatible.md) | `openai_chat`（Chat Completions と互換サーバー） |
| [anthropic-messages.md](anthropic-messages.md) | `anthropic`（Anthropic Messages API） |
| [gemini-generate-content.md](gemini-generate-content.md) | `gemini`（Gemini API の generateContent） |

## 横断の対応表

実装の該当箇所を探すときの索引。詳細と出典は各文書が正で、食い違ったら各文書に合わせる。

| 共通 | OpenAI Responses | Chat Completions 互換 | Anthropic Messages | Gemini generateContent |
|---|---|---|---|---|
| エンドポイント | `POST /v1/responses` | `POST {base}/chat/completions` | `POST /v1/messages` | `POST /v1beta/models/{model}:generateContent` |
| 認証ヘッダー | `Authorization: Bearer` | `Authorization: Bearer`（ローカルは不要） | `x-api-key` + `anthropic-version` | `x-goog-api-key` |
| system | トップレベル `instructions` | `role: system` | トップレベル `system` | `systemInstruction.parts` |
| max_tokens | `max_output_tokens` | `max_tokens`（OpenAI 本家は `max_completion_tokens`） | `max_tokens`（**必須**） | `generationConfig.maxOutputTokens` |
| stop | **非対応**（例外） | `stop` | `stop_sequences` | `generationConfig.stopSequences` |
| tool_choice を指定しないとき | 送らない（既定は `auto`） | 送らない（既定は `auto`。Ollama は非対応） | 送らない（既定は `auto`） | 送らない（既定は `AUTO`） |
| モデルのツール呼び出し | `function_call` アイテム（arguments は JSON 文字列） | `tool_calls[].function.arguments`（JSON 文字列） | `tool_use` ブロック（`input` はオブジェクト） | `functionCall` パート（`args` はオブジェクト） |
| ツール結果 | `function_call_output` | `role: tool` | user メッセージ内の `tool_result` | user の `functionResponse` |
| 保持が必要な推論情報 | `reasoning` アイテム | `reasoning_content`（DeepSeek。ツールを使う会話で必要） | `thinking` ブロック + `signature` | `thoughtSignature` |
| response_schema（構造化出力） | `text.format`（`json_schema`） | `response_format`（`json_schema`。DeepSeek は `json_object` だけ） | `output_config.format`（`json_schema`） | `generationConfig` の `responseMimeType` と `responseJsonSchema` |
| reasoning_effort（推論の強さ） | `reasoning.effort` | `reasoning_effort` | `output_config.effort`（`none` は `thinking: {"type": "disabled"}`、`minimal` は例外） | `generationConfig.thinkingConfig.thinkingLevel`（`none`・`max` は例外） |
| temperature | `temperature` | `temperature` | 既定の版（`2026-10-21`）では例外。`2023-06-01` なら `temperature` | `generationConfig.temperature`（3.6 Flash 以降は無視される） |
| ストリームの終わり | `response.completed` | `finish_reason` か `data: [DONE]` | `message_stop` | `finishReason` の入ったチャンク |

ストリームの終わりの合図が来ないまま接続が閉じたら、どのアダプターも `StreamError` にする（途中までを正常終了として返さない）。
