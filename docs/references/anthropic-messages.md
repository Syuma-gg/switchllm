# Anthropic Messages API

- アダプター: `providers/anthropic.py`（プリセット `anthropic`）
- エンドポイント: `POST https://api.anthropic.com/v1/messages`
- 認証: `x-api-key: $ANTHROPIC_API_KEY` と `anthropic-version: 2026-10-21`
  - 版は `anthropic_version=` で変えられる（下の「API の版」）
  - beta の機能は、`beta=[...]` が `anthropic-beta` ヘッダーになる
  - `x-api-key` は `headers=` で置き換えられる。`anthropic-version` と `anthropic-beta` は `headers=` では渡せず、専用のオプションを使う（[判断](../design-docs/custom-headers.md)）

## 共通型との対応

| 共通 | Messages API |
|---|---|
| system | トップレベルの `system`（system メッセージが複数あれば空行で連結） |
| user | `content` ブロック（`text` / `image` / `document`）。空の text ブロックは拒否されるので送らない |
| assistant（同じプロバイダーキーのとき） | 受け取った `content` ブロックをそのまま送る。モデルが違っても送る（下の「モデルの切り替え」） |
| assistant（再送しないとき） | `text` と `tool_use`（`input` はオブジェクト） |
| ツール結果 | user メッセージの中の `tool_result`（`tool_use_id`。エラーは `is_error: true`） |
| 続く同じ role | 1つのメッセージにまとめる（並列のツールの結果は1つの user メッセージに入れる） |
| max_tokens | `max_tokens`。**必須**で、指定がなければ 64000（`default_max_tokens=` で変えられる。下の「出力の上限」と[判断](../design-docs/anthropic-max-tokens.md)） |
| stop | `stop_sequences` |
| temperature | 既定の版（`2026-10-21`）では、送らずに捨てて `DroppedParameterWarning`（[判断](../design-docs/dropped-parameter-warning.md)）。`anthropic_version="2023-06-01"` なら `temperature`（下の「API の版」） |
| reasoning_effort | `output_config.effort`。`none` は `thinking: {"type": "disabled"}`、`minimal` は `UnsupportedFeatureError`（下の「推論の強さ」） |
| ツール定義 | `{"name", "description", "input_schema"}` |
| tool_choice | `{"type": "auto"}` / `{"type": "none"}` / `{"type": "any"}` / `{"type": "tool", "name"}`。指定がなければ送らない（ツールがあれば既定は `auto`） |
| 画像 | `{"type": "image", "source": {"type": "base64", ...}}` か `{"type": "url", "url"}`。オプション `image_url=False` なら URL は送る前に止める（`bedrock` のプリセット。[クラウドの経路](cloud-endpoints.md)） |
| ファイル | 下の「ファイルの入力」 |

ファイルの送り方:

- PDF: `{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data"}}`
- テキスト（`text/*`・`application/json`・`application/xml`）: base64 を UTF-8 に戻し、`{"type": "text", "media_type": "text/plain", "data"}` の document
- ほかの種類: 送る前に `UnsupportedFeatureError`

## レスポンス

- `content` ブロックの `text` / `thinking` / `tool_use` を読む。再送のために、`content` 全体を `ProviderState` に入れる（thinking の `signature` を含む）
- stop_reason:
  - `end_turn`・`stop_sequence` → `end`
  - `tool_use` → `tool_use`
  - `max_tokens`・`model_context_window_exceeded` → `max_tokens`
  - `refusal` → `content_filter`
- usage:
  - `input_tokens` はキャッシュ分を含まないので、`cache_creation_input_tokens` と `cache_read_input_tokens` を足す（U14。文書で確認済み）
  - `output_tokens` は thinking の分を含む。thinking の本文を返さない設定でも、生成した分すべてが出力として課金される

## ストリーミング

| イベント | 扱い |
|---|---|
| `message_start` | メッセージの骨組み |
| `content_block_start` / `content_block_delta` / `content_block_stop` | ブロックを組み立てる（下の一覧） |
| `message_delta` | `stop_reason` と usage を更新 |
| `message_stop` | 終了して `done`。届かないまま閉じたら `StreamError` |
| `error` | `StreamError` |
| `ping`・その他 | 無視（Anthropic は、新しいイベントを足すことがあると明言している） |

ブロックの組み立て:

- `text_delta` は `text`、`thinking_delta` は `thinking`
- `input_json_delta` は連結して、最後に JSON として読む。読めなければ `arguments={}` にし、原文を `raw_arguments` に残す（max_tokens で途中で切れたときなど）
- `signature_delta` は連結する

## モデルの切り替え

Anthropic のモデルどうしで切り替えるときも、thinking ブロックを捨てずに、受け取ったまま送る（U8。[判断](../design-docs/provider-state-resend.md)）。

