# Anthropic Messages API

- アダプター: `providers/anthropic.py`（プリセット `anthropic`）
- エンドポイント: `POST https://api.anthropic.com/v1/messages`
- 認証: `x-api-key: $ANTHROPIC_API_KEY` と `anthropic-version: 2026-10-21`（`anthropic_version=` で変更できる。下の「API の版」）。beta 機能は `beta=[...]` が `anthropic-beta` ヘッダーになる。`x-api-key` は `headers=` で置き換えられるが、`anthropic-version` と `anthropic-beta` は `headers=` では渡せない（専用のオプションを使う。[判断](../design-docs/custom-headers.md)）

## 共通型との対応

| 共通 | Messages API |
|---|---|
| system | トップレベルの `system`（system メッセージが複数あれば空行で連結） |
| user | `content` ブロック（`text` / `image` / `document`）。空の text ブロックは拒否されるので送らない |
| assistant（同じプロバイダーキーのとき） | 受け取った `content` ブロックをそのまま送る。モデルが違っても送る（下の「モデルの切り替え」） |
| assistant（再送しないとき） | `text` と `tool_use`（`input` はオブジェクト） |
| ツール結果 | user メッセージ内の `tool_result`（`tool_use_id`。エラーは `is_error: true`） |
| 連続する同じ role | 1メッセージにまとめる（並列ツールの結果は1つの user メッセージに入れる） |
| max_tokens | `max_tokens`（**必須**。指定がなければ 4096。`default_max_tokens=` で変更できる） |
| stop | `stop_sequences` |
| temperature | 既定の版（`2026-10-21`）では送る前に `UnsupportedFeatureError`。`anthropic_version="2023-06-01"` なら `temperature`（下の「API の版」） |
| reasoning_effort | `output_config.effort`。`none` は `thinking: {"type": "disabled"}`、`minimal` は `UnsupportedFeatureError`（下の「推論の強さ」） |
| ツール定義 | `{"name", "description", "input_schema"}` |
| tool_choice | `{"type": "auto"}` / `{"type": "none"}` / `{"type": "any"}` / `{"type": "tool", "name"}`。指定がなければ送らない（ツールがあれば既定は `auto`） |
| 画像 | `{"type": "image", "source": {"type": "base64", ...}}` か `{"type": "url", "url"}` |
| ファイル | PDF は `{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data"}}`。テキスト（`text/*`・`application/json`・`application/xml`）は base64 を UTF-8 に戻し、`{"type": "text", "media_type": "text/plain", "data"}` の document。ほかの種類は送る前に `UnsupportedFeatureError`（下の「ファイルの入力」） |

## レスポンス

- `content` ブロックの `text` / `thinking` / `tool_use` を読む。再送用に `content` 全体を `ProviderState` に入れる（thinking の `signature` を含む）
- stop_reason: `end_turn`・`stop_sequence` は `end`、`tool_use` は `tool_use`、`max_tokens`・`model_context_window_exceeded` は `max_tokens`、`refusal` は `content_filter`
- usage: `input_tokens` はキャッシュ分を含まないので、`cache_creation_input_tokens` と `cache_read_input_tokens` を足す（U14。文書で確認済み）。`output_tokens` は thinking の分を含む（thinking の本文を返さない設定でも、生成した分すべてが出力として課金される）

## ストリーミング

| イベント | 扱い |
|---|---|
| `message_start` | メッセージの骨組み |
| `content_block_start` / `content_block_delta` / `content_block_stop` | ブロックを組み立てる。`text_delta` は `text`、`thinking_delta` は `thinking`、`input_json_delta` は連結して最後にパース（JSON として読めなければ `arguments={}` にして原文を `raw_arguments` に残す。max_tokens で途中で切れたときなど）、`signature_delta` は連結 |
| `message_delta` | `stop_reason` と usage を更新 |
| `message_stop` | 終了して `done`。届かないまま閉じたら `StreamError` |
| `error` | `StreamError` |
| `ping`・その他 | 無視（Anthropic は新しいイベントを追加することがあると明言している） |

## モデルの切り替え

Anthropic のモデルどうしで切り替えるときも、thinking ブロックを捨てずに、受け取ったまま送る（U8。[判断](../design-docs/provider-state-resend.md)）。

