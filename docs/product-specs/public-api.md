# 公開 API

`switchllm` 直下の `__all__` にある名前だけが公開 API。サブモジュール（`switchllm.providers`、`switchllm._http` など）は内部実装で、予告なく変わる（[判断](../design-docs/minimal-public-api.md)）。

この文書は、公開 API が「何をするか」の正本。なぜそうしたかは、各行のリンク先の設計判断にある。下の一覧と `switchllm.__all__`、プリセット表と `PRESETS`、オプション表と各アダプターの `option_names` が一致することを `tests/test_docs.py` が検査する。

## 一覧

<!-- public-api:start -->
| 名前 | 種類 | 説明 |
|---|---|---|
| `Client` | クラス | 同期の入口。`chat()` / `stream()` / `with_model()` / `close()` |
| `AsyncClient` | クラス | 非同期の入口。引数とメソッドの名前は `Client` と同じ（下の「非同期のクライアント」） |
| `parse_model` | 関数 | `"provider:model"` を最初の `:` で分解する |
| `MODEL_ENV` | 定数 | モデル文字列を読む環境変数の名前（`"SWITCHLLM_MODEL"`） |
| `register_provider` | 関数 | OpenAI 互換サーバーなどを名前付きで登録する |
| `Message` | クラス | 会話の1メッセージ。`Message.user()` / `system()` / `assistant()` / `tool_result()`。`to_dict()` / `from_dict()` で、推論情報（`provider_state`）ごと JSON に保存・復元できる（[判断](../design-docs/history-serialization.md)） |
| `TextPart` | クラス | テキストのパーツ |
| `ImagePart` | クラス | 画像のパーツ（base64 か URL）。`ImagePart.from_file()`（下の「画像とファイル」） |
| `FilePart` | クラス | ファイル（PDF など）のパーツ。`data`（base64）・`media_type`・`filename`。`FilePart.from_file()`（下の「画像とファイル」） |
| `ToolCall` | クラス | モデルが要求したツール呼び出し |
| `Response` | クラス | `chat()` の結果。構造化出力を指定したら、読んだ JSON が `parsed` に入る（下の「正規化した値の意味」） |
| `Usage` | クラス | トークン使用量 |
| `StreamEvent` | クラス | `stream()` のイベント（`text` / `thinking` / `done`） |
| `Tool` | クラス | ツール定義（名前・説明・JSON Schema・関数） |
| `tool` | デコレーター | 関数から `Tool` を作る。対応外の型には `parameters=` |
| `run_tool_calls` | 関数 | ツール呼び出しをまとめて実行し、結果のメッセージを返す。`async def` のツールは実行せず、`arun_tool_calls` を使うよう `is_error=True` の結果で知らせる |
| `arun_tool_calls` | 関数 | `run_tool_calls` の非同期版（`await` する）。呼び出しを並行に実行し、結果は呼び出しの順に返す |
| `SwitchLLMError` | 例外 | このライブラリの例外の基底クラス |
| `ConfigurationError` | 例外 | 設定の誤り（モデル文字列、APIキー未設定など） |
| `UnsupportedFeatureError` | 例外 | 選んだプロバイダーが対応していない指定 |
| `DroppedParameterWarning` | 警告 | 指定を送らずに捨てた（`UserWarning` を継承。今は Anthropic の `temperature` だけ。`warnings.filterwarnings("error", ...)` で例外にできる。[判断](../design-docs/dropped-parameter-warning.md)） |
| `APIConnectionError` | 例外 | ネットワーク到達不可、タイムアウト |
| `APIError` | 例外 | プロバイダーがエラーを返した（`message` / `provider` / `status_code` / `body`） |
| `BadRequestError` | 例外 | 400 系。送った引数がモデルに断られたとみられるときは、`str(e)` に `Hint (<引数>): ...` の行が付く（`message` は API の文言のまま。[判断](../design-docs/error-hints.md)） |
| `AuthenticationError` | 例外 | 401 / 403 |
| `NotFoundError` | 例外 | 404 |
| `RateLimitError` | 例外 | 429 |
| `ServerError` | 例外 | 5xx（Anthropic の 529 を含む） |
| `StreamError` | 例外 | ストリーミングの途中で届いたエラーイベント |
<!-- public-api:end -->

### 非同期のクライアント

