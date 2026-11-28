# 実 API 検証

## 目的

テストはすべてモック（各社の文書の形式を再現したもの）なので、実際の API で動くかはまだ確かめていない。下の「未検証の前提」をすべて「確認済み」か「修正済み」にしたら完了。

すべての機能の E2E のケースを、この計画の `--record` の1回にまとめる。機能ごとの前提は次のとおり。

| 前提 | 足したとき |
|---|---|
| U1〜U18 | 2026-11-17 までの調べ（基本・ツール・切り替え・usage） |
| U19〜U22 | 構造化出力（[モデルの層の機能を足す](../completed/2026-11-23-model-layer-features.md)） |
| U23〜U27 | 推論の強さと Anthropic の API の版（同上） |
| U28〜U31 | ファイルの入力（同上） |
| U32〜U35 | 推論の要約（[推論の要約](../completed/2026-11-26-reasoning-summary.md)） |
| U36〜U37 | Anthropic の `max_tokens` の既定（[載せ替えの負担を減らす](../completed/2026-11-28-model-switch-friction.md)） |
| U38 | 断られたエラーの案内（同上） |
| U39 | Gemini の URL の画像（同上） |
| U40〜U44 | クラウドの経路（[クラウドの API キー](cloud-api-keys.md)） |

## 手順

- [x] 下の前提を1回の実行で確かめられるようにする（2026-11-17。pytest の E2E テスト `e2e/`。[E2E テスト](../../e2e-tests.md)）
- [ ] 各社のキーで E2E テストを `--record` 付きで実行し、記録をコミットする。記録は、それ以降の `python -m pytest` で毎回再生される
  - **料金が発生するので、実行前にユーザーの確認を取る**
  - 呼び出しの回数は、まず `--collect-only` で確かめる（API は呼ばない）。モデルと料金の目安を伝えて了承を得てから実行する
  - 例: `python -m pytest e2e --models "anthropic:claude-sonnet-5-5,anthropic:claude-haiku-5-5,openai:gpt-5.5,gemini:gemini-flash-latest,deepseek:deepseek-flash,deepseek:deepseek-v4-pro,ollama:<model>,lmstudio:<model>"`
  - モデルの並べ方で、切り替えの組が決まる（隣どうしで A → B → A）
    - 同じプロバイダーの2つのモデルを隣に置くと、U8（Anthropic）と U17（DeepSeek）を確かめられる
    - Gemini の隣に別のプロバイダーを置くと、U5 を確かめられる。U5 の「Gemini 3 系以外」は、Gemini 2.5 などのモデルも並べたときだけ確かめられる
  - LM Studio に推論するモデル（`reasoning_content` を返すもの）を読み込むと、U11 の「ほかの互換サーバー」を確かめられる
  - クラウドの経路（U40〜U44）は、そのクラウドのアカウントとキーを用意できたものだけを並べる（例: `azure:<deployment>,azure-chat:<deployment>,foundry:claude-sonnet-5-5,bedrock:anthropic.claude-sonnet-5-5,bedrock:anthropic.claude-haiku-5-5,vertex:gemini-2.5-flash`）。URL を組み立てる環境変数も要る（[クラウドの経路](../../references/cloud-endpoints.md)）
    - `bedrock` の2つのモデルを隣に置くと、U43 の「別のモデルへ thinking ブロックを送り返す」を確かめられる
    - `vertex` の隣に別のプロバイダーを置くと、U44 の「ダミーの署名」を確かめられる
- [ ] ローカルの Ollama / LM Studio で同じ確認をする（料金なし）
- [ ] 失敗した箇所を直し、同じ形のレスポンスをモックのテストにも足す
- [ ] 下の前提を上から潰す。結果は各前提の「結果」に日付つきで書き、分かった仕様は `docs/references/` に書く
- [ ] [QUALITY_SCORE.md](../../QUALITY_SCORE.md) の「実 API」列を直す

## 未検証の前提

ID はほかの文書から参照しているので変えない。「文書で確認」は、各社の文書で確かめたが、実 API ではまだ確かめていないことを表す。

### U1 OpenAI Responses: output アイテムを input に戻す

