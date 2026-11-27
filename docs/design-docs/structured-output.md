# 構造化出力は response_schema= で JSON Schema を受け取る

- 状態: 採用（2026-11-20、作者の判断）
- 検証: 一次情報で確認、実 API 未検証（U19〜U22。E2E の `test_e2e_structured.py`）
- コード: `client.py`（`response_schema=`、`Response.parsed` を入れる）、`providers/base.py`（`strict_object_schema`、`schema_name`）、4つのアダプターの `build_request`
- テスト: `tests/test_structured_output.py`
- 計画: [モデルの層の機能を足す](../exec-plans/completed/2026-11-23-model-layer-features.md) の 2

## 背景

LLM の答えをプログラムが受け取るとき（取り出し、分類、エージェントの途中のデータ）は、答えを JSON Schema の形に縛る指定（構造化出力）を使う。4社とも対応しているが、指定の場所も、スキーマの条件も違う（[各社の形式](../references/index.md) の「構造化出力」）。

- 指定の場所: OpenAI Responses は `text.format`、Chat Completions は `response_format`、Anthropic は `output_config.format`、Gemini は `generationConfig.responseJsonSchema`
- スキーマの条件:
  - OpenAI（strict）は、すべてのオブジェクトに `additionalProperties: false` を付け、すべてのプロパティを `required` に入れることを求める
  - Anthropic も、`additionalProperties: false` は必須
  - Gemini は、使えないキーワードを黙って無視する
- DeepSeek は JSON Schema を受け付けず、JSON モード（`json_object`）だけ

以前は「やらないこと」にしていたので（[判断](provider-specific-params.md)）、利用者は `extra_by_provider=` で各社の形式を書き分けるしかなかった。これでは、モデルを変えて試すたびに、呼び出し側の書き方も変わる。2026-11-18 に、機能を選ぶ基準（[基本方針](core-beliefs.md)）に照らして足すことにした。

## 決定

### 受け取る形と送る形

- `Client.chat()` / `stream()` に `response_schema=` を足す。受け取るのは JSON Schema の dict だけで、dict でなければ `TypeError`
  - 型ヒントや Pydantic のクラスからは作らない（[`@tool` の方針](tool-schema-scope.md) と同じ）。Pydantic を使う人は `Model.model_json_schema()` を渡す
- 各社へは次の形で送る。`extra=` で同じ場所を指定したら、`extra=` が勝つ（いつもの深いマージ）

| アダプター | 送る形 |
|---|---|
| `openai` | `text: {"format": {"type": "json_schema", "name", "schema", "strict": true}}` |
| `openai_chat` | `response_format: {"type": "json_schema", "json_schema": {"name", "schema", "strict": true}}` |
| `anthropic` | `output_config: {"format": {"type": "json_schema", "schema"}}` |
| `gemini` | `generationConfig` の `responseMimeType: "application/json"` と `responseJsonSchema` |

- OpenAI の `strict` は、常に `true` にする。`false` ではスキーマどおりの出力が保証されず、構造化出力を指定した意味がない
- OpenAI が必須とする `name` は、スキーマの `title` が名前の条件（英数字・`_`・`-` で 64 文字まで）に合えばそれを使い、合わなければ `"response"` にする。Pydantic のスキーマなら、クラス名が `title` に入っている

### スキーマには `additionalProperties: false` だけを足す

- `properties` を持つオブジェクトのうち、`additionalProperties` を書いていないものに足す
- `properties`・`items`・`prefixItems`・`anyOf`・`allOf`・`oneOf`・`$defs`・`definitions` の中もたどる
- 利用者の dict は書き換えず、写しに足す

理由と、足さないもの:

- OpenAI と Anthropic が必須とし、Gemini も受け付ける。プロパティを並べたオブジェクトに足しても、答えの形の意味はほぼ変わらない
- `properties` のないオブジェクト（キーを決めない dict）には足さない。足すと `{}` しか返せなくなり、黙って意味が変わる。OpenAI では 400 のままになり、気づける
- `additionalProperties` を書いてあるもの（`true` やスキーマ）は変えない
- `required` は補わない。省ける項目を必須にすると、意味が変わる
  - 省ける項目のあるスキーマは、OpenAI で 400 になる
  - OpenAI でも使うなら、`{"type": ["string", "null"]}` のように null との union にして `required` に入れる（OpenAI の文書の方法）
- 使えないキーワードも外さない。外すと、利用者の意図した制約が黙って消える。API のエラー（`BadRequestError`）か、Gemini では無視されることで分かる

### 結果は `Response.parsed` にも入れる