- Anthropic は「切り替えるときに thinking ブロックを取り除くな」としている
  - 新しいモデルが読めないブロック（別のモデルや別のアカウントのもの）は、API がそのリクエストでは捨てる。エラーにもせず、料金もかけない
  - 読めるモデルには届き、推論を引き継げる
- 捨てられたブロックは、会話の変更とは数えない。元のモデルに戻しても、両方のモデルのブロックが有効なまま
- どの組み合わせで読めるかは、モデルによる（例: Opus 5.5 から Fable 5.1 へは引き継ぐが、Fable 5.1 から Opus 5.5 へは捨てる）。一覧は出典の「Which model switches keep the reasoning」にある。増え続けるので、ここには写さない
- Sonnet 5.5・Haiku 5.5・Fable 5.2・Mythos 5.2 の thinking ブロックは、作ったアカウント（か、つながったアカウント）でしか使えない。ほかのアカウントでは、API がエラーにせずに捨てる
- 再送するのは、同じプロバイダーキーのときだけ。`register_provider()` で同じ `anthropic` 形式を別の名前で足した接続先には送らない
- 切り替え先のモデルで 400 になるのは、ブロックではなく設定の違いによる。switchllm は、利用者の指定と違う thinking の設定を送らない
  - preserved thinking に対応するモデルは、`thinking: {"type": "enabled"}` を 400 にする
  - Opus 5.5 などは、`reasoning_effort="none"` で送る `{"type": "disabled"}` を 400 にする

### 前置きの検査（prefix check）

preserved thinking に対応するモデル（Fable 5.2・Mythos 5.2・Fable 5.1・Mythos 5.1・Opus 5.5・Sonnet 5.5・Haiku 5.5）は、thinking ブロックを送り返すと、それより前の内容（system・tools・それより前のメッセージ）が、ブロックを作ったときと同じかを確かめる。違えば、モデルによって 400 になるか、そのまま読まれる。

- 1語でも変わったら、違うと数える
- thinking ブロックのうち、間のものだけを抜いたり動かしたりするのも、違うと数える（古いもの・新しいもの・全部を抜くのはよい）
- モデル・effort・`max_tokens`・`tool_choice` などのパラメータ、JSON の書式やキーの順は比べない
- 400 の文言は ``Invalid `signature` in `thinking` block. The block is bound to a different conversation.``
- `thinking.block_binding.prefix_mismatch_behavior` を `"drop_block"` にすると、エラーにせずにブロックを捨てる（beta ヘッダー `thinking-binding-controls-2026-08-01` が要る）
- switchllm は、同じ履歴からは毎回同じリクエストを組み立てる（system はトップレベルにまとめ、続く同じ role のメッセージは1つにまとめる）
  - `res.message` を加工せずに履歴に積めば、前置きは変わらない
  - 履歴の途中を書き換えたり、`tools` を変えたりすると、この検査に当たりうる

## API の版

既定は `2026-10-21`（2026-11-21 に `2023-06-01` から上げた。[判断](../design-docs/anthropic-api-version.md)）。`anthropic_version=` で変えられる。以下は 2026-11-21 に文書で確かめた内容。

- 公式は「Anthropic recommends using the latest API version whenever possible」とする。`2023-06-01` の提供を終える日は書かれていない
- `2026-10-21` の、Messages API の破壊的な変更は3つ
  - `temperature`・`top_p`・`top_k` を送ると、どのモデルでも、既定の値でも 400
  - assistant メッセージに `document` ブロックを直接置くと 400
  - user メッセージの `text` ブロックに、空でない `citations` を付けると 400
- switchllm が組み立てるリクエストのうち、当たるのは `temperature` だけ
  - 版が `2026-10-21` 以降なら、`temperature=` を送る前に `UnsupportedFeatureError` にする
  - `anthropic_version="2023-06-01"` なら、今までどおり送る
  - assistant メッセージの再送（受け取った `content` をそのまま送る）は、assistant の citations を禁じていないので当たらない
- 応答の形・ストリーミングのイベント・stop_reason の変更は書かれていない
- キーは「The Claude API accepts a key in either header」で、`x-api-key` のままでよい（SDK は `Authorization: Bearer` に変えた）
- 古い版でも、今のモデルは `temperature` を自由には使えない
  - Fable 5.2・Mythos 5.2・Fable 5.1・Mythos 5.1・Fable 5・Mythos 5・Mythos Preview・Opus 5.5・Opus 5・Opus 4.8・Opus 4.7・Sonnet 5.5・Sonnet 5・Haiku 5.5 は、既定でない `temperature`・`top_p`・`top_k` を、thinking の有無にかかわらず 400 にする
  - `top_p` の既定は 0.99 なので、`1` も 400。`temperature` と `top_p` を一緒に送るのも、既定の値でも 400
  - それより古いモデルは、`2026-10-21` より前の版なら、thinking が有効なときだけ制限がある（`temperature` と `top_k` は thinking と一緒に使えず、`top_p` は 0.95〜1）