[判断](../design-docs/async-client.md)

- `await client.chat(...)`、`async for ev in client.stream(...)`、`client.with_model(...)`
- 閉じるのは `await client.aclose()` か `async with`
- `http_client=` には `httpx.AsyncClient` を渡す
- `arun_tool_calls` は、`async def` のツールを await し、普通の関数はそのまま呼ぶ

### 画像とファイル

[判断](../design-docs/file-input.md)

- `ImagePart` は、`url` があれば `url` を、なければ `data` と `media_type` を送る。どちらもなければ、送る前に `TypeError`
  - URL の画像は、各社の API が取りに行く（ライブラリは取りに行かない）。Gemini には `fileData.fileUri` で送り、`media_type` が既定のままなら URL の拡張子から種類を推す。Ollama は送る前に `UnsupportedFeatureError`（[判断](../design-docs/provider-capabilities.md)）
- `FilePart` が受け付ける種類は、アダプターで違う
  - OpenAI Responses と Gemini は、種類を確かめずに送る
  - Chat Completions 互換は PDF だけ。Anthropic は PDF とテキスト
  - ほかは、送る前に `UnsupportedFeatureError`
- `FilePart` では、画像・音声・動画は送らない
- 画像とファイルは、user のメッセージにだけ入れられる

## 正規化した値の意味

プロバイダーを切り替えても同じ意味になるよう揃えた値。0.x の間も意味は変えない。

| 値 | 定義 |
|---|---|
| `Response.stop_reason` | `end` / `tool_use` / `max_tokens` / `content_filter` / `other` のいずれか。ツール呼び出しがあれば常に `tool_use` |
| `Usage.input_tokens` | 処理した入力トークンの総数（キャッシュの読み書きを含む）。[各社との対応](../design-docs/usage-normalization.md) |
| `Usage.output_tokens` | 出力トークンの総数（推論トークンを含む） |
| `Usage.details` | プロバイダーが返した生の内訳 |
| `ToolCall.arguments` | 常に dict。JSON として壊れていたら `{}` にし、原文を `raw_arguments` に残す |
| `Response.parsed` | `response_schema=` を指定し、`stop_reason` が `end` で、本文が JSON として読めたときだけ、読んだ値。それ以外（拒否、上限で止まった、ツールを呼んだ、指定なし）は `None`。スキーマに合うかは、ライブラリでは確かめない（各社が出力を縛る。[判断](../design-docs/structured-output.md)） |
| `stream()` の `done` | 最後に1回だけ届く。`response` は非ストリーミングの `chat()` と同じ内容。終わりの合図が来ないまま接続が閉じたら、`done` ではなく `StreamError` |
| `Response.message.provider_state` | プロバイダーの生データ。利用者は触らず、メッセージごと履歴に積む。保存するときは `to_dict()` に含まれる |

## 推論の指定

`chat()` / `stream()` の引数。0.x の間も意味は変えない。

### `reasoning_effort=`（推論の強さ）

[判断](../design-docs/reasoning-effort.md)

- `none` / `minimal` / `low` / `medium` / `high` / `xhigh` / `max` のどれか。ほかの文字列は `ConfigurationError`、文字列でなければ `TypeError`
- 各社の指定に、名前を変えずに入れる。考える量まではそろえない
- 各社の API に名前のない段階（Anthropic の `minimal`、Gemini の `none`・`max`）は、送る前に `UnsupportedFeatureError`
- Anthropic の `none` は thinking を切る
- モデルが受け付けない段階は、API のエラーになる

### `reasoning_summary=`（推論の要約）

[判断](../design-docs/reasoning-summary.md)

- `True` / `False`（既定）だけで、ほかは `TypeError`
- `True` なら、各社の「要約を返す」指定を送る（Chat Completions 互換には何も足さない）
- 返った要約（DeepSeek などは推論の本文）を、`Response.thinking` と `thinking` イベントに入れる。返すかはモデルが決めるので、`None` のこともある
- `reasoning_effort="none"` と一緒なら、送る前に `ConfigurationError`
- OpenAI 本家の Chat Completions は、送る前に `UnsupportedFeatureError`

## プリセット

モデル文字列の `provider:` 部分に書ける名前。base_url を公式ドキュメントで確認したものだけを置く（[判断](../design-docs/verified-presets.md)）。

