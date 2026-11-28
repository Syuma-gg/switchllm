# switchllm

[English](https://github.com/Syuma-gg/switchllm/blob/main/README.md) | 日本語

OpenAI / Anthropic / Gemini / OpenAI互換サーバー（Ollama・LM Studio・vLLM など）を、**同じコードで呼べる**軽量なクライアントライブラリ。

```python
from switchllm import Client

client = Client("anthropic:claude-sonnet-5-5")   # ← ここだけ変えればプロバイダーが変わる
print(client.chat("こんにちは").text)
```

- 依存は `httpx` だけ。各社の SDK は使わず、HTTP リクエストの形式差をアダプターで吸収する
- ツール呼び出しの往復に必要な署名・推論情報（Gemini の thoughtSignature、Anthropic の thinking、OpenAI の reasoning、DeepSeek の reasoning_content）を落とさない
- エージェントフレームワークではなく、その下で使う薄い層（[やること・やらないこと](https://github.com/Syuma-gg/switchllm/blob/main/docs/product-specs/scope.md)）
- プロバイダーは文字列で選ぶ。失敗したときに別のプロバイダーへ自動で切り替える機能（フォールバック、ルーティング）はない。必要なら `RateLimitError` などを捕まえて `client.with_model()` で切り替える

---

## インストール

```shell
pip install switchllm
```

Python 3.10 以上が必要。ランタイムの依存は `httpx` だけ。

APIキーは環境変数で渡す（`api_key=` 引数でも可）。

| プロバイダー | モデル文字列の例 | 環境変数 |
|---|---|---|
| OpenAI（Responses API） | `openai:gpt-5.5` | `OPENAI_API_KEY` |
| Anthropic | `anthropic:claude-sonnet-5-5` | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini:gemini-flash-latest` | `GEMINI_API_KEY`（または `GOOGLE_API_KEY`） |
| OpenAI（Chat Completions） | `openai-chat:<model>` | `OPENAI_API_KEY` |
| DeepSeek | `deepseek:deepseek-flash` | `DEEPSEEK_API_KEY` |
| Ollama / LM Studio（ローカル） | `ollama:llama3.2` / `lmstudio:<model>` | 不要 |
| その他の OpenAI 互換 API | `register_provider()` で登録する（下の「その他の OpenAI 互換 API」） | 登録時に指定 |

モデル ID は頻繁に変わるので、各社のモデル一覧で確認してから指定する。

## 切り替え方は2通り

1. **コードの1か所**: `Client("openai:gpt-5.5")` の文字列を変える
2. **コードを変えない**: `Client()` と書いておき、環境変数 `SWITCHLLM_MODEL` で指定する

```shell
# macOS / Linux
export SWITCHLLM_MODEL="gemini:gemini-flash-latest"
# Windows（PowerShell）
$env:SWITCHLLM_MODEL = "gemini:gemini-flash-latest"

python examples/agent_loop.py
```

モデル文字列は最初の `:` で区切るので、`ollama:llama3.2:3b` のようにモデル名にコロンを含んでもよい。

ほとんどのコードは、ほかに直すところがない。接続先によって違う少しの引数と、載せ替えてもコードを直さずに済む書き方は、下の「コードを直さずにモデルを載せ替える」にある。

## 使い方

### テキスト・ストリーミング

```python
res = client.chat("日本の首都は？", system="一言で答える", max_tokens=100)
res.text          # 本文
res.stop_reason   # "end" | "tool_use" | "max_tokens" | "content_filter" | "other"
res.usage         # input_tokens / output_tokens / total_tokens / details（生の内訳）
res.thinking      # 推論テキスト（返すプロバイダーのみ）

for ev in client.stream("俳句を詠んで"):
    if ev.type == "text":
        print(ev.text, end="")
    elif ev.type == "done":
        final = ev.response   # ツール呼び出しも組み立て済みの Response
```

### ツール呼び出し（エージェントループ）

```python
from typing import Annotated, Literal
from switchllm import Client, Message, run_tool_calls, tool

@tool
def get_weather(city: Annotated[str, "都市名"], unit: Literal["c", "f"] = "c") -> dict:
    """都市の現在の天気を返す。"""
    return {"city": city, "weather": "晴れ"}

messages = [Message.user("東京と大阪の天気は？")]
while True:
    res = client.chat(messages, tools=[get_weather])
    messages.append(res.message)          # 加工せずそのまま積む（署名などの生データが入っている）
    if res.stop_reason != "tool_use":
        break
    messages.extend(run_tool_calls([get_weather], res.tool_calls))
print(res.text)
```

- `@tool` は型ヒントと docstring（最初の段落）から、ツール定義の JSON Schema を作る
- 自動で変換できる型は `str` / `int` / `float` / `bool` / `list[T]`（`tuple`・`set` も可）/ `dict` / `Literal` / `Enum` / `Optional`・`Union` / `Annotated[T, "説明"]`。型ヒントのない引数は文字列として扱う
- それ以外の型（dataclass、Pydantic モデルなど）を使うと、デコレートした時点で `TypeError` になる。その場合は `parameters=` で JSON Schema を直接渡す

```python
@tool(parameters={
    "type": "object",
    "properties": {"order": {"type": "object", "properties": {"item": {"type": "string"}, "qty": {"type": "integer"}}}},
    "required": ["order"],
})
def place_order(order: dict) -> str:
    """注文を確定する。"""
    return f"{order['item']} を {order['qty']} 個注文しました"
```

- ツール定義は dict（`{"name", "description", "parameters"}`）でも渡せる
- `tool_choice`: `"auto"`（既定）/ `"none"` / `"required"` / `{"name": "get_weather"}`
  - 指定しなければ送らない（各社とも既定は auto）
  - `tools` にない名前を指定すると、送る前に `ConfigurationError`
  - `"required"` と名前の指定を受け付けないモデルもある（Anthropic の Opus 5.5・Sonnet 5.5 など）。そのときは API のエラーを `BadRequestError` で返し（エラーに `tool_choice` が出てくれば案内を付ける。下の「例外」）、黙って `"auto"` に替えることはしない
  - Ollama には `tool_choice` がない。`"none"` と強制は送る前に `UnsupportedFeatureError` になり、`"auto"` は送らないだけ
- 並列ツール呼び出しにも対応（Anthropic・Gemini では結果を1メッセージにまとめて送る）
- `run_tool_calls` は、モデルが決めた引数で関数を実行する。危険な操作をする関数では引数を検証すること（[SECURITY.md](https://github.com/Syuma-gg/switchllm/blob/main/docs/SECURITY.md)）

### 非同期

`AsyncClient` は、引数とメソッドの名前が `Client` と同じ。`chat()` は `await` し、`stream()` は `await` を付けずに `async for` で回す。

```python
import asyncio
from switchllm import AsyncClient

async def main():
    async with AsyncClient("anthropic:claude-sonnet-5-5") as client:
        res = await client.chat("日本の首都は？")
        async for ev in client.stream("俳句を書いて"):
            if ev.type == "text":
                print(ev.text, end="")

asyncio.run(main())
```

- リクエストを並行に送れる（`asyncio.gather(client.chat(a), client.chat(b))`）。リトライの待ち時間も、イベントループを止めない
- 自分の接続を使うときは `http_client=httpx.AsyncClient(...)` を渡す。`with_model()` で作ったクライアントは、この接続を共有する。閉じるのは `await client.aclose()` か `async with`
- ストリームは、最後まで読めば接続を閉じる。途中で `break` してすぐに閉じたいときは、`async with contextlib.aclosing(client.stream(...)) as events:` で囲む
- `await arun_tool_calls(tools, res.tool_calls)` は、`async def` のツールを await し、普通の関数はそのまま呼ぶ。呼び出しを並行に実行し、結果は呼び出しの順に返す。`run_tool_calls` は `async def` のツールを実行せず、`arun_tool_calls` を使うよう、エラーの結果を返す

### 履歴の保存と復元

```python
import json

saved = json.dumps([m.to_dict() for m in messages], ensure_ascii=False)   # ファイルやデータベースに保存する

messages = json.loads(saved)                           # chat() は dict のまま受け取る
res = client.chat(messages + [{"role": "user", "content": "明日は？"}], tools=[get_weather])
```

- `to_dict()` は生データ（`provider_state`）も残すので、復元した会話でも署名や推論を落とさずに送り返せる。残さないと、復元したツールの会話が失敗したり（DeepSeek は 400 を返す）、モデルの推論が引き継がれなかったりする
- 1つの dict を `Message` に戻すときは `Message.from_dict()`
- 読み込むのは自分で保存した履歴だけにする。生データはそのままプロバイダーに送られる

### 構造化出力

```python
schema = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
    "required": ["name", "age"],
}
res = client.chat("タロウは20歳です。", response_schema=schema)
res.parsed   # {"name": "タロウ", "age": 20}
res.text     # 同じ JSON の文字列
```

- `response_schema=` に JSON Schema の dict を渡すと、各社の形式（OpenAI `text.format`、Anthropic `output_config.format`、Gemini `responseJsonSchema`、Chat Completions `response_format`）で送り、答えをその形に縛る。Pydantic なら `Model.model_json_schema()` を渡す
- `res.parsed` が入るのは、モデルが最後まで答え（`stop_reason == "end"`）、本文が JSON として読めたときだけ。拒否や上限で止まったときは `None` なので、`res.stop_reason` と `res.text` を見る
- `properties` を並べたオブジェクトには `additionalProperties: false` を足して送る（OpenAI と Anthropic が必須とする）。スキーマのほかの部分は変えない
- `stream()` では、`text` イベントが JSON の断片になり、最後の `done` の Response に `parsed` が入る
- DeepSeek は JSON モードだけなので、`response_schema=` を指定すると送る前に `UnsupportedFeatureError`。`extra={"response_format": {"type": "json_object"}}` を渡し、形をプロンプトで伝える（[詳細](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/structured-output.md)）

### 推論の強さ

```python
res = client.chat("「strawberry」に r はいくつある？", reasoning_effort="low")
```

- `reasoning_effort=` には `"none"`・`"minimal"`・`"low"`・`"medium"`・`"high"`・`"xhigh"`・`"max"` のどれか（OpenAI が文書に挙げる段階）を渡す。各社の指定（OpenAI `reasoning.effort`、Anthropic `output_config.effort`、Gemini `thinkingConfig.thinkingLevel`、Chat Completions `reasoning_effort`）に、名前を変えずに入れる。指定しなければモデルの既定の段階になる
- 各社の API に名前のない段階は、送る前に `UnsupportedFeatureError`。Anthropic の `"minimal"` と、Gemini の `"none"`・`"max"` が当たる。Anthropic の `"none"` は thinking を切る（`thinking: {"type": "disabled"}`）
- 使える段階はモデルによって違う（gpt-5.5 に `"max"` はなく、Claude Opus 5.5 は推論を切れない）。ライブラリはモデルの一覧を持たないので、API の `BadRequestError` で返る
- 段階は目安で、トークンの予算ではない。同じ段階でも、モデルによって考える量は違う。ほかの推論の指定（Gemini 2.5 の `thinkingBudget` など）は `extra_by_provider=` に書く（[詳細](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/reasoning-effort.md)）

### 推論の要約

推論するモデルの多くは、頼まなければ推論の本文を返さない。`reasoning_summary=True` を渡すと頼める。

```python
res = client.chat("1 から 1000 までの整数のうち、各桁の和が 10 になるものはいくつ？", reasoning_summary=True)
print(res.thinking)  # 推論の要約。モデルが返さなければ None
```

- 各社の指定に置き換えて送る（OpenAI `reasoning.summary: "auto"`、Anthropic `thinking: {"type": "adaptive", "display": "summarized"}`、Gemini `thinkingConfig.includeThoughts: true`）。Chat Completions には標準の指定がないので何も足さない。DeepSeek は頼まなくても推論を返し、Ollama・LM Studio は返せばそれを入れる。`stream()` では `thinking` イベントで届く
- 既定は `False`
  - Anthropic と Gemini では、要約そのものに料金はかからない（どちらでも推論の全体の分がかかる）。Anthropic では待ち時間が少し増える
  - Claude Opus 4.6〜4.8 と Sonnet 4.6 は、頼まなければ考えない。`adaptive` で考えるようになり、その分の料金が増える
  - Claude 4.5 系は `adaptive` を 400 にするので、`extra={"thinking": {"type": "enabled", "budget_tokens": 2048}}` も一緒に渡す
  - OpenAI は、最新の推論モデルで、要約に組織の認証が要ることがある
- 要約を返すかはモデルが決めるので、`res.thinking` が `None` のこともある
- 渡せるのは `True` か `False` だけ。OpenAI の `"concise"`・`"detailed"` は `extra_by_provider=` で渡す
- `reasoning_effort="none"` と一緒に渡すと、送る前に `ConfigurationError`。OpenAI に向けた `openai-chat:` は、Chat Completions が推論の本文を返さないので `UnsupportedFeatureError`。`openai:` を使う（[詳細](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/reasoning-summary.md)）

### ファイル（PDF など）

`FilePart` を user のメッセージに入れる。質問より先に置く（Anthropic は PDF を先に置くよう勧める）。

```python
from switchllm import Client, FilePart, Message, TextPart

client = Client("anthropic:claude-sonnet-5-5")
res = client.chat([Message.user([FilePart.from_file("report.pdf"), TextPart("3行で要約して")])])
```

- PDF は、`deepseek`・`ollama`・`lmstudio` のプリセットを除くすべてのプロバイダーで送れる
- ほかの種類は、プロバイダーによって違う。OpenAI（Responses API）と Gemini には種類を確かめずに送り、受け付けない種類は API が拒否する。Anthropic は PDF とテキスト（テキストのファイルは文字として送る）。Chat Completions は PDF だけ
- 受け付けないと分かっているファイルは、送る前に `UnsupportedFeatureError`。ファイルは base64 でリクエストに入れて送る（ファイルの URL やアップロードの API は使わない）。画像は `ImagePart` で送る。音声と動画は未対応（[判断](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/file-input.md)）

### プロバイダー固有の機能

共通化できないパラメータは、各社の形式のまま `extra_by_provider=` に並べて書く。送るのはモデル文字列で選んだプロバイダーの分だけなので、モデルを切り替えても呼び出し側のコードは変わらない。書いていないプロバイダーには何も足さない。キーはモデル文字列の `provider:` 部分（`google:` で指定するなら `"google"`）。

```python
client.chat("...", extra_by_provider={
    "openai": {"reasoning": {"summary": "auto"}},      # OpenAI Responses: 推論の要約を返させる
    "anthropic": {"metadata": {"user_id": "u-1"}},     # Anthropic
})
```

`extra=` は、どのプロバイダーにも同じものをリクエスト本体にそのままマージする。両方を指定すると、`extra_by_provider` の分が優先される。

1つの接続先だけが読む `Client` のオプション（Anthropic の `default_max_tokens` など）は、同じ形で `options_by_provider=` に並べる。使うのは選んだ接続先の分だけなので、モデル文字列を設定から読んでも同じコードで動く。どの接続先の分も `Client` を作るときに検査するので、打ち間違いには、その接続先へ切り替える前に気づける。

```python
client = Client(options_by_provider={   # モデルは $SWITCHLLM_MODEL から読む
    "anthropic": {"default_max_tokens": 32000},
    "gemini": {"schema_field": "parametersJsonSchema"},
})
```

`client.with_model("anthropic:claude-haiku-5-5")` で、接続を共有したまま別のモデルのクライアントを作れる（フォールバックや比較に使う）。同じプロバイダーなら `api_key` / `base_url` / オプションを引き継ぎ、別のプロバイダーへは `options_by_provider=` だけを引き継ぐ（それぞれ自分の分だけを使う。キーや認証ヘッダーを別のサーバーへ送らないため）。

`headers=` で、どのプロバイダーにも HTTP ヘッダーを足せる（API の前にゲートウェイを置くときなど）。

- ライブラリが付けるヘッダーと同じ名前なら、APIキーのヘッダー（`Authorization`、`x-api-key`、`x-goog-api-key`）も含めて置き換える。名前は大文字と小文字を区別せずに比べる
- APIキーのヘッダーをこれで渡せば、`api_key=` も環境変数も要らない
- ライブラリが付けるヘッダーを止めるときは、値に `None` を渡す。たとえば、ゲートウェイがキーを別の名前のヘッダーで受けるときは、ライブラリのAPIキーのヘッダーを `None` にすれば、プロバイダーのキーをゲートウェイへ送らない
- Anthropic の API の版と beta 機能は、`anthropic_version=` と `beta=` で指定する

```python
Client("anthropic:claude-sonnet-5-5", base_url="https://gateway.example.com",
       headers={"x-api-key": "<ゲートウェイのキー>", "X-Team": "search"})
# ゲートウェイがキーを Authorization で受ける。x-api-key は送らない
Client("anthropic:claude-sonnet-5-5", base_url="https://gateway.example.com",
       headers={"Authorization": "Bearer <ゲートウェイのキー>", "x-api-key": None})
```

### 例外

どのプロバイダーでも同じ例外クラスになる: `AuthenticationError`(401/403) / `RateLimitError`(429) / `BadRequestError`(400系) / `NotFoundError`(404) / `ServerError`(5xx) / `StreamError` / `APIConnectionError` / `ConfigurationError` / `UnsupportedFeatureError`。

プロバイダーが返したエラーは、手を加えずに渡す。`e.message` は API が返した文言そのもの、`e.body` は本文の全体。

モデルによって断られる指定がある（強制する `tool_choice`、`reasoning_effort` の段階、`reasoning_summary=True`、`response_schema` の一部、`temperature`）。API がこれらを 400 系で断り、エラーにその名前が出てくると、`str(e)` に `Hint (...)` の行が付く。どの引数が原因かと、どのモデルでも動く書き方を示す。モデル名ではなく、送ったものとエラーの中身で判断する（[詳細](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/error-hints.md)）。

```python
from switchllm import APIError

try:
    client.chat("...", tools=[get_weather], tool_choice="required")
except APIError as e:
    print(e)          # [anthropic] tool_choice: type "tool" and "any" are not supported for this model. (HTTP 400)
                      # Hint (tool_choice): this model may not accept a forced tool_choice ('required' or a tool name). ...
    print(e.message)  # API の文言だけ
    print(e.body)     # {"type": "error", "error": {...}, "request_id": "req_..."}
```

そのプロバイダーのどのモデルも拒否する指定は、例外にせずに送らずに捨て、`DroppedParameterWarning` で知らせる（今は Anthropic の `temperature` だけ）。送る前に止めたいときは、`warnings.filterwarnings("error", category=DroppedParameterWarning)` で例外にする。

429・5xx・接続エラーは指数バックオフで自動リトライする（`max_retries=2`、`Retry-After` ヘッダーを尊重）。ストリーミングは最初のイベントを受け取る前だけリトライする。詳しくは [RELIABILITY.md](https://github.com/Syuma-gg/switchllm/blob/main/docs/RELIABILITY.md)。

### その他の OpenAI 互換 API

プリセットにないサーバー（クラウドの互換 API、自前の vLLM、社内ゲートウェイなど）は、名前を付けて登録する。base_url は各サービスの公式ドキュメントで確認すること。

```python
from switchllm import Client, register_provider

register_provider("mycloud", base_url="https://api.example.com/v1", api_key_env="MYCLOUD_API_KEY")
register_provider("myvllm", base_url="http://gpu-box:8000/v1", api_key_env=())   # キー不要
client = Client("myvllm:Qwen/Qwen3-8B")
```

登録せずに `Client("openai-chat:<model>", base_url="...", api_key="...")` と直接指定してもよい。`openai-chat` は、接続先が OpenAI 本家のときだけ max_tokens を `max_completion_tokens` として送り（OpenAI は `max_tokens` を非推奨にしている）、それ以外のサーバーには `max_tokens` で送る。

## コードを直さずにモデルを載せ替える

ほとんどのコードは、モデルの文字列を変えるだけでそのまま動く。ただし、接続先によって違う引数がいくつかあり、あるモデルが受け付ける指定を別のモデルが断ることもある。ライブラリはモデルの一覧を持たないので、送る前に止めるのは、その接続先のどのモデルにもないものだけで、ほかは API のエラーで返る。次のように書いておけば、載せ替えてもコードを直さずに済む。

- **接続先ごとの指定は並べて書く。** 1つの接続先だけが読むリクエストの引数は `extra_by_provider=` に、`Client` のオプションは `options_by_provider=` に書く。使うのは選んだ接続先の分だけ。`extra=` と `**options` はどの接続先にも渡るので、どこでも受け付けるものだけにする
- **`max_tokens` は省くか、推論の分を空けて指定する。** 省くと、各モデルの上限まで出す（必須の Anthropic には 64000 を送る）。長さや料金を抑えるために指定するなら、OpenAI・Gemini・Claude 5 系は推論のトークンもここに含めることを考えに入れる。上限で切れた答えは `stop_reason == "max_tokens"` で、本文がないこともある
- **`temperature` と `stop` に頼らない。** `temperature` は、Anthropic では送らずに捨てて `DroppedParameterWarning` を出し、Gemini 3.6 以降と DeepSeek の思考モードはエラーにせずに無視する。`stop` は OpenAI Responses で `UnsupportedFeatureError` になる。長さや形はプロンプトで伝え（または `response_schema=` を使い）、切りたいところは受け取った本文を自分で切る
- **画像は base64 で渡す。** `ImagePart.from_file()` と `ImagePart(data=..., media_type=...)` は、どのプリセットでも送れる。URL の画像は各社の API が取りに行き、Ollama は URL を受け付けない
- **ファイルは PDF かテキストで送る。** PDF は `deepseek`・`ollama`・`lmstudio` のプリセットを除いて送れる。ほかのテキストのファイル（CSV など）は、読み込んだ文字を `TextPart` に入れれば、どこでも送れる
- **`reasoning_effort` は `"low"`・`"medium"`・`"high"` を使う。** 推論するモデルの多くが受け付ける段階。`"none"`・`"minimal"`・`"xhigh"`・`"max"` は、接続先によってはなく（送る前に `UnsupportedFeatureError`）、モデルによってもない（`BadRequestError`）。Gemini 2.5 は段階を受け付けないので、`thinkingBudget` を `extra_by_provider=` に書く
- **`tool_choice` は指定しない。** どこでも既定は `"auto"`。強制（`"required"` やツールの名前）は、Claude 5 系のいくつかのモデル、DeepSeek の思考モード、Ollama が断る。使ってほしいツールはプロンプトで頼み、ツールを呼ばせたくなければ `tools` を渡さない
- **`res.thinking` は、ないこともあるものとして扱う。** `reasoning_summary=True` を渡しても、要約を返すかはモデルが決める。OpenAI に向けた `openai-chat:` では `UnsupportedFeatureError` になり、Claude 4.5 系は 400 で断る
- **構造化出力では、すべてのプロパティを `required` に入れる。** 省ける項目は null も受け付ける形にする（OpenAI が求める）。DeepSeek には JSON Schema がないので、`response_schema=` は `UnsupportedFeatureError` になる（上の「構造化出力」）
- **会話の途中の載せ替え**は、同じ `messages` のままでよい。生データは同じモデルにだけ送り返し（Anthropic はどの Claude のモデルにも送る）、ほかのモデルには履歴を変換し直して送る。載せ替え先が受け取れないもの（Ollama なら画像の URL や PDF など）が履歴にあると、送る前に `UnsupportedFeatureError` になる

プリセットごとに送れるものと、送る前に止まるものは、[public-api.md](https://github.com/Syuma-gg/switchllm/blob/main/docs/product-specs/public-api.md) の表にある。モデルが指定を断ったときは、`str(e)` に、原因の引数と、どのモデルでも動く書き方が付く（上の「例外」）。捨てられた指定を自分のテストで見つけるには、`DroppedParameterWarning` を例外にする。

---

## 設計の要点

詳しい理由は [設計判断の一覧](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/index.md) にある。

- **OpenAI 本家は Responses API、互換サーバーは Chat Completions。** GPT-5.4 以降、Chat Completions では推論とツール呼び出しを両立できないため（[詳細](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/openai-responses-api.md)）
- **生データを `provider_state` に退避し、同じモデルへはそのまま返す。** 署名や推論アイテムを落とすと、ツールの往復がエラーになるため。別のモデルへは共通型から変換し直す。例外は Anthropic で、Anthropic の文書が求めるとおり、別の Claude のモデルへも thinking ブロックをそのまま送る。新しいモデルが読めないものは、API がエラーにせず、料金もかけずに捨てる（[詳細](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/provider-state-resend.md)）
- **主要3社はネイティブ API を使う。** 各社の OpenAI 互換エンドポイントは、キャッシュや thinking などの機能が制限されるため（[詳細](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/native-apis.md)）
- **`usage` の意味を揃える。** `input_tokens` はキャッシュを含む入力の総数、`output_tokens` は推論を含む出力の総数（[詳細](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/usage-normalization.md)）
- **SDK を使わず HTTP を直接呼ぶ。** 依存を httpx だけにし、形式の差をコード上で見えるようにするため（[詳細](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/http-without-sdk.md)）

## 既知の制限

- **実 API ではまだ検証していない**。テストは各社ドキュメントの形式を再現したモックに対するもの。`python -m pytest e2e --models "anthropic:claude-sonnet-5-5,openai:gpt-5.5,gemini:gemini-flash-latest"` で、手元のキーを使って疎通を確かめてほしい（少額の料金が発生する。先に `--collect-only` を付けると、API を呼ばずに呼び出しの回数だけを表示する）
- 画像とファイルの入力は対応（ファイルは base64 だけ）、音声入力は未対応。URL の画像は各社の API が取りに行き、ライブラリは取りに行かない。Ollama は base64 の画像だけ。プリセットごとに送れるものと、送る前に止まるものは [public-api.md](https://github.com/Syuma-gg/switchllm/blob/main/docs/product-specs/public-api.md) の表にある
- 推論の本文: OpenAI・Claude 5 系・Gemini は、頼まなければ推論の要約を返さないので、`res.thinking` は `None` のことが多い。`reasoning_summary=True` を渡すと頼める（上の「推論の要約」）。頼んでも、返すかはモデルが決める
- Anthropic: 既定の API の版は `2026-10-21` で、どのモデルでも `temperature` を拒否する。そのため `temperature=` は送らずに捨て、`DroppedParameterWarning` で知らせる（リクエストの残りはそのまま送る）。受け付ける古いモデルでは `Client(..., anthropic_version="2023-06-01")` を使う。今の Claude は、古い版でも既定でない `temperature` を拒否する。Gemini 3.6 以降と DeepSeek の思考モードは、`temperature` をエラーにせずに無視する（[詳細](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/dropped-parameter-warning.md)）
- 構造化出力: OpenAI は、すべてのプロパティを `required` に入れることを求める（省略できる項目は、`{"type": ["string", "null"]}` のように null も受け付ける形にする）。使える JSON Schema のキーワードは各社で違い、使えないものは `BadRequestError` になる（Gemini は無視する）
- Anthropic は max_tokens が必須なので、指定しないと 64000 で送る。今の Claude のモデルはどれもこの値を受け付け、Claude 5 系は推論の分もここに含める（`Client(..., default_max_tokens=...)` で変えられる）。ほかのプロバイダーは指定しなければ上限を送らないので、モデルの上限まで出す（[詳細](https://github.com/Syuma-gg/switchllm/blob/main/docs/design-docs/anthropic-max-tokens.md)）
- OpenAI Responses には `stop` シーケンスが存在しないため、指定すると `UnsupportedFeatureError`
- Responses API は既定でレスポンスを保存する。`extra={"store": False}` にしても、reasoning アイテムには既定で `encrypted_content` が付くので、`include` なしで再送できる（実 API では未確認）
- Gemini の関数スキーマは `parameters`（OpenAPI のサブセット）で送る。複雑な JSON Schema を使うなら `Client(..., schema_field="parametersJsonSchema")`
- Google は generateContent を「レガシー」とし、新規開発には Interactions API を勧めている。本ライブラリは広く文書化された generateContent を使う

## 既存ライブラリとの比較

同じ目的の OSS はすでにある。用途によってはそちらが適切。

| | switchllm（これ） | LiteLLM | any-llm (mozilla-ai) |
|---|---|---|---|
| 対応数 | 主要3社 + OpenAI 互換 | 100以上 | 主要どころ多数 |
| 出力形式 | 独自の共通型 | OpenAI Chat Completions 形式 | OpenAI 形式 |
| 実装 | HTTP 直叩き、依存 httpx のみ | 独自変換 + Proxy/Gateway | 公式 SDK を利用 |
| 向いている用途 | 中身を理解したい・依存を減らしたい・エージェント自作 | 本番運用、コスト管理、フォールバック | SDK ベースで軽く使いたい |

- LiteLLM — https://docs.litellm.ai/docs/
- any-llm — https://github.com/mozilla-ai/any-llm

## 開発者向け

```shell
git clone https://github.com/Syuma-gg/switchllm.git
cd switchllm
pip install -e ".[dev]"
python -m pytest          # モック API に対するテスト（APIキー不要）
python scripts/quality_gate.py --ci   # ruff・テスト・カバレッジ（CI と同じ検査）
```

- 構造: [ARCHITECTURE.md](https://github.com/Syuma-gg/switchllm/blob/main/ARCHITECTURE.md)
- ドキュメントの索引: [docs/index.md](https://github.com/Syuma-gg/switchllm/blob/main/docs/index.md)（設計判断、スコープ、公開 API、各社 API の仕様メモと出典、計画）
- 変更履歴: [CHANGELOG.md](https://github.com/Syuma-gg/switchllm/blob/main/CHANGELOG.md)

## ライセンス

MIT（[LICENSE](https://github.com/Syuma-gg/switchllm/blob/main/LICENSE)）
