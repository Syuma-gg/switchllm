# 引き継ぎメモ（2026-11-08 時点）

## 経緯

- 動機: AI プロバイダーごとにライブラリもリクエスト形式も違い、モデルを変えるたびにコード修正が必要になるのが面倒。クライアント側の1か所を変えれば AI を切り替えられるライブラリが欲しい。
- claude.ai のチャットで調査・設計・実装・テストまで行い、ここから Claude Code での開発に移行する。
- 同じ目的の OSS（LiteLLM、mozilla-ai の any-llm）は既に存在する。それでも自作する価値は「中身を理解する」「依存を httpx だけにする」「自作エージェントの LLM 層として使う」点にある。本番運用だけが目的なら LiteLLM の方が適切、という前提は共有済み。

## 現状

| 項目 | 状態 |
|---|---|
| 同期 `chat()` / `stream()` | 実装済み（4アダプター） |
| ツール呼び出し（並列含む）、`@tool` デコレーター、`run_tool_calls` | 実装済み |
| 生データ再送（署名・reasoning の保持） | 実装済み |
| 途中でのプロバイダー切り替え（履歴の変換） | 実装済み |
| 共通例外・自動リトライ（429 / 5xx / 接続エラー） | 実装済み |
| 画像入力 | 実装済み（Gemini は base64 のみ） |
| テスト | 37件すべて成功（**モックのみ**） |
| **実 API での動作確認** | **未実施** ← 最優先 |

## 次にやること（優先順）

1. **実 API の疎通確認**: `scripts/smoke_test.py` を各社のキーで実行し、失敗した箇所を直す（料金が発生するので、実行前にユーザーに確認）。下の「未検証の前提」表を上から潰す。
2. **git 管理を始める**: まだリポジトリになっていない。`git init` → `.gitignore` は用意済み。
3. **非同期クライアント**: `httpx.AsyncClient` 版の `AsyncClient`。`build_request` / `parse_response` / `parse_stream` は同期版と共有し、`_http.py` に async 版の送信関数を足すだけで済む設計になっている。
4. **フォールバック**: `Client(["anthropic:...", "openai:..."])` のように複数指定し、`RateLimitError` / `ServerError` で次のモデルに切り替える。履歴の変換は実装済みなので、主な作業はリトライ制御。
5. **推論強度の共通化**: `reasoning="low|medium|high"` のような共通パラメータ → OpenAI `reasoning.effort` / Anthropic thinking / Gemini thinkingConfig へのマッピング。各社とも世代で指定方法が変わるので、必ず最新ドキュメントで確認する。
6. **構造化出力の共通化**: OpenAI `text.format` / Anthropic structured outputs / Gemini `responseSchema`。
7. 余力があれば: PDF・ファイル入力、プロンプトキャッシュ制御（Anthropic `cache_control`）、Gemini Interactions API アダプター、Bedrock / Vertex / Azure 認証、料金計算、GitHub Actions で CI。

## 未検証の前提（実 API で確認すべきこと）

調査はしたが、実際のリクエストで確かめていない点。確認したら「結果」列を埋めること。

| # | 箇所 | 現在の実装 | 不確実な理由 | 結果 |
|---|---|---|---|---|
| 1 | OpenAI Responses | 同モデルへは output アイテム（id 付き）をそのまま input に戻す | 既定の `store`（保存あり）なら問題ないはず。`store: false` にすると id 参照が解決できずエラーになる可能性がある | |
| 2 | OpenAI Responses | assistant の過去発言を `{"role": "assistant", "content": "文字列"}` で送る | 文字列 content を assistant ロールで受け付けると理解しているが、実リクエスト未確認 | |
| 3 | OpenAI Responses | function ツールに `strict: false` を明示 | strict の既定値に依存しないための措置。true にする場合は全プロパティ required + `additionalProperties: false` が必要 | |
| 4 | Gemini | 関数スキーマを `parameters`（OpenAPI サブセット）で送る | `parametersJsonSchema` の方が JSON Schema をそのまま扱えるが、REST での挙動を一次情報で確認できなかった。オプション `schema_field` で切り替え可 | |
| 5 | Gemini | 他プロバイダーの履歴を送るとき、最初の functionCall にダミー署名 `skip_thought_signature_validator` を付ける | 公式は「最終手段、性能が落ちる」と記載。Gemini 2.5 など非 Gemini 3 系に付けて害がないかは未確認。オプション `inject_dummy_signatures=False` で無効化可 | |
| 6 | Gemini | functionCall に id がなければ仮 ID `unillm_...` を振り、functionResponse には id を付けない | Gemini 3 では id が常に返るという二次情報あり。古いモデルでの挙動は未確認 | |
| 7 | Gemini | generateContent を使用 | Google は generateContent を「レガシー」と表記し、新規開発には Interactions API を推奨している。当面は動くはずだが将来の移行候補 | |
| 8 | Anthropic | 別モデルへ切り替えると thinking ブロックを捨てる | thinking 有効のままツール往復の途中でモデルを変えると 400 になる可能性がある（同モデル内なら問題なし） | |
| 9 | Anthropic | `tool_choice: "none"` → `{"type": "none"}` | 対応済みと理解しているが未確認 | |
| 10 | OpenAI 互換 | ストリーミング時に `stream_options: {include_usage: true}` を送る | 拒否するサーバーがあれば `Client(..., stream_usage=False)` で無効化可 | |
| 11 | DeepSeek | `reasoning_content` は受け取るが送り返さない | 思考モードでツールを使うとき送り返しが必要になるかは未確認 | |
| 12 | プリセット | groq / deepseek / openrouter / xai / mistral / together / ollama / lmstudio の base_url | 第三者のまとめ（developersdigest.tech）で照合。各社公式で個別には未確認 | |
| 13 | サンプル | モデル ID `gpt-5.5` / `claude-sonnet-5-5` / `gemini-flash-latest` | モデル ID は頻繁に変わる。`gemini-flash-latest` は公式の latest エイリアス | |