<!-- presets:start -->
| 名前 | 形式 | 接続先 | APIキーの環境変数 |
|---|---|---|---|
| `openai` | Responses API | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `openai-chat` | Chat Completions | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `deepseek` | Chat Completions | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` |
| `anthropic` | Messages API | `https://api.anthropic.com` | `ANTHROPIC_API_KEY` |
| `gemini` | generateContent | `https://generativelanguage.googleapis.com/v1beta` | `GEMINI_API_KEY` または `GOOGLE_API_KEY` |
| `google` | `gemini` の別名 | 同上 | 同上 |
| `ollama` | Chat Completions | `http://localhost:11434/v1` | 不要 |
| `lmstudio` | Chat Completions | `http://localhost:1234/v1` | 不要 |
<!-- presets:end -->

### プリセットごとに送れるもの

載せ替える前に、送る前に止まる指定が分かるようにする（[判断](../design-docs/provider-capabilities.md)）。`google` は `gemini` と同じ。表とライブラリの振る舞いが一致することを `tests/test_support_table.py` が全プリセットで呼んで確かめる。

- ○: 送る。モデルが受け付けなければ API のエラーになり、`str(e)` に案内が付くことがある（[判断](../design-docs/error-hints.md)）
- ×: 送る前に `UnsupportedFeatureError`。メッセージで代わりの書き方を案内する
- △: 送らずに捨て、`DroppedParameterWarning` を出す（[判断](../design-docs/dropped-parameter-warning.md)）

<!-- support:start -->
| 指定 | `openai` | `openai-chat` | `anthropic` | `gemini` | `deepseek` | `ollama` | `lmstudio` |
|---|---|---|---|---|---|---|---|
| `stop` | × | ○ | ○ | ○ | ○ | ○ | ○ |
| 画像（base64） | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| 画像の URL | ○ | ○ | ○ | ○ | ○ | × | ○ |
| ファイル（PDF） | ○ | ○ | ○ | ○ | × | × | × |
| ファイル（テキスト） | ○ | × | ○ | ○ | × | × | × |
| `response_schema` | ○ | ○ | ○ | ○ | × | ○ | ○ |
| `reasoning_effort="none"` | ○ | ○ | ○ | × | ○ | ○ | ○ |
| `reasoning_effort="minimal"` | ○ | ○ | × | ○ | ○ | ○ | ○ |
| `reasoning_effort="max"` | ○ | ○ | ○ | × | ○ | ○ | ○ |
| `reasoning_summary=True` | ○ | × | ○ | ○ | ○ | ○ | ○ |
| `tool_choice="none"` | ○ | ○ | ○ | ○ | ○ | × | ○ |
| 強制する `tool_choice` | ○ | ○ | ○ | ○ | ○ | × | ○ |
| `temperature` | ○ | ○ | △ | ○ | ○ | ○ | ○ |
<!-- support:end -->

- 会話の途中で載せ替えるときも同じで、履歴に×のもの（PDF や画像の URL）があると、その接続先へは送れない
- 互換サーバー（`register_provider()` や `base_url=`）は、オプション（`json_schema`・`file_input`・`tool_choice`・`image_url`）で決まる。既定ではどれも送る

## プロバイダー固有の指定と切り替え

[判断](../design-docs/provider-specific-params.md)

| 指定 | 意味 |
|---|---|
| `chat(extra=...)` / `stream(extra=...)` | どのプロバイダーにも、リクエスト本体にそのままマージする |
| `chat(extra_by_provider=...)` / `stream(extra_by_provider=...)` | `{"anthropic": {...}, "openai": {...}}` の形。モデル文字列の `provider:` 部分と同じ名前の分だけを `extra` に深くマージする（同じキーはこちらが優先）。知らない名前は `ConfigurationError` |
| `Client(options_by_provider=...)` / `AsyncClient(options_by_provider=...)` | `{"anthropic": {...}, "gemini": {...}}` の形。下の「プロバイダー別のオプション」を、接続先ごとに並べて書く。モデル文字列の `provider:` 部分と同じ名前の分だけを `**options` に深くマージする（同じ名前はこちらが優先）。選ばれなかった分も、`Client` を作るときにオプション名と中身を検査する。知らない名前は `ConfigurationError`、値が dict でなければ `TypeError` |
| `Client.with_model(model, **overrides)` | 接続を共有したまま、別モデルのクライアントを作る。`AsyncClient.with_model()` も同じで、`AsyncClient` を返す |