- 実装: 同じモデルへは、output アイテム（id 付き）をそのまま input に戻す
- 不確実な理由: 既定の `store`（保存あり）なら問題ないはず。`store: false` では、id の参照が解決できずにエラーになるかもしれない
- 結果: 2026-11-17 に文書で確認
  - 手で状態を管理するときは、output アイテムをそのまま input に戻すのが文書の方法
  - `store: false` でも、reasoning アイテムに `encrypted_content` が既定で付き、`include` は要らない
  - `store: false` の例も output 配列を手を加えずに戻していて、id を外せとは書いていない
  - 実 API は未確認（E2E の `test_store_false`）

### U2 OpenAI Responses: assistant の過去の発言を文字列で送る

- 実装: `{"role": "assistant", "content": "文字列"}` で送る
- 不確実な理由: 文字列の content を assistant ロールで受け付けると理解しているが、実リクエストでは確かめていない
- 結果: 2026-11-17 に文書で確認。Conversation state の、過去の会話を手で組み立てる例が `{"role": "assistant", "content": "Who's there?"}` を input に入れている

### U3 OpenAI Responses: function ツールに `strict: false` を明示する

- 実装: `strict: false` を明示する
- 不確実な理由: strict の既定値に頼らないための措置。`true` にするなら、全プロパティを required にし、`additionalProperties: false` が要る

### U4 Gemini: 関数のスキーマを `parameters` で送る

- 実装: `parameters`（OpenAPI のサブセット）で送る
- 不確実な理由: `parametersJsonSchema` の方が JSON Schema をそのまま扱えるが、REST での挙動を一次情報で確かめられなかった。`schema_field` で切り替えられる

### U5 Gemini: ほかのプロバイダーの履歴にダミー署名を付ける

- 実装: ほかのプロバイダーの履歴を送るとき、最初の functionCall にダミー署名 `skip_thought_signature_validator` を付ける
- 不確実な理由: 公式は「最終手段で、性能が落ちる」と書く。Gemini 2.5 など Gemini 3 系以外に付けて害がないかは確かめていない。`inject_dummy_signatures=False` で止められる

### U6 Gemini: functionCall の id がないときの仮 ID

- 実装: functionCall に id がなければ仮 ID `switchllm_...` を振り、functionResponse には id を付けない
- 不確実な理由: Gemini 3 では id が常に返るという二次情報がある。古いモデルでの挙動は確かめていない

### U7 Gemini: generateContent を使う

- 実装: generateContent を使う
- 不確実な理由: Google は generateContent を「レガシー」と書き、新しい開発には Interactions API を勧めている。当面は動くはずだが、将来の移行の候補（[負債](../tech-debt-tracker.md)）

### U8 Anthropic: 別のモデルへも thinking ブロックを送る

- 実装: 別のモデルへ切り替えても、同じプロバイダーキーなら、受け取った thinking ブロックをそのまま送る
- 不確実な理由: 文書は「読めないブロックは、API がエラーにせず、料金もかけずに捨てる」とするが、実リクエストでは確かめていない。ツールの往復の途中で切り替えたときと、読めない組み合わせ（例: Sonnet 5.5 から Haiku 5.5）で確かめる
- 結果: 2026-11-17 に文書で確認し、モデルが違っても送るように直した（それまでは thinking を抜いて変換し直していた）

### U9 Anthropic: `tool_choice: "none"`

- 実装: `tool_choice: "none"` を `{"type": "none"}` にする
- 結果: 2026-11-17 に文書で確認。`none` は「prevents Claude from using any tools」。ツールの強制を受け付けないモデルでも、`auto` と `none` は使える

### U10 Chat Completions 互換: ストリーミングの usage

- 実装: ストリーミングで `stream_options: {include_usage: true}` を送る
- 不確実な理由: 拒否するサーバーがあるかもしれない。`Client(..., stream_usage=False)` で止められる

### U11 Chat Completions 互換（DeepSeek の形式）: `reasoning_content` を送り返す