## 推論の強さ

- `reasoning_effort=` は `output_config.effort` で送る。`none` は `thinking: {"type": "disabled"}` にし、`minimal` は送る前に `UnsupportedFeatureError` にする（[判断](../design-docs/reasoning-effort.md)）
- `reasoning_summary=True` は `thinking: {"type": "adaptive", "display": "summarized"}` で送る（[判断](../design-docs/reasoning-summary.md)。U33）

以下は 2026-11-21 に文書で確かめた形式（`display` は 2026-11-26）。

- 指定: `output_config: {"effort": "medium"}`
  - 段階は `low`・`medium`・`high`・`xhigh`・`max` の5つで、`none` と `minimal` はない
  - `response_schema=` の `output_config.format` と同じオブジェクトに並ぶ
- 既定: 多くのモデルは `high`。Opus 5.5 と Haiku 5.5 は `medium`。既定と同じ段階を送るのは、送らないのと同じ
- 対応するモデル: Claude 5 系すべて（Fable・Mythos・Opus・Sonnet・Haiku）、Opus 4.8・4.7・4.6・Sonnet 4.6・Opus 4.5
  - Haiku 4.5 と Sonnet 4.5 は一覧にない。対応しないモデルに送ったときに、400 か無視かは書かれていない（U24）
- 段階ごと:
  - `max` は、上の一覧のうち Opus 4.5 以外（Mythos Preview・Opus 4.6・Sonnet 4.6 を含む）
  - `xhigh` は、そこから Mythos Preview・Opus 4.6・Sonnet 4.6 を除いたもの。「Not every model that supports `max` supports `xhigh`」
- 効く範囲:
  - thinking だけでなく、本文とツール呼び出しにも効く（「The effort parameter affects all tokens in the response」）
  - thinking がなくても効く（「it works with or without thinking」）
  - 厳密な予算ではない（「Effort is a behavioral signal, not a strict token budget」）
  - `max_tokens` は thinking と本文を合わせた上限で、`high` 以上では大きく取ることを勧めている
- 公式の勧め: 「Where adaptive thinking is available, effort is the recommended way to control thinking depth」
- 段階を会話の途中で変えてよい。前置きの検査は effort を比べない（上の「前置きの検査」）。ただし、effort や thinking の設定を変えると、プロンプトキャッシュが効かなくなる

### thinking の設定（`thinking.type`）

- 値: `adaptive`（Claude が、考えるかと量を決める）・`enabled`（`budget_tokens` を 1024 以上で `max_tokens` 未満）・`disabled`・`between_tools`（Sonnet 5.5 だけ）
- `thinking` を送らないとき:
  - Claude 5 系と Mythos Preview は、adaptive で考える
  - Opus 4.8・4.7・4.6・Sonnet 4.6 は考えない（`{"type": "adaptive"}` を送ると考える）
  - 4.5 系も考えない
- 400 になる組み合わせ:
  - `enabled` は、Claude 5 系・Opus 4.8・4.7 で 400
  - `adaptive` は、4.5 系で 400
- `disabled`:
  - Fable・Mythos の 5 系すべて、Mythos Preview、Opus 5.5 は 400（推論を切れない）
  - Opus 5 と Haiku 5.5 は、effort が `high` 以下なら受け付け、`xhigh`・`max` では 400
  - Sonnet 5.5 は 400 で、代わりに `between_tools`
  - Sonnet 5・Opus 4.8・4.7・4.6・Sonnet 4.6・4.5 系は受け付ける
  - Models API の `capabilities.thinking.types.disabled.supported` で確かめられる
  - `disabled` にしたリクエストで、前のターンの thinking ブロックを送り返してよいかは書かれていない。ツールの往復の途中で thinking の設定を変えると、API はエラーにせず、そのリクエストの thinking を切る（U24）

### thinking の本文（`display`）

- Claude 5 系・Mythos Preview・Opus 4.8・4.7 は、`display` の既定が `"omitted"`
  - thinking ブロックの `thinking` が空（`signature` だけ）で返る
  - ストリーミングでも、空の `thinking_delta` のあとに `signature_delta` が来る
  - `thinking: {"type": "adaptive", "display": "summarized"}` で要約が返る