- `response_schema=` を指定し、`stop_reason` が `end` で、本文が JSON として読めたときだけ、読んだ値を入れる。それ以外は `None`
- 本文の JSON の文字列は、いつもどおり `Response.text` に残る
- 拒否（`content_filter`）や上限（`max_tokens`）で止まったときは、読めても入れない。本文が JSON でないか、スキーマに合わない途中までのものだから
- `stream()` の `text` イベントは JSON の断片で、最後の `done` の `response.parsed` は `chat()` と同じになる（不変条件 2）
- スキーマに合っているかは確かめない（JSON Schema の検証には依存が要る）。各社が出力をスキーマに縛る。値の意味まで確かめたいなら、利用者が Pydantic などで確かめる
- 読む処理は、アダプターではなく `Client` に置く。4社とも、HTTP の応答では JSON を文字列で返すので、アダプターごとに分ける理由がない

### 対応しない接続先と、ほかの機能との組み合わせ

- **JSON Schema に対応しない接続先では、送る前に `UnsupportedFeatureError` にする**（不変条件 4）
  - `openai_chat` のオプション `json_schema` を `False` にした接続先が対象。`deepseek` のプリセットは `False` にしてある
  - 例外のメッセージで、JSON モード（`extra={"response_format": {"type": "json_object"}}`）を案内する
  - Ollama・LM Studio と、`register_provider()` で足したサーバーには、OpenAI と同じ形で送る（U21）。受け付けないサーバーを登録するときは `json_schema=False` を渡す
- ツール・ストリーミング・推論との組み合わせは、先回りして弾かない。受け付けないモデルは、API のエラーをそのまま返す（[実 API 検証](../exec-plans/active/real-api-verification.md) の 2026-11-17 の判断と同じ）。Anthropic と Gemini 3 以降はツールと一緒に使えると文書にあり、OpenAI は書いていない（U22）
- スキーマのない JSON モードは、共通化しない。形を保証せず、OpenAI と DeepSeek はプロンプトに「JSON」の語を入れることを求めるので、同じ書き方で同じ結果にならない。使う人は `extra=` で各社の形式を渡す

## 却下した案

- **引数の名前を `output_schema=` にする**: Google ADK の名前と同じだが、`response_schema` のほうが「応答の形」と読める。Gemini のフィールド名（`responseJsonSchema`）にも近い
- **`response_format=` で OpenAI の形（`{"type": "json_schema", "json_schema": {...}}`）を受け取る**: LiteLLM などと同じ書き方になるが、利用者に OpenAI の包みと `name` を書かせることになる。共通型を OpenAI の形式にしない方針（[判断](common-types.md)）にも合わない
- **スキーマをそのまま送る**: いちばん薄い。ただ、Gemini で通ったスキーマが、OpenAI と Anthropic では `additionalProperties` がないだけで 400 になる。切り替えるたびにスキーマを書き直すことになり、機能を足した目的に反する
- **各社の SDK のように変換する**（省ける項目を null との union にして `required` に入れる、使えないキーワードを外して説明文に移す）: どこでも通りやすくなるが、送る形が利用者のスキーマとずれ、ライブラリも厚くなる
- **`r.json()` メソッドにして、読めなければ例外にする**: 呼ぶたびに読み直し、拒否のときの扱いを例外の捕捉で書かせることになる。各社の SDK（`output_parsed`、`parsed_output`）も属性で返している
- **`r.text` だけを返す**: いちばん薄いが、構造化出力を使う人は必ず `json.loads()` を書き、拒否や途中で切れたときの扱いも自分で書く
- **DeepSeek では JSON モードで代わりにする**（`json_object` を送り、スキーマを system プロンプトに書き足す）: 切り替えは楽になるが、形は保証されず、利用者のプロンプトを黙って書き換える
- **DeepSeek にも `json_schema` を送り、API のエラーを返す**: アダプターに分岐が要らない。ただ、文書で非対応と分かっているものを送って失敗させることになる。エラーの文言も DeepSeek 次第で、JSON モードを案内できない

## 影響

- `Response` に `parsed` が増える（公開 API の型の属性。`__all__` は変わらない）
- `openai_chat` のオプションに `json_schema` が増える（[公開 API](../product-specs/public-api.md) のオプション表）
- Anthropic の古い API の版（`2023-06-01`）で `output_config` を受け付けるかは、文書にない（U19）。2026-11-21 に既定の版を `2026-10-21`（文書の例と同じ版）に上げたので、古い版を選んだときだけの前提になった（[判断](anthropic-api-version.md)）
- Gemini は、文書の SDK の例の形（`responseMimeType` と `responseJsonSchema`）で送る。REST の例は別の形（`responseFormat.text`）で、どちらを受け付けるかは U20 で確かめる
- 省ける項目のあるスキーマ（Pydantic で既定値を持つフィールドなど）は、OpenAI では 400 になる。README の「制限」に書く