- 実装: 同じプロバイダー・同じモデルが返した `reasoning_content` を、assistant メッセージに足して送り返す（ツールの有無にかかわらず）
- 不確実な理由: DeepSeek の文書どおりに直したが、実リクエストでは確かめていない。`reasoning_content` を返すほかの互換サーバー（LM Studio など）が、送り返したものを受け付けるかも分からない
- 結果: 2026-11-17 に文書で確認し、送り返すように直した。DeepSeek は、ツールを使うリクエストで送り返さないと 400 になる

### U12 プリセット: `ollama` / `lmstudio` の base_url

- 不確実な理由: 公式の文書では確かめた。実際の疎通は確かめていない
- 結果: 2026-11-09 に文書で確認

### U13 サンプルのモデル ID

- 実装: `gpt-5.5` / `claude-sonnet-5-5` / `gemini-flash-latest`
- 不確実な理由: モデル ID はよく変わる
- 結果: 2026-11-17 に文書で確認
  - `gpt-5.5`・`claude-sonnet-5-5`・`claude-haiku-5-5` は、各社のモデルのページにある
  - `gemini-flash-latest` は公式の latest エイリアスで、指す先を変える2週間前にメールで知らせるとある。今どのモデルを指すかは、文書にない

### U14 usage の合算

- 実装: Anthropic の `input_tokens` はキャッシュ分を含まず、Gemini の `candidatesTokenCount` は `thoughtsTokenCount` を含まない、という前提で合算する
- 不確実な理由: 調べたときの出典が記録されていなかった
- 結果: 2026-11-17 に文書で確認し、出典を記録した
  - Anthropic は「total_input_tokens = cache_read_input_tokens + cache_creation_input_tokens + input_tokens」と明記している
  - Gemini の `totalTokenCount` は「prompt + tool-use prompt + thoughts + response candidates」で、thoughts と candidates を別に数える。ただし、candidates が thoughts を含まないとは明記していない。E2E の `test_usage_breakdown` で、total と内訳の和を比べて確かめる

### U15 Ollama: `tool_choice`

- 実装: `tool_choice` は明示したときだけ送る
- 不確実な理由: Ollama の公式の文書は `tool_choice` を非対応と書いている。明示したときに、無視されるのか拒否されるのかは分からない
- 結果: 2026-11-15 に、指定がなければ送らないように変えた（[切り替えの精査](../completed/2026-11-15-switching-review.md) の S10）。明示したときの挙動は未確認
- 結果: 2026-11-28 に修正済み。対応表で `tool_choice` が未対応（`[ ]`）と確かめ、`"none"` と強制は送る前に `UnsupportedFeatureError`、`"auto"` は送らないようにした（[判断](../../design-docs/provider-capabilities.md)）。Ollama の振る舞いには頼らなくなった。E2E は、送る前に止まることを確かめる

### U16 ストリーミングの終わりの合図（Chat Completions 互換・Gemini）

- 実装: 終わりの合図が来ないまま閉じたら `StreamError` にする。合図は、Chat Completions は `finish_reason` か `[DONE]`、Gemini は最後のチャンクの `finishReason`
- 不確実な理由: OpenAI の文書は「`[DONE]` の前に usage のチャンクが来る」と書くだけで、常に `[DONE]` で終わるとは明記していない。Gemini と互換サーバーで、最後のチャンクに必ず合図が入るかは分からない。Anthropic の `message_stop` は文書で確認した

### U17 DeepSeek（思考モード・ツールあり）: ほかで作った履歴

- 実装: 別のプロバイダーや別のモデルの履歴、手で作ったメッセージは、`reasoning_content` を付けずに送る
- 不確実な理由: 文書は「送り返さないと 400」とだけ書き、ほかで作った履歴やモデルの切り替え（`deepseek-flash` → `deepseek-v4-pro`）の扱いを書いていない。400 になるなら、会話の途中で DeepSeek に切り替えられない

### U18 usage（DeepSeek）: 推論の分

- 実装: `completion_tokens` を、推論の分を含む出力トークン数として `output_tokens` に入れる
- 不確実な理由: `completion_tokens_details.reasoning_tokens` はあるが、`completion_tokens` がそれを含むかは文書にない

### U19 Anthropic（構造化出力）: 古い API の版