- Opus 4.6・Sonnet 4.6 以前は、`"summarized"` が既定
- 要約でも省いても、料金は同じで、推論の全体の分がかかる（「Omitting reduces latency, not cost」）
- `display` の値は `summarized`・`omitted`（Thinking のページは、beta ヘッダーの要る `updates` も挙げる）
- `type` と一緒に送る。`type` は必須で、`display` だけを送る例もない
- `adaptive` と `enabled` で使え、`disabled` と `between_tools` では無効（「there is nothing to display」）
- 会話の途中で `display` を変えてよい（「Switching `display` values between turns in a conversation is supported」）。`signature` は `display` によらず同じ。省いたブロックを送り返すときも、手を加えずに送る

## 出力の上限（`max_tokens`）

- 必須で、省いたときの既定はない。リファレンスのほかの任意のパラメータには「optional」と付いているが、`max_tokens` には付いていない。最小は 0（0 はプロンプトキャッシュを温めるだけで、応答を作らない）
- 上限はモデルによる（「Different models have different maximum values for this parameter」）。同期の Messages API では次のとおり
  - 128K: Claude 5 系（Fable・Mythos・Opus・Sonnet・Haiku）、Opus 4.8・4.7・4.6、Sonnet 4.6
  - 64K: Opus 4.5・Sonnet 4.5・Haiku 4.5・Sonnet 4（Sonnet 4 は提供終了。Google Cloud と Bedrock を除く）
  - 32K: Opus 4.1・Opus 4（提供終了。Google Cloud などを除く）
  - Message Batches API では、Claude 5 系の一部と Opus 4.8〜4.6・Sonnet 4.6 が、beta ヘッダー `output-300k-2026-03-24` で 300K まで
- Models API（`GET /v1/models/{model_id}`）が、トップレベルの `max_tokens` で、そのモデルに送れる `max_tokens` の最大を返す。`max_input_tokens` は文脈の窓
- モデルの上限を超える `max_tokens` を送ったときに、400 になるか切り詰められるかは書かれていない（U36）
- 入力と `max_tokens` の和が文脈の窓を超えるとき:
  - Claude 4.5 以降は受け付け、生成が窓に達したら `stop_reason: "model_context_window_exceeded"` で止まる
  - それより前のモデルは検証エラー。beta ヘッダー `model-context-window-exceeded-2025-08-26` で同じ動きにできる
  - 入力だけで窓を超えると、どのモデルでも 400（「prompt is too long」）
- thinking のトークンも `max_tokens` に含まれる（「Thinking tokens are a subset of your `max_tokens` parameter」）
- レート制限: 出力のトークンの上限（OTPM）は、実際に出したトークンだけを数える。`max_tokens` は数えないので、大きくしても制限の上で損はない
- 時間のかかるリクエスト:
  - 10 分を超えるようなものは、ストリーミングか Message Batches API を勧めている。大きな `max_tokens` をストリーミングなしで送ることも勧めていない。ネットワークが、何も流れない接続を途中で切ることがあるため
  - 公式 SDK は、ストリーミングなしで 10 分を超えそうなリクエストを、送る前に止める。API の側が送る前に断るとは書かれていない（U37）
  - サーバーにも時間の上限があり、応答を始める前に達すると `timeout_error` を返す。長さは書かれていない
- switchllm は、省かれたら 64000 を送る。提供中のモデルすべての上限に収まる値で、モデル名を見て変えない（[判断](../design-docs/anthropic-max-tokens.md)）

## 注意点

- thinking とツールを一緒に使うとき、thinking ブロックを signature ごと手を加えずに返す必要がある（[判断](../design-docs/provider-state-resend.md)）
- OpenAI SDK 互換のレイヤーは、本番向けではないとされているので使わない（[判断](../design-docs/native-apis.md)）
- ツールの強制（`tool_choice` の `any` と `tool`。共通の `"required"` とツール名）:
  - Claude Fable 5.2・Mythos 5.2・Opus 5.5・Sonnet 5.5・Fable 5.1・Mythos 5.1 と、thinking を予算で指定するモード（`thinking: {"type": "enabled"}`）では 400 になる
  - エラーの文言は `tool_choice: type "tool" and "any" are not supported for this model.`
  - `auto` と `none` は、どのモデルでも使える
  - それ以外のモデルは強制を受け付けるが、強制した応答は thinking を飛ばして `tool_use` から始まる
  - switchllm は先回りして弾かず、黙って `auto` に替えもせず、API のエラーをそのまま `BadRequestError` で返す（[RELIABILITY.md](../RELIABILITY.md)）
  - 公式は代わりに、`auto` のままプロンプトでツールを使う場面を書くことを勧めている

## 構造化出力

`response_schema=` は `output_config.format` で送る（[判断](../design-docs/structured-output.md)）。以下は 2026-11-20 に文書で確かめた形式。

- 指定: `output_config: {"format": {"type": "json_schema", "schema": {...}}}`
  - `name` はない。正式な機能で、beta ヘッダーは要らない
  - 以前の `output_format` は非推奨で、beta ヘッダー `structured-outputs-2025-11-13` を付けないと 400 になる
