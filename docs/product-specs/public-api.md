# 公開 API

`switchllm` 直下の `__all__` にある名前だけが公開 API。サブモジュール（`switchllm.providers`、`switchllm._http` など）は内部実装で、予告なく変わる（[判断](../design-docs/minimal-public-api.md)）。

下の一覧と `switchllm.__all__`、プリセット表と `PRESETS` が一致することを `tests/test_docs.py` が検査する。

## 一覧

<!-- public-api:start -->
| 名前 | 種類 | 説明 |
|---|---|---|
| `Client` | クラス | 唯一の入口。`chat()` / `stream()` / `with_model()` / `close()` |
| `parse_model` | 関数 | `"provider:model"` を最初の `:` で分解する |
| `MODEL_ENV` | 定数 | モデル文字列を読む環境変数の名前（`"SWITCHLLM_MODEL"`） |
| `register_provider` | 関数 | OpenAI 互換サーバーなどを名前付きで登録する |
| `Message` | クラス | 会話の1メッセージ。`Message.user()` / `system()` / `assistant()` / `tool_result()` |
| `TextPart` | クラス | テキストのパーツ |
| `ImagePart` | クラス | 画像のパーツ（base64 か URL）。`ImagePart.from_file()` |
| `ToolCall` | クラス | モデルが要求したツール呼び出し |
| `Response` | クラス | `chat()` の結果 |
| `Usage` | クラス | トークン使用量 |
| `StreamEvent` | クラス | `stream()` のイベント（`text` / `thinking` / `done`） |
| `Tool` | クラス | ツール定義（名前・説明・JSON Schema・関数） |
| `tool` | デコレーター | 関数から `Tool` を作る。対応外の型には `parameters=` |
| `run_tool_calls` | 関数 | ツール呼び出しをまとめて実行し、結果のメッセージを返す |
| `SwitchLLMError` | 例外 | このライブラリの例外の基底クラス |
| `ConfigurationError` | 例外 | 設定の誤り（モデル文字列、APIキー未設定など） |
| `UnsupportedFeatureError` | 例外 | 選んだプロバイダーが対応していない指定 |
| `APIConnectionError` | 例外 | ネットワーク到達不可、タイムアウト |
| `APIError` | 例外 | プロバイダーがエラーを返した（`provider` / `status_code` / `body`） |
| `BadRequestError` | 例外 | 400 系 |
| `AuthenticationError` | 例外 | 401 / 403 |
| `NotFoundError` | 例外 | 404 |
| `RateLimitError` | 例外 | 429 |
| `ServerError` | 例外 | 5xx（Anthropic の 529 を含む） |
| `StreamError` | 例外 | ストリーミングの途中で届いたエラーイベント |
<!-- public-api:end -->

## 正規化した値の意味

プロバイダーを切り替えても同じ意味になるよう揃えた値。0.x の間も意味は変えない。

| 値 | 定義 |
|---|---|
| `Response.stop_reason` | `end` / `tool_use` / `max_tokens` / `content_filter` / `other` のいずれか。ツール呼び出しがあれば常に `tool_use` |
| `Usage.input_tokens` | 処理した入力トークンの総数（キャッシュの読み書きを含む）。[各社との対応](../design-docs/usage-normalization.md) |
| `Usage.output_tokens` | 出力トークンの総数（推論トークンを含む） |
| `Usage.details` | プロバイダーが返した生の内訳 |
| `ToolCall.arguments` | 常に dict。JSON として壊れていたら `{}` にし、原文を `raw_arguments` に残す |
| `stream()` の `done` | 最後に1回だけ届く。`response` は非ストリーミングの `chat()` と同じ内容 |
| `Response.message.provider_state` | プロバイダーの生データ。利用者は触らず、メッセージごと履歴に積む |

## プリセット

モデル文字列の `provider:` 部分に書ける名前。base_url を公式ドキュメントで確認したものだけを置く（[判断](../design-docs/verified-presets.md)）。

<!-- presets:start -->
| 名前 | 形式 | 接続先 | APIキーの環境変数 |
|---|---|---|---|
| `openai` | Responses API | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `openai-chat` | Chat Completions | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `anthropic` | Messages API | `https://api.anthropic.com` | `ANTHROPIC_API_KEY` |
| `gemini` | generateContent | `https://generativelanguage.googleapis.com/v1beta` | `GEMINI_API_KEY` または `GOOGLE_API_KEY` |
| `google` | `gemini` の別名 | 同上 | 同上 |
| `ollama` | Chat Completions | `http://localhost:11434/v1` | 不要 |
| `lmstudio` | Chat Completions | `http://localhost:1234/v1` | 不要 |
<!-- presets:end -->

## 互換性の方針

- 1.0 までは、マイナーバージョン（0.1 → 0.2）で破壊的変更を入れることがある。変更点は CHANGELOG に書く（[v0.1 公開](../exec-plans/active/v0.1-release.md) で作成）
- 上の「正規化した値の意味」は、0.x の間も変えない