- 実装: `anthropic-version: 2023-06-01` のまま `output_config.format` を送る
- 不確実な理由: 文書の例は `2026-10-21` だけで、`2023-06-01` で受け付けるかは書いていない。版の約束は「新しい版で、省略できる入力を足すことがある」としか言っていない
- 結果: 2026-11-21 に既定の版を `2026-10-21`（文書の例と同じ版）に上げたので、既定では当たらない。`anthropic_version="2023-06-01"` を選んだときだけの前提として残す

### U20 Gemini（構造化出力）: `responseJsonSchema`

- 実装: `generationConfig` の `responseMimeType` と `responseJsonSchema` で送る
- 不確実な理由: 文書の SDK の例はこの形だが、REST の例は `responseFormat.text`。API リファレンスの `responseJsonSchema` の説明は、自分自身を指している

### U21 Ollama・LM Studio（構造化出力）

- 実装: OpenAI と同じ `response_format: {"type": "json_schema", ...}` を送る
- 不確実な理由: Ollama は対応表にチェックがあるだけで、例がない。LM Studio は例があるが、Chat Completions のページのパラメータの一覧にない。受け付けないときに黙って無視するかも分からない

### U22 OpenAI Responses（構造化出力）: ツールとの併用

- 実装: `text.format` と `tools` を同じリクエストで送る
- 不確実な理由: 文書は使い分けを書くだけで、併用できるかを書いていない

### U23 OpenAI（推論の強さ）: モデルが受け付けない段階

- 実装: `reasoning.effort`（Chat Completions は `reasoning_effort`）に段階をそのまま送る。モデルが受け付けない段階（gpt-5.5 の `minimal`・`max`）も送る
- 不確実な理由: 文書は、`none` を受け付けないモデルが 400 を返すとだけ書く。ほかの段階を送ったときに、400 か、黙って置き換えるかを書いていない。推論モデルで `temperature` を送れるかも書いていない

### U24 Anthropic（推論の強さ）

- 実装: `output_config.effort` を送る。`none` は `thinking: {"type": "disabled"}` にする
- 不確実な理由: effort に対応しないモデル（Haiku 4.5・Sonnet 4.5）に送ったときに、400 か無視かを書いていない。thinking を切ったリクエストで、前のターンの thinking ブロックを送り返してよいかも書いていない

### U25 Anthropic（API の版）: `2026-10-21`

- 実装: 既定の `anthropic-version` を `2026-10-21` にする
- 不確実な理由: 移行ガイドの破壊的な変更（`temperature` など）に当たるものは送らない。ただし、ツール・画像・ストリーミング・thinking の再送がすべて今までどおり通るかは、実リクエストでは確かめていない

### U26 Gemini（推論の強さ）: `thinkingLevel`

- 実装: `generationConfig.thinkingConfig.thinkingLevel` に小文字の段階を送る
- 不確実な理由: ガイドの例は小文字だが、API リファレンスの列挙は大文字。モデルが使えない段階（Gemini 4 の `minimal` など）や、Gemini 2.5 に送ったときの挙動も書いていない

### U27 Ollama・LM Studio（推論の強さ）

- 実装: `reasoning_effort` をそのまま送る
- 不確実な理由: Ollama は、知らない段階をモデルの既定に置き換える（エラーにしない）。LM Studio は Chat Completions のパラメータの一覧に `reasoning_effort` がなく、受け付けるか、無視するか、拒否するかが分からない

### U28 OpenAI Responses（ファイル）

- 実装: `input_file` に `file_data`（data URL）と、あれば `filename` を送る。種類は確かめずに送る
- 不確実な理由:
  - ガイドのコードの例は data URL だが、curl の例とスキーマは base64 とだけ書く
  - `filename` は省けるとされるが、例はいつも付けている
  - PDF 以外の種類を、拡張子と data URL のどちらで見分けるかが書いていない

### U29 Chat Completions 互換（ファイル）

- 実装: `{"type": "file", "file": {"file_data": data URL, "filename"}}` を送る。PDF 以外は、送る前に `UnsupportedFeatureError`
- 不確実な理由: `file_data` の形（data URL か、base64 だけか）は、例がなく書いていない

### U30 Anthropic（ファイル）