- Anthropic は「切り替えるときに thinking ブロックを取り除くな」としている。新しいモデルが読めないブロック（別のモデルや別のアカウントのもの）は、API がそのリクエストでは捨て、エラーにもせず料金もかけない。読めるモデルには届き、推論を引き継げる
- 捨てられたブロックは会話の変更とは数えないので、元のモデルに戻しても、両方のモデルのブロックが有効なまま
- どの組み合わせで読めるかはモデルによる（例: Opus 5.5 から Fable 5.1 へは引き継ぐが、Fable 5.1 から Opus 5.5 へは捨てる）。一覧は出典の「Which model switches keep the reasoning」にあり、増え続けるので、ここには写さない
- Sonnet 5.5・Haiku 5.5・Fable 5.2・Mythos 5.2 の thinking ブロックは、作ったアカウント（かつながったアカウント）でしか使えない。ほかのアカウントでは API がエラーにせずに捨てる
- 再送するのは同じプロバイダーキーのときだけ。`register_provider()` で同じ `anthropic` 形式を別の名前で足した接続先には送らない
- 切り替え先のモデルで 400 になるのは、ブロックではなく設定の違いによるもの（preserved thinking に対応するモデルは `thinking: {"type": "enabled"}` を 400 にする。Opus 5.5 などは `reasoning_effort="none"` で送る `{"type": "disabled"}` を 400 にする）。switchllm は、利用者の指定と違う thinking の設定を送らない

### 前置きの検査（prefix check）

preserved thinking に対応するモデル（Fable 5.2・Mythos 5.2・Fable 5.1・Mythos 5.1・Opus 5.5・Sonnet 5.5・Haiku 5.5）は、thinking ブロックを送り返すと、それより前の内容（system・tools・それより前のメッセージ）がブロックを作ったときと同じかを確かめる。違えば、モデルによって 400 になるか、そのまま読まれる。

- 1語でも変わったら違うと数える。thinking ブロックのうち、間のものだけを抜いたり動かしたりするのも違うと数える（古いもの・新しいもの・全部を抜くのはよい）
- モデル・effort・`max_tokens`・`tool_choice` などのパラメータ、JSON の書式やキーの順は比べない
- 400 の文言は ``Invalid `signature` in `thinking` block. The block is bound to a different conversation.``。`thinking.block_binding.prefix_mismatch_behavior` を `"drop_block"` にすると、エラーにせずにブロックを捨てる（beta ヘッダー `thinking-binding-controls-2026-08-01` が要る）
- switchllm は、同じ履歴からは毎回同じリクエストを組み立てる（system はトップレベルにまとめ、続く同じ role のメッセージは1つにまとめる）。`res.message` を加工せずに履歴に積めば、前置きは変わらない。履歴の途中を書き換えたり、`tools` を変えたりすると、この検査に当たりうる

## API の版

既定は `2026-10-21`（2026-11-21 に `2023-06-01` から上げた。[判断](../design-docs/anthropic-api-version.md)）。`anthropic_version=` で変えられる。以下は 2026-11-21 に文書で確かめた内容。

- 公式は「Anthropic recommends using the latest API version whenever possible」とする。`2023-06-01` の提供を終える日は書かれていない
- `2026-10-21` の、Messages API の破壊的変更は3つ
  - `temperature`・`top_p`・`top_k` を送ると、どのモデルでも、既定の値でも 400
  - assistant メッセージに `document` ブロックを直接置くと 400
  - user メッセージの `text` ブロックに空でない `citations` を付けると 400