## API 仕様メモ（調査結果の要約）

共通型 → 各社形式の対応表。実装の該当箇所を探すときの索引にも使える。

| 共通 | OpenAI Responses | Chat Completions 互換 | Anthropic Messages | Gemini generateContent |
|---|---|---|---|---|
| エンドポイント | `POST /v1/responses` | `POST {base}/chat/completions` | `POST /v1/messages` | `POST /v1beta/models/{model}:generateContent` |
| 認証ヘッダー | `Authorization: Bearer` | `Authorization: Bearer`（ローカルは不要） | `x-api-key` + `anthropic-version: 2023-06-01` | `x-goog-api-key` |
| system | トップレベル `instructions` | `role: system` | トップレベル `system`（複数は連結） | `systemInstruction.parts` |
| max_tokens | `max_output_tokens` | `max_tokens` | `max_tokens`（**必須**、既定 4096 を補う） | `generationConfig.maxOutputTokens` |
| stop | **非対応**（例外） | `stop` | `stop_sequences` | `generationConfig.stopSequences` |
| ツール定義 | `{type:function, name, description, parameters, strict}` | `{type:function, function:{name, description, parameters}}` | `{name, description, input_schema}` | `tools[].functionDeclarations[]` |
| tool_choice=required | `"required"` | `"required"` | `{type: any}` | `functionCallingConfig.mode: ANY` |
| モデルのツール呼び出し | `function_call` アイテム（`call_id`、arguments は JSON 文字列） | `tool_calls[].function.arguments`（JSON 文字列） | `tool_use` ブロック（`input` はオブジェクト） | `functionCall` パート（`id`, `args` はオブジェクト） |
| ツール結果 | `function_call_output`（`call_id`, `output`） | `role: tool` + `tool_call_id` | user メッセージ内の `tool_result` ブロック | user content 内の `functionResponse`（`name`, `id`, `response` はオブジェクト） |
| 保持が必要な推論情報 | `reasoning` アイテム | なし | `thinking` ブロック + `signature` | `thoughtSignature`（並列時は最初の呼び出しだけ） |
| ストリーム終端 | `response.completed` | `data: [DONE]` | `message_stop` | 接続終了 |

重要な一次情報:
- OpenAI: GPT-5.4 以降、Chat Completions はツール呼び出しを `reasoning_effort: none` 以外で受け付けない → 本家は Responses API を使う理由 — https://developers.openai.com/api/docs/guides/migrate-to-responses
- Gemini: Gemini 3 以降、関数呼び出しで thoughtSignature を返さないと 4xx。並列時は FC1(署名), FC2, FR1, FR2 の順 — https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures
- Anthropic: thinking + ツール併用時は thinking ブロックを無加工で返す — https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking
- Anthropic: ストリーミングのイベント種別、新イベントは無視せよとの方針 — https://docs.anthropic.com/en/api/messages-streaming

## 判断の記録

- **SDK を使わず HTTP 直叩き**: 形式差が見えること・モックでテストできることを優先。代償は API 変更への追従を自分でやること。
- **主要3社は各社の OpenAI 互換エンドポイントではなくネイティブ API**: Anthropic は互換レイヤーを本番向けではないとし、プロンプトキャッシュ非対応・thinking 非返却などの制限がある。
- **生データ再送は「同プロバイダー かつ 同モデル」に限定**: 署名がモデルをまたいで有効かは保証がないため、安全側に倒した（未検証 #8 参照）。
- **出力形式は OpenAI 互換ではなく独自の共通型**: 署名などのプロバイダー固有情報を `ProviderState` として持たせたかったため。LiteLLM / any-llm は OpenAI 形式に寄せる方針で、ここが最大の違い。