- 対応するモデル: 「Structured outputs are available on every model」
- スキーマの条件:
  - オブジェクトの `additionalProperties` は `false` にする（それ以外は使えない）
  - すべてのプロパティを `required` に入れる必要はない（出力では、required の項目が先に並ぶ）
  - 対応しない機能を使うと「a 400 error with details」
- 使えるもの:
  - 基本の型すべて（object・array・string・integer・number・boolean・null）
  - `$ref`・`$defs`・`definitions`、`default`
  - `enum` と `const`（文字列・数・真偽値・null だけ）
  - `anyOf` と `allOf`（制限つき）
  - string の `format`（date-time・time・date・duration・email・hostname・uri・ipv4・ipv6・uuid）、単純な `pattern`
  - array の `minItems`（0 と 1 だけ）
- 使えないもの:
  - 再帰するスキーマ、`oneOf`（`anyOf` を使う）、`not`
  - `type` や `properties` と同じ階層の `anyOf`
  - 数の制約（`minimum`・`maximum`・`multipleOf` など）、文字列の長さ（`minLength`・`maxLength`）
  - `minItems` の 0・1 以外、外部の `$ref`
- 上限（1つのリクエストの、strict なツールと JSON 出力のスキーマを合わせた数）:
  - 省略できるパラメータは 24 個まで
  - `anyOf` か型の配列を使うパラメータは 16 個まで
  - strict なツールは 20 個まで
  - 内部の上限を超えると、400 の「Schema is too complex for compilation.」。コンパイルは 180 秒で打ち切る
- 結果:
  - JSON は `text` ブロックに入る。HTTP の応答に、読んだ値はない（`parsed_output` は SDK の機能）
  - 拒否は `stop_reason: "refusal"`（200 で返り、出力はスキーマに合わないことがある）
  - `max_tokens` で切れたら、出力が途中までのことがある
  - enum と const の値は、大文字・小文字が違って返ることがある
- 一緒に使えるもの:
  - ストリーミングは、いつもどおり
  - thinking とも使える（文法は thinking には効かず、最後の答えにだけ効く）
  - strict なツールとも、同じリクエストで使える
  - citations を有効にすると 400。assistant の先読み（prefill）とは一緒に使えない
- 速さ: スキーマごとに文法をコンパイルするので、初回は遅くなる。コンパイルした文法は、最後に使ってから 24 時間残る。`output_config.format` を変えると、その会話のプロンプトキャッシュが無効になる
- API の版: 文書の例は `anthropic-version: 2026-10-21` を送っている。switchllm の既定も、2026-11-21 から `2026-10-21` にした。`2023-06-01` で `output_config` を受け付けるかは書かれていない（U19）

## ファイルの入力

`FilePart` は `document` ブロックで送る（[判断](../design-docs/file-input.md)）。以下は 2026-11-23 に文書で確かめた形式。

- PDF: `{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": ...}}`
  - `data` は data URL ではなく base64
  - ほかの source は、`url`（PDF の URL）、`text`（テキストをそのまま）、`content`（ブロックの配列）、`file`（Files API の `file_id`）
- テキスト: `{"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "..."}}`（引用の文書の例）
  - PDF のガイドは「Plain text files such as .txt, .csv, or .md can be used directly in document blocks」とし、Files API には `text/plain` で上げるとする
  - switchllm は、テキストの種類（Markdown・CSV・JSON など）も、base64 を UTF-8 に戻して `text/plain` の `text` で送る（U30）。UTF-8 として読めなければ、送る前に `UnsupportedFeatureError`
- 受け付けない種類: 「Binary formats such as .xlsx or .docx are not supported in document blocks, so convert them to text or PDF first」。switchllm は、送る前に `UnsupportedFeatureError` にする
- 省略できる項目: `title`・`context`・`citations`・`cache_control`。switchllm は送らない（`filename` を `title` に入れない）
- PDF の読み方: 「The system converts each page of the document into an image」で、ページごとの文字も一緒に渡す。1ページで、文字が 1,500〜3,000 トークン、画像が最大 1,568 トークン
- 上限: リクエストは 128 MB まで。ページはリクエストあたり 600（文脈の長さが 100 万トークン未満のときは 100）。パスワードや暗号化のある PDF は読めない
- 順番: 「Place PDFs before text in your requests」。switchllm はパーツの順を変えないので、`FilePart` を先に置く
- beta のヘッダーは要らない（PDF のガイドの例は `anthropic-version: 2026-10-21` だけを送る）

## エラー