- switchllm が組み立てるリクエストのうち、当たるのは `temperature` だけ。版が `2026-10-21` 以降なら、`temperature=` を送る前に `UnsupportedFeatureError` にする。`anthropic_version="2023-06-01"` なら今までどおり送る。assistant メッセージの再送（受け取った `content` をそのまま送る）は、assistant の citations を禁じていないので当たらない
- 応答の形・ストリーミングのイベント・stop_reason の変更は書かれていない。キーは「The Claude API accepts a key in either header」で、`x-api-key` のままでよい（SDK は `Authorization: Bearer` に変えた）
- 古い版でも、今のモデルは `temperature` を自由には使えない。Fable 5.2・Mythos 5.2・Fable 5.1・Mythos 5.1・Fable 5・Mythos 5・Mythos Preview・Opus 5.5・Opus 5・Opus 4.8・Opus 4.7・Sonnet 5.5・Sonnet 5・Haiku 5.5 は、既定でない `temperature`・`top_p`・`top_k` を、thinking の有無にかかわらず 400 にする（`top_p` の既定は 0.99 なので、`1` も 400。`temperature` と `top_p` を一緒に送るのも、既定の値でも 400）。それより古いモデルは、`2026-10-21` より前の版なら、thinking が有効なときだけ制限がある（`temperature` と `top_k` は thinking と一緒に使えず、`top_p` は 0.95〜1）

## 推論の強さ

`reasoning_effort=` は `output_config.effort` で送る。`none` は `thinking: {"type": "disabled"}` にし、`minimal` は送る前に `UnsupportedFeatureError` にする（[判断](../design-docs/reasoning-effort.md)）。`reasoning_summary=True` は `thinking: {"type": "adaptive", "display": "summarized"}` で送る（[判断](../design-docs/reasoning-summary.md)。U33）。以下は 2026-11-21 に文書で確かめた形式（`display` は 2026-11-26）。

- 指定: `output_config: {"effort": "medium"}`。段階は `low`・`medium`・`high`・`xhigh`・`max` の5つで、`none` と `minimal` はない。`response_schema=` の `output_config.format` と同じオブジェクトに並ぶ
- 既定: 多くのモデルは `high`。Opus 5.5 と Haiku 5.5 は `medium`。既定と同じ段階を送るのは、送らないのと同じ
- 対応するモデル: Claude 5 系すべて（Fable・Mythos・Opus・Sonnet・Haiku）、Opus 4.8・4.7・4.6・Sonnet 4.6・Opus 4.5。Haiku 4.5 と Sonnet 4.5 は一覧にない。対応しないモデルに送ったときに 400 か無視かは書かれていない（U24）
- 段階ごと: `max` は上の一覧のうち Opus 4.5 以外（Mythos Preview・Opus 4.6・Sonnet 4.6 を含む）。`xhigh` はそこから Mythos Preview・Opus 4.6・Sonnet 4.6 を除いたもの。「Not every model that supports `max` supports `xhigh`」
- 効く範囲: thinking だけでなく、本文とツール呼び出しにも効く（「The effort parameter affects all tokens in the response」）。thinking がなくても効く（「it works with or without thinking」）。厳密な予算ではない（「Effort is a behavioral signal, not a strict token budget」）。`max_tokens` は thinking と本文を合わせた上限で、`high` 以上では大きく取ることを勧めている
- 公式の勧め: 「Where adaptive thinking is available, effort is the recommended way to control thinking depth」
- thinking の設定（`thinking.type`）: `adaptive`（Claude が考えるかと量を決める）・`enabled`（`budget_tokens` を 1024 以上で `max_tokens` 未満）・`disabled`・`between_tools`（Sonnet 5.5 だけ）
  - `thinking` を送らないとき: Claude 5 系と Mythos Preview は adaptive で考える。Opus 4.8・4.7・4.6・Sonnet 4.6 は考えない（`{"type": "adaptive"}` を送ると考える）。4.5 系も考えない
  - `enabled` は Claude 5 系・Opus 4.8・4.7 で 400。`adaptive` は 4.5 系で 400
  - `disabled`: Fable・Mythos の 5 系すべて、Mythos Preview、Opus 5.5 は 400（推論を切れない）。Opus 5 と Haiku 5.5 は effort が `high` 以下なら受け付け、`xhigh`・`max` では 400。Sonnet 5.5 は 400 で、代わりに `between_tools`。Sonnet 5・Opus 4.8・4.7・4.6・Sonnet 4.6・4.5 系は受け付ける。Models API の `capabilities.thinking.types.disabled.supported` で確かめられる
  - `disabled` にしたリクエストで、前のターンの thinking ブロックを送り返してよいかは書かれていない。ツールの往復の途中で thinking の設定を変えると、API はエラーにせず、そのリクエストの thinking を切る（U24）