- 実装: PDF は base64 の document。テキスト（`text/*`・`application/json`・`application/xml`）は UTF-8 に戻し、`text/plain` の text の document で送る
- 不確実な理由: text の document の例は、引用の文書にある。Markdown・CSV・JSON を `text/plain` として送ってよいかは、PDF のガイドの「.txt, .csv, or .md can be used directly in document blocks」と Files API の説明から推した

### U31 Gemini（ファイル）

- 実装: `inlineData` に `mimeType` と base64 を送る。種類は確かめずに送る
- 不確実な理由: 今の文書の例は Interactions API の形で、generateContent で PDF を送る例がない（Part のスキーマの `mimeType` の例に `application/pdf` があることからの推定）。Office のファイルの扱いは、要約でしか読めていない

### U32 OpenAI Responses（推論の要約）

- 実装: `reasoning_summary=True` で `reasoning: {"summary": "auto"}` を送る
- 不確実な理由: 最新の推論モデルでは組織の認証が要ることがあり、認証していないときに、エラーになるか、要約が返らないだけかが書いていない。推論しないモデルに送ったときの挙動も書いていない

### U33 Anthropic（推論の要約）

- 実装: `thinking: {"type": "adaptive", "display": "summarized"}` を送る
- 不確実な理由:
  - `display` の既定が `omitted` のモデル（Claude 5 系・Opus 4.8・4.7）で、要約が返るか
  - 要約の入った thinking ブロックを、ツールの往復で手を加えずに送り返せるか
  - Opus 4.6・Sonnet 4.6 は考えるようになる。`temperature` と一緒に送れるかは、要約でしか読めていない

### U34 Gemini（推論の要約）

- 実装: `generationConfig.thinkingConfig.includeThoughts: true` を送る
- 不確実な理由: 「thoughts are returned only when available」なので、頼んでも返らないことがある。推論しないモデルに送るとエラーになる、とは API リファレンスの要約でしか読めていない

### U35 Ollama・LM Studio（推論の要約）

- 実装: 何も足さずに送り、返った `reasoning_content` か `reasoning` を `Response.thinking` に入れる
- 不確実な理由: 互換 API が推論のテキストを返すか、返すならどのフィールドかが、どちらの文書にも書いていない

### U36 Anthropic: モデルの上限を超える `max_tokens`

- 実装: `max_tokens=` の値を確かめずに送る。省いたときの 64000 は、提供中のモデルすべての上限（64K〜128K）に収まる（[判断](../../design-docs/anthropic-max-tokens.md)）
- 不確実な理由: 上限を超える値を送ったときに、400 になるか、切り詰めて受け付けるかが、どの文書にも書いていない。上限が 64000 より小さいモデル（提供を終えた Opus 4.1・Opus 4）で省いたときの動きもこれで決まる（E2E の `test_max_tokens_above_model_limit`）

### U37 Anthropic: ストリーミングなしで `max_tokens` 64000 を送る

- 実装: `max_tokens` を省いたら、`chat()`（ストリーミングなし）でも 64000 を送る
- 不確実な理由: 公式 SDK は、ストリーミングなしで 10 分を超えそうなリクエストを送る前に止める（`max_tokens` から見積もる）。API の側が送る前に断るとは書かれていないが、確かめていない（E2E の `test_max_tokens_omitted`）

### U38 すべて: 断られたエラーに、送った引数のフィールド名が出る

- 実装: API が 400 系で断ったら、送った共通の引数（強制する `tool_choice`・`reasoning_effort`・`reasoning_summary`・`response_schema`・`temperature`）の各社でのフィールド名が、エラーの文言か本文に出てくるかを見て、`str(e)` に案内を足す（[判断](../../design-docs/error-hints.md)）
- 不確実な理由:
  - Anthropic は、ツールの強制と thinking の設定の文言が文書にあり、フィールド名が入っている。ほかのエラー（`response_schema` など）の文言は書かれていない
  - OpenAI は `error.param` が引数を指すと文書にあるが、`reasoning.effort` を断ったときの本文の例はない
  - Gemini（generateContent）と DeepSeek は、エラーの形も文言も書かれていない。Gemini は snake_case と camelCase のどちらで名前を出すかも分からない
  - E2E は、断られたときに案内が付いた引数を記録する。Anthropic のツールの強制では、案内が付くことを確かめる（`test_forced_tool_choice`）

