# switchllm

OpenAI / Anthropic / Gemini / OpenAI互換サーバー（Ollama・LM Studio・vLLM など）を、**同じコードで呼べる**軽量なクライアントライブラリ。

```python
from switchllm import Client

client = Client("anthropic:claude-sonnet-5-5")   # ← ここだけ変えればプロバイダーが変わる
print(client.chat("こんにちは").text)
```

- 依存は `httpx` だけ。各社の SDK は使わず、HTTP リクエストの形式差をアダプターで吸収する
- ツール呼び出しの往復に必要な署名・推論情報（Gemini の thoughtSignature、Anthropic の thinking、OpenAI の reasoning）を落とさない
- エージェントフレームワークではなく、その下で使う薄い層（[やること・やらないこと](docs/product-specs/scope.md)）
- プロバイダーは文字列で選ぶ。失敗したときに別のプロバイダーへ自動で切り替える機能（フォールバック、ルーティング）はない。必要なら `RateLimitError` などを捕まえて `client.with_model()` で切り替える

---

## インストール

```powershell
cd switchllm
pip install -e ".[dev]"
python -m pytest          # モック API に対するテスト（APIキー不要）
```

APIキーは環境変数で渡す（`api_key=` 引数でも可）。

| プロバイダー | モデル文字列の例 | 環境変数 |
|---|---|---|
| OpenAI（Responses API） | `openai:gpt-5.5` | `OPENAI_API_KEY` |
| Anthropic | `anthropic:claude-sonnet-5-5` | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini:gemini-flash-latest` | `GEMINI_API_KEY`（または `GOOGLE_API_KEY`） |
| OpenAI（Chat Completions） | `openai-chat:<model>` | `OPENAI_API_KEY` |
| Ollama / LM Studio（ローカル） | `ollama:llama3.2` / `lmstudio:<model>` | 不要 |
| その他の OpenAI 互換 API | `register_provider()` で登録する（[後述](#その他の-openai-互換-api)） | 登録時に指定 |

モデル ID は頻繁に変わるので、各社のモデル一覧で確認してから指定する。

## 切り替え方は2通り

1. **コードの1か所**: `Client("openai:gpt-5.5")` の文字列を変える
2. **コードを変えない**: `Client()` と書いておき、環境変数 `SWITCHLLM_MODEL` で指定する

```powershell
$env:SWITCHLLM_MODEL = "gemini:gemini-flash-latest"
python examples/agent_loop.py
```

モデル文字列は最初の `:` で区切るので、`ollama:llama3.2:3b` のようにモデル名にコロンを含んでもよい。

## 使い方

### テキスト・ストリーミング

```python
res = client.chat("日本の首都は？", system="一言で答える", max_tokens=100, temperature=0)
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
- 並列ツール呼び出しにも対応（Anthropic・Gemini では結果を1メッセージにまとめて送る）
- `run_tool_calls` は、モデルが決めた引数で関数を実行する。危険な操作をする関数では引数を検証すること（[SECURITY.md](docs/SECURITY.md)）

### プロバイダー固有の機能

共通化できないパラメータは `extra=` でリクエスト本体にそのままマージする。ここだけはプロバイダー依存のコードになる。

```python
client.chat("...", extra={"metadata": {"user_id": "u-1"}})          # Anthropic
client.chat("...", extra={"reasoning": {"effort": "high"}})          # OpenAI Responses
```

### 例外

どのプロバイダーでも同じ例外クラスになる: `AuthenticationError`(401/403) / `RateLimitError`(429) / `BadRequestError`(400系) / `NotFoundError`(404) / `ServerError`(5xx) / `StreamError` / `APIConnectionError` / `ConfigurationError` / `UnsupportedFeatureError`。

429・5xx・接続エラーは指数バックオフで自動リトライする（`max_retries=2`、`Retry-After` ヘッダーを尊重）。ストリーミングは最初のイベントを受け取る前だけリトライする。詳しくは [RELIABILITY.md](docs/RELIABILITY.md)。

### その他の OpenAI 互換 API

プリセットにないサーバー（クラウドの互換 API、自前の vLLM、社内ゲートウェイなど）は、名前を付けて登録する。base_url は各サービスの公式ドキュメントで確認すること。

```python
from switchllm import Client, register_provider

register_provider("mycloud", base_url="https://api.example.com/v1", api_key_env="MYCLOUD_API_KEY")
register_provider("myvllm", base_url="http://gpu-box:8000/v1", api_key_env=())   # キー不要
client = Client("myvllm:Qwen/Qwen3-8B")
```

登録せずに `Client("openai-chat:<model>", base_url="...", api_key="...")` と直接指定してもよい。

---

## 設計の要点

詳しい理由は [設計判断の一覧](docs/design-docs/index.md) にある。

- **OpenAI 本家は Responses API、互換サーバーは Chat Completions。** GPT-5.4 以降、Chat Completions では推論とツール呼び出しを両立できないため（[詳細](docs/design-docs/openai-responses-api.md)）
- **生データを `provider_state` に退避し、同じモデルへはそのまま返す。** 署名や推論アイテムを落とすと、ツールの往復がエラーになるため。別のモデルへは共通型から変換し直す（[詳細](docs/design-docs/provider-state-resend.md)）
- **主要3社はネイティブ API を使う。** 各社の OpenAI 互換エンドポイントは、キャッシュや thinking などの機能が制限されるため（[詳細](docs/design-docs/native-apis.md)）
- **`usage` の意味を揃える。** `input_tokens` はキャッシュを含む入力の総数、`output_tokens` は推論を含む出力の総数（[詳細](docs/design-docs/usage-normalization.md)）
- **SDK を使わず HTTP を直接呼ぶ。** 依存を httpx だけにし、形式の差をコード上で見えるようにするため（[詳細](docs/design-docs/http-without-sdk.md)）

## 既知の制限

- **実 API ではまだ検証していない**。テストは各社ドキュメントの形式を再現したモックに対するもの。`python scripts/smoke_test.py anthropic:claude-sonnet-5-5 openai:gpt-5.5 gemini:gemini-flash-latest` で、手元のキーを使って疎通を確かめてほしい（少額の料金が発生する）
- 非同期（`async`）は未対応
- 画像入力は対応、音声・PDF・ファイル入力は未対応。Gemini への画像 URL は非対応（base64 で渡す）
- 構造化出力と推論の強さは共通化していない（`extra` で各社の指定を渡す）
- OpenAI Responses には `stop` シーケンスが存在しないため、指定すると `UnsupportedFeatureError`
- Responses API は既定でレスポンスを保存する。`extra={"store": False}` にする場合、reasoning アイテムの再送には `include: ["reasoning.encrypted_content"]` も必要
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

- 構造: [ARCHITECTURE.md](ARCHITECTURE.md)
- ドキュメントの索引: [docs/index.md](docs/index.md)（設計判断、スコープ、公開 API、各社 API の仕様メモと出典、計画）
- 変更履歴: [CHANGELOG.md](CHANGELOG.md)

## ライセンス

MIT（[LICENSE](LICENSE)）