- thinking の本文: Claude 5 系・Mythos Preview・Opus 4.8・4.7 は `display` の既定が `"omitted"` で、thinking ブロックの `thinking` が空（`signature` だけ）で返る。ストリーミングでも空の `thinking_delta` のあとに `signature_delta` が来る。`thinking: {"type": "adaptive", "display": "summarized"}` で要約が返る。Opus 4.6・Sonnet 4.6 以前は `"summarized"` が既定。要約でも省いても料金は同じで、推論の全体の分がかかる（「Omitting reduces latency, not cost」）
  - `display` の値は `summarized`・`omitted`（Thinking のページは、beta ヘッダーの要る `updates` も挙げる）。`type` と一緒に送る（`type` は必須で、`display` だけを送る例もない）。`adaptive` と `enabled` で使え、`disabled` と `between_tools` では無効（「there is nothing to display」）
  - 会話の途中で `display` を変えてよい（「Switching `display` values between turns in a conversation is supported」）。`signature` は `display` によらず同じ。省いたブロックを送り返すときも、手を加えずに送る
- 段階を会話の途中で変えてよい。前置きの検査は effort を比べない（上の「前置きの検査」）。ただし、effort や thinking の設定を変えると、プロンプトキャッシュが効かなくなる

## 注意点

- thinking とツールを併用するとき、thinking ブロックを signature ごと無加工で返す必要がある（[判断](../design-docs/provider-state-resend.md)）
- OpenAI SDK 互換のレイヤーは本番向けではないとされているので使わない（[判断](../design-docs/native-apis.md)）
- ツールの強制（`tool_choice` の `any` と `tool`。共通の `"required"` とツール名）は、Claude Fable 5.2・Mythos 5.2・Opus 5.5・Sonnet 5.5・Fable 5.1・Mythos 5.1 と、thinking を予算で指定するモード（`thinking: {"type": "enabled"}`）では 400 になる。エラーの文言は `tool_choice: type "tool" and "any" are not supported for this model.`。`auto` と `none` はどれでも使える。それ以外のモデルは強制を受け付けるが、強制した応答は thinking を飛ばして `tool_use` から始まる。switchllm は先回りして弾かず、黙って `auto` に替えもせず、API のエラーをそのまま `BadRequestError` で返す（[RELIABILITY.md](../RELIABILITY.md)）。公式は代わりに、`auto` のままプロンプトでツールを使う場面を書くことを勧めている

## 構造化出力

`response_schema=` は `output_config.format` で送る（[判断](../design-docs/structured-output.md)）。以下は 2026-11-20 に文書で確かめた形式。

- 指定: `output_config: {"format": {"type": "json_schema", "schema": {...}}}`。`name` はない。正式な機能で、beta ヘッダーは要らない。以前の `output_format` は非推奨で、beta ヘッダー `structured-outputs-2025-11-13` を付けないと 400 になる
- 対応するモデル: 「Structured outputs are available on every model」
- スキーマの条件: オブジェクトの `additionalProperties` は `false` にする（それ以外は使えない）。すべてのプロパティを `required` に入れる必要はない（出力では required の項目が先に並ぶ）。対応しない機能を使うと「a 400 error with details」
- 使えるもの: 基本の型すべて（object・array・string・integer・number・boolean・null）、`$ref`・`$defs`・`definitions`、`default`、`enum` と `const`（文字列・数・真偽値・null だけ）、`anyOf` と `allOf`（制限つき）、string の `format`（date-time・time・date・duration・email・hostname・uri・ipv4・ipv6・uuid）、単純な `pattern`、array の `minItems`（0 と 1 だけ）
- 使えないもの: 再帰するスキーマ、`oneOf`（`anyOf` を使う）、`not`、`type` や `properties` と同じ階層の `anyOf`、数の制約（`minimum`・`maximum`・`multipleOf` など）、文字列の長さ（`minLength`・`maxLength`）、`minItems` の 0・1 以外、外部の `$ref`
- 上限（1つのリクエストの、strict なツールと JSON 出力のスキーマを合わせた数）: 省略できるパラメータは 24 個まで、`anyOf` か型の配列を使うパラメータは 16 個まで、strict なツールは 20 個まで。内部の上限を超えると 400 の「Schema is too complex for compilation.」。コンパイルは 180 秒で打ち切る
- 結果: JSON は `text` ブロックに入る（HTTP の応答に読んだ値はない。`parsed_output` は SDK の機能）。拒否は `stop_reason: "refusal"`（200 で返り、出力はスキーマに合わないことがある）。`max_tokens` で切れたら出力が途中までのことがある。enum と const の値は、大文字・小文字が違って返ることがある
- 併用: ストリーミングはいつもどおり。thinking とも使える（文法は thinking には効かず、最後の答えにだけ効く）。strict なツールとも同じリクエストで使える。citations を有効にすると 400。assistant の先読み（prefill）とは併用できない
- スキーマごとに文法をコンパイルするので、初回は遅くなる。コンパイルした文法は最後に使ってから 24 時間残る。`output_config.format` を変えると、その会話のプロンプトキャッシュが無効になる
- API の版: 文書の例は `anthropic-version: 2026-10-21` を送っている。switchllm の既定も 2026-11-21 から `2026-10-21` にした。`2023-06-01` で `output_config` を受け付けるかは書かれていない（U19）