### U39 Gemini: URL の画像を `fileData.fileUri` で送る

- 実装: `ImagePart(url=...)` を `{"fileData": {"fileUri": URL, "mimeType": ...}}` で送る。URL は Gemini の API が取りに行く（[判断](../../design-docs/provider-capabilities.md)）
- 不確実な理由: 文書は「supported in all of the Gemini API endpoints」とするが、公開の URL を渡す例は Interactions API の形だけで、generateContent の例がない。Gemini 2.0 系は不可。E2E の `test_image_url` は、このリポジトリの `e2e/assets/red.png` を GitHub の raw の URL で読ませる

### U40 Azure OpenAI: Responses API を `api-key` ヘッダーで呼ぶ

- 実装: `azure:` は `AZURE_OPENAI_ENDPOINT` に `/openai/v1` を足した URL へ、`openai` アダプターの本文を送る。キーは `api-key` ヘッダー、`model` は deployment 名（[クラウドの経路](../../references/cloud-endpoints.md)）
- 不確実な理由: v1 の API に `api-key` で送る例はあるが、OpenAI と同じ本文（reasoning アイテムを戻す、`store: false`）で往復した例はない。エラーの本文の形も文書にない。E2E は `test_text`・`test_store_false`・`test_invalid_api_key`・`test_unknown_model`

### U41 Azure OpenAI: Chat Completions を `max_completion_tokens` で呼ぶ

- 実装: `azure-chat:` は同じ URL の `/chat/completions` へ送る。`max_tokens` を `max_completion_tokens` で送り、`reasoning_summary=True` は送る前に止める
- 不確実な理由: 推論モデルが `max_tokens` を受け付けないことは文書にあるが、v1 の Chat Completions の REST の文書が開けなかった（404）。`stream_options.include_usage` と PDF（`file` のパート）は文書にない。E2E は `test_text`・`test_stream_text`・`test_pdf_file`・エラーの2件

### U42 Claude in Microsoft Foundry: Claude API と同じ本文で呼ぶ

- 実装: `foundry:` は `https://<resource>.services.ai.azure.com/anthropic/v1/messages` へ、`anthropic` アダプターの本文と `x-api-key`・`anthropic-version: 2026-10-21` を送る
- 不確実な理由: Anthropic の文書の例は `2026-10-21`、Microsoft Learn の例は `2023-06-01` で、受け付ける版が書いていない。SSE のイベントの名前、テキストのファイル（`text` の source）、エラーの本文の形も文書にない。E2E は `test_text`・`test_stream_text`・`test_text_file`・エラーの2件と、`foundry` の2つのモデルの切り替え

### U43 Claude in Amazon Bedrock: bedrock-mantle の Messages API で呼ぶ

- 実装: `bedrock:` は `https://bedrock-mantle.<region>.api.aws/anthropic/v1/messages` へ、`anthropic` アダプターの本文と `x-api-key`・`anthropic-version: 2026-10-21` を送る。URL の画像は送る前に止める
- 不確実な理由: 文書は「the same request body shape as the Claude API」とするが、base64 の PDF・テキストのファイル・effort・adaptive thinking は書いていない。同じ経路の別のモデルへ thinking ブロックを送り返せるか（U8 と同じこと）も書いていない。E2E は `test_text`・`test_stream_text`・`test_pdf_file`・`test_text_file`・エラーの2件と、`bedrock` の2つのモデルの切り替え

### U44 Vertex AI express mode: Gemini API と同じ本文で呼ぶ

- 実装: `vertex:` は `https://aiplatform.googleapis.com/v1/publishers/google/models/<model>:generateContent` へ、`gemini` アダプターの本文と `x-goog-api-key` を送る。ストリーミングは `:streamGenerateContent?alt=sse`
- 不確実な理由: 文書に次のことがない
  - `?alt=sse` で SSE を返すか（返さなければ、ストリームの解析が要る）
  - エラーの本文の形
  - `thinkingLevel` を小文字で受け付けるか（文書の値は大文字）
  - `responseJsonSchema`（非推奨とされる）を受け付けるか、`parametersJsonSchema` を受け付けるか
  - express mode で公開の HTTPS の URL の画像を取りに行くか
  - ダミーの署名（U5 と同じこと）、`thoughtsTokenCount`