`with_model()` が引き継ぐもの:

- 同じプロバイダーなら、api_key / base_url / オプション
- 別のプロバイダーへは、timeout / max_retries / retry_backoff と `options_by_provider`（切り替え先の分しか使わない）だけ

### プロバイダー別のオプション

`Client(..., **options)`・`AsyncClient(..., **options)` と `register_provider(..., **options)` で渡す。アダプターごとに下の名前だけを受け付け、それ以外は `ConfigurationError` にする。接続先によって違うオプションを1つのコードで渡すときは、`options_by_provider=` に並べる。

<!-- options:start -->
| 名前 | アダプター | 意味 |
|---|---|---|
| `headers` | `openai`、`openai_chat`、`anthropic`、`gemini` | リクエストに足すヘッダー（`str` から `str` か `None` への dict。下の一覧） |
| `max_tokens_field` | `openai_chat` | max_tokens を送るフィールド名。既定は、接続先が OpenAI 本家なら `max_completion_tokens`、それ以外は `max_tokens` |
| `stream_usage` | `openai_chat` | `False` にすると、ストリーミングで `stream_options.include_usage` を送らない（既定 `True`、U10） |
| `json_schema` | `openai_chat` | `False` にすると、`response_schema=` を送る前に `UnsupportedFeatureError` にする。JSON Schema を受け付けない接続先のため（既定 `True`、`deepseek` のプリセットは `False`） |
| `tool_choice` | `openai_chat` | `False` にすると、`tool_choice` の `"none"` と強制を送る前に `UnsupportedFeatureError` にし、`"auto"` は送らない。`tool_choice` に対応しない接続先のため（既定 `True`、`ollama` のプリセットは `False`。[判断](../design-docs/provider-capabilities.md)） |
| `image_url` | `openai_chat` | `False` にすると、URL の画像を送る前に `UnsupportedFeatureError` にする。画像を base64 でしか受け付けない接続先のため（既定 `True`、`ollama` のプリセットは `False`。[判断](../design-docs/provider-capabilities.md)） |
| `file_input` | `openai_chat` | `False` にすると、`FilePart` を送る前に `UnsupportedFeatureError` にする。ファイルを受け付けない接続先のため（既定 `True`、`deepseek`・`ollama`・`lmstudio` のプリセットは `False`。[判断](../design-docs/file-input.md)） |
| `default_max_tokens` | `anthropic` | max_tokens を指定しなかったときの値（既定 64000。Anthropic は必須。[判断](../design-docs/anthropic-max-tokens.md)） |
| `anthropic_version` | `anthropic` | `anthropic-version` ヘッダー（既定 `2026-10-21`）。`2026-10-21` 以降の版では、`temperature=` を送らずに捨て、`DroppedParameterWarning` を出す（[判断](../design-docs/dropped-parameter-warning.md)） |
| `beta` | `anthropic` | `anthropic-beta` ヘッダーにする名前のリスト |
| `schema_field` | `gemini` | 関数スキーマを入れるフィールド。既定 `parameters`、`parametersJsonSchema` も選べる（U4） |
| `inject_dummy_signatures` | `gemini` | `False` にすると、ほかのプロバイダーの履歴にダミー署名を付けない（既定 `True`、U5） |
<!-- options:end -->

`headers` の扱い（[判断](../design-docs/custom-headers.md)）:

- ライブラリが付けるヘッダー（認証の `Authorization`・`x-api-key`・`x-goog-api-key` を含む）と同じ名前なら、大文字と小文字を区別せずに比べて、こちらの値で置き換える
- 値が `None` なら、ライブラリが付けるそのヘッダーを送らない（httpx が自分で付けるヘッダーは止められない）
- 認証のヘッダーを渡せば（`None` でも）、APIキーがなくてもよい
- Anthropic の `anthropic-version`・`anthropic-beta` は、`anthropic_version`・`beta` で渡す。`headers` で渡すと `ConfigurationError`

## 互換性の方針

- 1.0 までは、マイナーバージョン（0.1 → 0.2）で破壊的変更を入れることがある。変更点は CHANGELOG に書く
- 上の「正規化した値の意味」と「推論の指定」は、0.x の間も変えない