## ファイルの入力

`FilePart` は `document` ブロックで送る（[判断](../design-docs/file-input.md)）。以下は 2026-11-23 に文書で確かめた形式。

- PDF: `{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": ...}}`。`data` は data URL ではなく base64。ほかの source は `url`（PDF の URL）、`text`（テキストをそのまま）、`content`（ブロックの配列）、`file`（Files API の `file_id`）
- テキスト: `{"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "..."}}`（引用の文書の例）。PDF のガイドは「Plain text files such as .txt, .csv, or .md can be used directly in document blocks」とし、Files API には `text/plain` で上げるとする。switchllm は、テキストの種類（Markdown・CSV・JSON など）も、base64 を UTF-8 に戻して `text/plain` の `text` で送る（U30）。UTF-8 として読めなければ、送る前に `UnsupportedFeatureError`
- 受け付けない種類: 「Binary formats such as .xlsx or .docx are not supported in document blocks, so convert them to text or PDF first」。switchllm は送る前に `UnsupportedFeatureError` にする
- 省略できる項目: `title`・`context`・`citations`・`cache_control`。switchllm は送らない（`filename` を `title` に入れない）
- PDF の読み方: 「The system converts each page of the document into an image」で、ページごとの文字も一緒に渡す。1ページで文字が 1,500〜3,000 トークン、画像が最大 1,568 トークン
- 上限: リクエストは 128 MB まで。ページはリクエストあたり 600（文脈の長さが 100 万トークン未満のときは 100）。パスワードや暗号化のある PDF は読めない
- 順番: 「Place PDFs before text in your requests」。switchllm はパーツの順を変えないので、`FilePart` を先に置く
- beta のヘッダーは要らない（PDF のガイドの例は `anthropic-version: 2026-10-21` だけを送る）

## 出典