- E2E は `test_text`・`test_stream_text`・`test_reasoning_effort_low_stream`・`test_structured_output`・`test_image_url`・`test_parameters_json_schema`・`test_usage_breakdown`・エラーの2件と、`vertex` の隣の別のプロバイダーとの切り替え

## 進捗ログ

細かい経緯は、git の履歴（2026-11-17〜18 のコミット）にある。ここには、あとで効く気づきだけを残す。

- 2026-11-09: 旧 `docs/HANDOFF.md`（2026-11-08 時点）の「次にやること」と「未検証の前提」から作った
- 2026-11-16: API キーはどの環境変数のスコープにもなく、Ollama と LM Studio も起動していなかった。料金のかからない準備として、各社の文書で前提を調べた
- 2026-11-16: 当時の smoke test は `tool_choice="required"` を使っていて、Sonnet 5.5 や Opus 5.5 では 400 になる（これらのモデルはツールの強制を受け付けない）。E2E では、ツールの強制を使わず、`auto` のままプロンプトでツールを使うよう頼む
- 2026-11-17: DeepSeek に対応した（`a3c7068`）。DeepSeek の思考モードでは、`tool_choice` の `required` とツール名の指定が 400 になる
- 2026-11-17: U8 を直した（`81aeb9b`）。preserved thinking に対応するモデル（Opus 5.5・Sonnet 5.5・Haiku 5.5 など）は、送り返した thinking ブロックより前の内容が変わっていると 400 にすることがある。switchllm は同じ履歴から毎回同じリクエストを組み立てるので、`res.message` を加工しなければ当たらない（[Anthropic Messages](../../references/anthropic-messages.md) の「前置きの検査」）
- 2026-11-17: smoke test を pytest の E2E テスト（`e2e/`）に置き換えた（`9c8120f`）。偽のサーバーで、料金をかけずに流れを確かめた
- 2026-11-18: 実 API の記録と再生を足した（`340ef7b`）。`--record` で回すと、通ったテストのやりとりを `e2e/recordings/` に残す。`--models` がなければ記録を再生し、既定の `python -m pytest` にも入る。記録は sdist に入れない
- 2026-11-28: クラウドの経路の前提 U40〜U44 を足した（[クラウドの API キー](cloud-api-keys.md)）。クラウドの経路の結果は、その経路の U 番号にだけ数え、各社に直接つなぐときの前提（U25 など）には数えない（`e2e_support.CLOUD_ROUTES`）

## 判断ログ

- 2026-11-09: 公式の文書で確かめられていなかった互換クラウド API のプリセット（openrouter / groq / deepseek / xai / mistral / together）は、確かめるのではなく消した（[判断](../../design-docs/verified-presets.md)）
- 2026-11-17: 対応できるプロバイダーは、すべて実 API で試す（OpenAI・Anthropic・Gemini・DeepSeek・Ollama・LM Studio）（作者の判断）
- 2026-11-17: DeepSeek に対応する（作者の判断）。U11 を直し、ツールを使う会話で `reasoning_content` を送り返す
- 2026-11-17: Anthropic のモデルどうしで切り替えるときも、thinking ブロックを捨てずに送る（U8）
  - 作者の基準は「一般的に使われる機能なら対応する」
  - 費用での使い分け（難しい部分を大きいモデル、残りを小さいモデル）や、混雑・レート制限のときに別のモデルで続ける使い方は広く使われている。`with_model()` はこのライブラリの中心の機能で、Anthropic の文書もこの場面を名指しで扱っている
  - OpenAI と Gemini は、別のモデルへ送ってよいとする文書が見つからないので、同じモデルのときだけ送る
- 2026-11-17: モデルが受け付けない指定（Anthropic の一部のモデルでのツールの強制など）は、ライブラリの側で先回りして弾かず、黙って別の指定に替えもせず、API のエラーをそのまま返す（作者の判断）。モデルごとの制限は増え続け、一覧を持つとすぐに古くなるため