- 形は `{"type": "error", "error": {"type": "...", "message": "..."}, "request_id": "req_..."}`。`error.details.error_code` が付くこともあり、種類は増える
- 原因の引数を指す項目（OpenAI の `param` のようなもの）はない。フィールド名は文言に入る
- モデルが受け付けない設定の 400（`invalid_request_error`）の文言:
  - ツールの強制: `tool_choice: type "tool" and "any" are not supported for this model.`
  - Claude 4.7 以降の `thinking.type.enabled`・`disabled`: `"thinking.type.enabled" is not supported for this model.`（`disabled` も同じ形）。続けて「Use "thinking.type.adaptive" and "output_config.effort" to control thinking behavior.」
  - Claude 4.5 以前の adaptive thinking: `adaptive thinking is not supported on this model`
  - Sonnet 5.5 で thinking を切る: 「To turn thinking off on this model, send "thinking": {"type": "between_tools"} instead of {"type": "disabled"}.」
  - Sonnet 5.5 で thinking を切ったまま effort を `xhigh` か `max` にする: `output_config.effort 'xhigh' is not supported when thinking is disabled on this model.`
  - `between_tools` を Sonnet 5.5 以外に送る: `"thinking.type.between_tools" is not supported for this model.`
- `display` と `temperature` のエラーの文言は、Errors のページにない
- switchllm は、送った共通の引数のフィールド名がエラーに出てきたら、`str(e)` に案内を足す（[判断](../design-docs/error-hints.md)）。文言に `tool_choice`・`output_config.effort`・`thinking.type.disabled` などが入るので当たる（U38）

## 出典

同じページを何度か確かめたものは、日付ごとに分けて書く。

### リクエストの形・API の版