- [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)（2026-11-20 確認: 「The request carries the schema in `output_config.format` with `type: "json_schema"`」「Structured outputs are available on every model」「`required` and `additionalProperties` (must be set to `false` for objects)」「If you use an unsupported feature, you'll receive a 400 error with details」、使えるもの・使えないものの一覧、上限の表、拒否と `max_tokens` のとき、「Grammars apply only to Claude's direct output, not to tool use calls, tool results, or thinking tags」、citations は 400、「Prefilling is incompatible with JSON outputs」、「The `output_format` parameter has moved to `output_config.format`, and beta headers are no longer required」、文法のキャッシュ）
- [Messages API](https://platform.claude.com/docs/en/api/messages)（2026-11-20 確認: `output_config.format` は `type: "json_schema"` と `schema` の2つの項目）
- [Versioning](https://platform.claude.com/docs/en/api/versioning)（2026-11-21 確認: 提供を終える日は書かれていない、「if you are using the API as documented in this reference, Anthropic will not break your usage」。2026-11-20 確認: 版は `2026-10-21`・`2023-06-01`・`2023-01-01`。「Anthropic recommends using the latest API version whenever possible」。`2026-10-21` では「The `temperature`, `top_p`, and `top_k` request parameters are removed. A request that sends any of them returns a 400 error on every model」）
- [Migrating to 2026-10-21](https://platform.claude.com/docs/en/api/migration-2026-10-21)（2026-11-20 確認: 「If you call the API without an SDK, set the `anthropic-version` header to `2026-10-21`」「If a request relies on a non-default value, either stop sending the parameter, or keep that request on `2023-06-01`」）
- [Migrating to 2026-10-21](https://platform.claude.com/docs/en/api/migration-2026-10-21)（2026-11-21 確認: Messages API の破壊的変更は、`temperature`・`top_p`・`top_k`（「returns a 400 error, even at the parameter's default value」）、assistant メッセージの `document` ブロック、user メッセージの `citations` の3つ。「The Claude API accepts a key in either header」）
- [Effort](https://platform.claude.com/docs/en/build-with-claude/effort)（2026-11-21 確認: 「Set `output_config.effort` on the request」、「Most Claude models default to high effort ... Claude Opus 5.5 and Claude Haiku 5.5 default to medium」、対応するモデルの一覧、`max` と `xhigh` の段階ごとの一覧、「Not every model that supports `max` supports `xhigh`」「it works with or without thinking」「The effort parameter affects all tokens in the response」「Effort is a behavioral signal, not a strict token budget」「Where adaptive thinking is available, effort is the recommended way to control thinking depth」「`adaptive` is a thinking mode, not an effort level」）
- [Messages API](https://platform.claude.com/docs/en/api/messages)（2026-11-21 確認: `output_config.effort` は「Valid values are `low`, `medium`, `high`, `xhigh`, or `max`」、`budget_tokens` は「Must be ≥1024 and less than `max_tokens`」「Thinking tokens count toward your `max_tokens` limit」）
- [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)（2026-11-21 確認: 「On Claude 5 and later models and Claude Mythos Preview, thinking is already on and needs no configuration」、「On Claude Opus 4.8, Claude Opus 4.7, Claude Opus 4.6, and Claude Sonnet 4.6, thinking is off until you set `thinking: {type: "adaptive"}`」、モデルごとの `thinking.type` の表、「Thinking can't be turned off on these models」、`display` の既定、Sampling parameters の「non-default `temperature`, `top_p`, or `top_k` values return a 400 error on every request, regardless of whether thinking is used」と、古いモデルは「the restriction applies only while thinking is on」、途中で thinking を切り替えると「it silently disables thinking for that request」、effort を変えるとキャッシュが効かなくなる）
- [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)（2026-11-26 確認: `display` の既定は、`"omitted"` が「Claude Fable 5.2, … Claude Opus 5, Claude Sonnet 5.5, Claude Sonnet 5, Claude Opus 4.8, Claude Opus 4.7, [Claude Mythos Preview], and Claude Haiku 5.5」、`"summarized"` が「Claude Opus 4.6, Claude Sonnet 4.6, and earlier models」。「`display` works in both modes: set it alongside `type: "adaptive"` or `type: "enabled"`」「`display` is invalid with `thinking.type: "disabled"` (there is nothing to display)」「The `signature` field is identical whichever `display` value you set. Switching `display` values between turns in a conversation is supported.」「You're charged for the full thinking tokens generated by the original request, not the summary tokens.」「Omitting reduces latency, not cost.」、省いたときのストリーミング）
- [Extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)（2026-11-26 確認: 「Claude Opus 4.5, Claude Sonnet 4.5, Claude Haiku 4.5, Claude Opus 4.1, Claude Sonnet 4, and Claude Opus 4 support only extended thinking. On these models, `type: "adaptive"` returns a 400 error.」）
- [Messages API](https://platform.claude.com/docs/en/api/messages)（2026-11-26 確認。`thinking` のスキーマは自動取得の要約でしか読めなかった: `ThinkingConfigAdaptive` と `ThinkingConfigEnabled` の `type` は必須、`display` は `summarized` か `omitted` で「The default depends on the model」）
- [Models overview](https://platform.claude.com/docs/en/about-claude/models/overview)（2026-11-21 確認: Models API の `capabilities.thinking.types.disabled.supported` は「`false` when the model rejects `"disabled"` with a 400 error」）
- [Streaming Messages](https://docs.anthropic.com/en/api/messages-streaming)
- [Extended thinking](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking)
- [OpenAI SDK compatibility](https://docs.anthropic.com/en/api/openai-sdk)
- [Implement tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use)（2026-11-15 確認: `tool_choice` の `auto` は「This is the default value when `tools` are provided」）
- [Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)（2026-11-15 確認: イベントの流れの最後は「A final `message_stop` event」）
- [Implement tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use) の「Forcing tool use」（2026-11-16 確認: 「Not every model and setting supports forced tool use」。対応しないモデルの一覧と、強制すると「the response starts with the `tool_use` block and has no `thinking` block」。`{"type": "none"}` は「prevents Claude from using any tools」）
- [Implement tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use)（2026-11-17 確認: 「`none` prevents Claude from using any tools」、ツールの強制を受け付けないモデルでも「`tool_choice: {"type": "auto"}` (the default) and `tool_choice: {"type": "none"}` still work」。U9）
- [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) の FAQ「How do I calculate total input tokens from the usage fields?」（2026-11-17 確認: 「`input_tokens` does NOT represent all input tokens, only the portion after your last cache breakpoint」「total_input_tokens = cache_read_input_tokens + cache_creation_input_tokens + input_tokens」。U14）
- [Models overview](https://platform.claude.com/docs/en/about-claude/models/overview)（2026-11-17 確認: Claude API ID に `claude-sonnet-5-5` と `claude-haiku-5-5` がある。U13）
- [Errors](https://platform.claude.com/docs/en/api/errors) の「Forced tool use not supported」（2026-11-16 確認: 上のエラーの文言と、`auto` と `none` は受け付けること）
- [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking) の「Thinking with tool use」（2026-11-17 確認: 強制は「incompatible with manual extended thinking」。6つのモデルで拒否する理由は、どの文書にも書かれていない）
- [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)（2026-11-17 確認: 「the tokens Claude spends reasoning are billed as output tokens, even when the thinking text isn't returned to you」）
- [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking) の「Thinking block preservation by model」（2026-11-17 確認: 「Don't strip thinking blocks when you switch. The API drops unreadable ones without billing them, and a model that reads them still gets them.」「Required: within a tool-use turn, pass thinking blocks back.」、アカウントに結びつくモデルの一覧）
- [Preserved thinking](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking) の「Switch models and back」（2026-11-17 確認: 「A thinking block that the new model or account can't use never causes an error. The API drops it for that request and doesn't bill it」「A dropped block doesn't count as a change to the conversation, so both models' blocks stay valid.」、モデルの組み合わせごとの一覧、preserved thinking のモデルは `thinking: {"type": "enabled"}` を 400 にする）
- [Prefix checks](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking/prefix-checks)（2026-11-17 確認: 「A prefix check fails if system, tools, or an earlier message changed after Claude produced a block. It also fails if a thinking block between others was left out or moved.」、400 になるかはモデルによる、比べないパラメータ、`prefix_mismatch_behavior`）
- [Fix: bound to a different conversation](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking/fix-bound-to-a-different-conversation)（2026-11-17 確認: 400 の文言）
- [PDF support](https://platform.claude.com/docs/en/build-with-claude/pdf-support)（2026-11-23 確認: base64 と URL の document の例、「Maximum request size: 128 MB」「Maximum pages per request: 600 (100 when the request's context window is under 1M tokens)」、「Plain text files such as .txt, .csv, or .md can be used directly in document blocks」「Binary formats such as .xlsx or .docx are not supported in document blocks」、ページの文字と画像のトークン、「Place PDFs before text in your requests」、例に beta のヘッダーがない）
- [Citations](https://platform.claude.com/docs/en/build-with-claude/citations)（2026-11-23 確認: `{"type": "text", "media_type": "text/plain", "data": ...}` の document の例、「`title` and `context` are optional fields」）

## 関連する未検証の前提

U8、U9、U14、U19、U24、U25、U30（[実 API 検証](../exec-plans/active/real-api-verification.md)）