- [Messages API](https://platform.claude.com/docs/en/api/messages)
  - 2026-11-20: `output_config.format` は `type: "json_schema"` と `schema` の2つの項目
  - 2026-11-21: `output_config.effort` は「Valid values are `low`, `medium`, `high`, `xhigh`, or `max`」。`budget_tokens` は「Must be ≥1024 and less than `max_tokens`」「Thinking tokens count toward your `max_tokens` limit」
  - 2026-11-26: `thinking` のスキーマは、自動取得の要約でしか読めなかった。`ThinkingConfigAdaptive` と `ThinkingConfigEnabled` の `type` は必須。`display` は `summarized` か `omitted` で「The default depends on the model」
  - 2026-11-28: 「`max_tokens: number`」「The maximum number of tokens to generate before stopping.」「minimum: 0」「Different models have different maximum values for this parameter.」。`max_tokens` に「optional」の印がない（`stop_sequences` などには「optional」と付いている）。上限を超えたときの扱いと、ストリーミングなしの長いリクエストの扱いは、読めた範囲には書かれていなかった（ページが長く、前の 8 万字ほどしか原文で読めていない）
- [Versioning](https://platform.claude.com/docs/en/api/versioning)
  - 2026-11-20: 版は `2026-10-21`・`2023-06-01`・`2023-01-01`。「Anthropic recommends using the latest API version whenever possible」。`2026-10-21` では「The `temperature`, `top_p`, and `top_k` request parameters are removed. A request that sends any of them returns a 400 error on every model」
  - 2026-11-21: 提供を終える日は書かれていない。「if you are using the API as documented in this reference, Anthropic will not break your usage」
- [Migrating to 2026-10-21](https://platform.claude.com/docs/en/api/migration-2026-10-21)
  - 2026-11-20: 「If you call the API without an SDK, set the `anthropic-version` header to `2026-10-21`」「If a request relies on a non-default value, either stop sending the parameter, or keep that request on `2023-06-01`」
  - 2026-11-21: Messages API の破壊的な変更は、`temperature`・`top_p`・`top_k`（「returns a 400 error, even at the parameter's default value」）、assistant メッセージの `document` ブロック、user メッセージの `citations` の3つ。「The Claude API accepts a key in either header」
- [Models overview](https://platform.claude.com/docs/en/about-claude/models/overview)
  - 2026-11-17: Claude API ID に `claude-sonnet-5-5` と `claude-haiku-5-5` がある（U13）
  - 2026-11-21: Models API の `capabilities.thinking.types.disabled.supported` は「`false` when the model rejects `"disabled"` with a 400 error」
  - 2026-11-28: 「All models」の表の Max output（上の「出力の上限」）。脚注「Max output: Synchronous Messages API limit. On the Message Batches API, Claude Opus 5.5, Claude Opus 5, Claude Sonnet 5.5, Claude Sonnet 5, Claude Haiku 5.5, Claude Opus 4.8, Claude Opus 4.7, Claude Opus 4.6, and Claude Sonnet 4.6 support up to 300k output tokens with the output-300k-2026-03-24 beta header.」。Haiku 3.5 は表にない
- [Get a Model](https://platform.claude.com/docs/en/api/models/retrieve)
  - 2026-11-28: トップレベルの `max_tokens` は「Maximum value for the `max_tokens` parameter when using this model.」、`max_input_tokens` は「Maximum input context window size in tokens for this model.」。どちらも `number or null`。例の値は 0 で、実際の値ではない。Batches の 300K を含むかは書かれていない
- [Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)
  - 2026-11-28: 「If the input alone already exceeds the model's context window, the API returns a 400 `invalid_request_error` ("prompt is too long") on every model.」「On Claude 4.5 models and newer, if input tokens plus `max_tokens` exceeds the context window size, the API accepts the request. If generation then reaches the context window limit, it stops with `stop_reason: "model_context_window_exceeded"`. On earlier models, the API returns a validation error instead.」「Thinking tokens are a subset of your `max_tokens` parameter, are billed as output tokens, and count toward rate limits.」「A single Messages API request to a model with a 1M-token context window can generate up to 128k output tokens (`max_tokens`).」
- [Rate limits](https://platform.claude.com/docs/en/api/rate-limits)
  - 2026-11-28: 「OTPM rate limits are evaluated in real time as output tokens are produced, counting only the actual tokens generated. The `max_tokens` parameter does not factor into OTPM rate limit calculations, so there is no rate limit downside to setting a higher `max_tokens` value.」
- [Errors](https://platform.claude.com/docs/en/api/errors) の「Forced tool use not supported」
  - 2026-11-16: ツールの強制のエラーの文言と、`auto` と `none` は受け付けること
- [Errors](https://platform.claude.com/docs/en/api/errors)（エラーの形と、モデルが受け付けない設定の文言）
  - 2026-11-28: 「The API always returns errors as JSON, with a top-level `error` object that always includes a `type` and `message` value.」「The response also includes a `request_id` field for easier tracking and debugging.」。`error.details.error_code` は「The set of codes can grow.」。上の「エラー」の文言。`param` に当たる項目はない
- [Errors](https://platform.claude.com/docs/en/api/errors) の「Long requests」
  - 2026-11-28: 「Consider using the streaming Messages API or Message Batches API for long-running requests, especially those over 10 minutes.」「Avoid setting a large `max_tokens` value without using the streaming Messages API or Message Batches API」「Some networks might drop idle connections after a variable period of time」「The Anthropic SDK validates that your non-streaming Messages API requests are not expected to exceed a 10-minute timeout.」。504 の項に「The API also returns `timeout_error` with other status codes, such as a 500 when a request reaches the server's time limit before the response starts.」
- [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) の FAQ「How do I calculate total input tokens from the usage fields?」
  - 2026-11-17: 「`input_tokens` does NOT represent all input tokens, only the portion after your last cache breakpoint」「total_input_tokens = cache_read_input_tokens + cache_creation_input_tokens + input_tokens」（U14）

### ツール・ストリーミング

- [Implement tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use)
  - 2026-11-15: `tool_choice` の `auto` は「This is the default value when `tools` are provided」
  - 2026-11-16（「Forcing tool use」）: 「Not every model and setting supports forced tool use」。対応しないモデルの一覧。強制すると「the response starts with the `tool_use` block and has no `thinking` block」。`{"type": "none"}` は「prevents Claude from using any tools」
  - 2026-11-17: 「`none` prevents Claude from using any tools」。ツールの強制を受け付けないモデルでも「`tool_choice: {"type": "auto"}` (the default) and `tool_choice: {"type": "none"}` still work」（U9）
- [Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)
  - 2026-11-15: イベントの流れの最後は「A final `message_stop` event」
  - 2026-11-28: 「This is especially useful for requests with large `max_tokens` values, where the SDK requires streaming to avoid HTTP timeouts.」。例は `max_tokens` 128000 をストリーミングで送っている。ストリーミングが必須とは書かれていない
- [Streaming Messages](https://docs.anthropic.com/en/api/messages-streaming)（日付の記録なし）
- [OpenAI SDK compatibility](https://docs.anthropic.com/en/api/openai-sdk)（日付の記録なし）

### thinking・推論の強さ

- [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)
  - 2026-11-17: 「the tokens Claude spends reasoning are billed as output tokens, even when the thinking text isn't returned to you」
  - 2026-11-17（「Thinking with tool use」）: 強制は「incompatible with manual extended thinking」。6つのモデルで拒否する理由は、どの文書にも書かれていない
  - 2026-11-17（「Thinking block preservation by model」）: 「Don't strip thinking blocks when you switch. The API drops unreadable ones without billing them, and a model that reads them still gets them.」「Required: within a tool-use turn, pass thinking blocks back.」。アカウントに結びつくモデルの一覧
  - 2026-11-21: 「On Claude 5 and later models and Claude Mythos Preview, thinking is already on and needs no configuration」。「On Claude Opus 4.8, Claude Opus 4.7, Claude Opus 4.6, and Claude Sonnet 4.6, thinking is off until you set `thinking: {type: "adaptive"}`」。モデルごとの `thinking.type` の表。「Thinking can't be turned off on these models」。`display` の既定。Sampling parameters の「non-default `temperature`, `top_p`, or `top_k` values return a 400 error on every request, regardless of whether thinking is used」と、古いモデルは「the restriction applies only while thinking is on」。途中で thinking を切り替えると「it silently disables thinking for that request」。effort を変えるとキャッシュが効かなくなる
  - 2026-11-26: `display` の既定は、`"omitted"` が「Claude Fable 5.2, … Claude Opus 5, Claude Sonnet 5.5, Claude Sonnet 5, Claude Opus 4.8, Claude Opus 4.7, [Claude Mythos Preview], and Claude Haiku 5.5」、`"summarized"` が「Claude Opus 4.6, Claude Sonnet 4.6, and earlier models」。「`display` works in both modes: set it alongside `type: "adaptive"` or `type: "enabled"`」「`display` is invalid with `thinking.type: "disabled"` (there is nothing to display)」「The `signature` field is identical whichever `display` value you set. Switching `display` values between turns in a conversation is supported.」「You're charged for the full thinking tokens generated by the original request, not the summary tokens.」「Omitting reduces latency, not cost.」。省いたときのストリーミング
- [Extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)
  - 2026-11-26: 「Claude Opus 4.5, Claude Sonnet 4.5, Claude Haiku 4.5, Claude Opus 4.1, Claude Sonnet 4, and Claude Opus 4 support only extended thinking. On these models, `type: "adaptive"` returns a 400 error.」
  - 古い URL（https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking）も、日付の記録なしで載せていた
- [Effort](https://platform.claude.com/docs/en/build-with-claude/effort)
  - 2026-11-21: 「Set `output_config.effort` on the request」。「Most Claude models default to high effort ... Claude Opus 5.5 and Claude Haiku 5.5 default to medium」。対応するモデルの一覧。`max` と `xhigh` の段階ごとの一覧。「Not every model that supports `max` supports `xhigh`」「it works with or without thinking」「The effort parameter affects all tokens in the response」「Effort is a behavioral signal, not a strict token budget」「Where adaptive thinking is available, effort is the recommended way to control thinking depth」「`adaptive` is a thinking mode, not an effort level」

### preserved thinking

- [Preserved thinking](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking) の「Switch models and back」
  - 2026-11-17: 「A thinking block that the new model or account can't use never causes an error. The API drops it for that request and doesn't bill it」「A dropped block doesn't count as a change to the conversation, so both models' blocks stay valid.」。モデルの組み合わせごとの一覧。preserved thinking のモデルは `thinking: {"type": "enabled"}` を 400 にする
- [Prefix checks](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking/prefix-checks)
  - 2026-11-17: 「A prefix check fails if system, tools, or an earlier message changed after Claude produced a block. It also fails if a thinking block between others was left out or moved.」。400 になるかはモデルによる。比べないパラメータ。`prefix_mismatch_behavior`
- [Fix: bound to a different conversation](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking/fix-bound-to-a-different-conversation)
  - 2026-11-17: 400 の文言

### 構造化出力・ファイル

- [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
  - 2026-11-20: 「The request carries the schema in `output_config.format` with `type: "json_schema"`」「Structured outputs are available on every model」「`required` and `additionalProperties` (must be set to `false` for objects)」「If you use an unsupported feature, you'll receive a 400 error with details」。使えるもの・使えないものの一覧。上限の表。拒否と `max_tokens` のとき。「Grammars apply only to Claude's direct output, not to tool use calls, tool results, or thinking tags」。citations は 400。「Prefilling is incompatible with JSON outputs」。「The `output_format` parameter has moved to `output_config.format`, and beta headers are no longer required」。文法のキャッシュ
- [PDF support](https://platform.claude.com/docs/en/build-with-claude/pdf-support)
  - 2026-11-23: base64 と URL の document の例。「Maximum request size: 128 MB」「Maximum pages per request: 600 (100 when the request's context window is under 1M tokens)」。「Plain text files such as .txt, .csv, or .md can be used directly in document blocks」「Binary formats such as .xlsx or .docx are not supported in document blocks」。ページの文字と画像のトークン。「Place PDFs before text in your requests」。例に beta のヘッダーがない
- [Citations](https://platform.claude.com/docs/en/build-with-claude/citations)
  - 2026-11-23: `{"type": "text", "media_type": "text/plain", "data": ...}` の document の例。「`title` and `context` are optional fields」

## 関連する未検証の前提

U8、U9、U14、U19、U24、U25、U30、U33、U36〜U38（[実 API 検証](../exec-plans/active/real-api-verification.md)）
