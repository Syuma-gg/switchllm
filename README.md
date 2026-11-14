# unillm

OpenAI / Anthropic / Gemini / OpenAI互換サーバー（Ollama・OpenRouter・Groq など）を、**同じコードで呼べる**薄いクライアントライブラリ。

```python
from unillm import Client

client = Client("anthropic:claude-sonnet-5-5")   # ← ここだけ変えればプロバイダーが変わる
print(client.chat("こんにちは").text)
```

依存は `httpx` のみ。各社 SDK は使わず、HTTP リクエストの形式差をアダプターで吸収する。

---

## インストール

```powershell
cd unillm
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
| OpenRouter / Groq / DeepSeek / xAI / Mistral / Together | `groq:<model>` など | `GROQ_API_KEY` など |
| Ollama / LM Studio（ローカル） | `ollama:llama3.2` | 不要 |

モデル ID は頻繁に変わるので、各社のモデル一覧で確認してから指定する。

## 切り替え方は2通り

1. **コードの1か所**: `Client("openai:gpt-5.5")` の文字列を変える
2. **コードを変えない**: `Client()` と書いておき、環境変数 `UNILLM_MODEL` で指定する

```powershell
$env:UNILLM_MODEL = "gemini:gemini-flash-latest"
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
from unillm import Client, Message, run_tool_calls, tool

@tool
def get_weather(city: Annotated[str, "都市名"], unit: Literal["c", "f"] = "c") -> dict:
    """都市の現在の天気を返す。"""
    return {"city": city, "weather": "晴れ"}

messages = [Message.user("東京と大阪の天気は？")]
while True:
    res = client.chat(messages, tools=[get_weather])
    messages.append(res.message)          # 加工せずそのまま積む（後述の「署名」が入っている）
    if res.stop_reason != "tool_use":
        break
    messages.extend(run_tool_calls([get_weather], res.tool_calls))
print(res.text)
```

- `@tool` は型ヒントと docstring から JSON Schema を作る（`Annotated[T, "説明"]`、`Literal`、`Optional`、`list[T]`、`Enum` に対応）
- `tool_choice`: `"auto"`（既定）/ `"none"` / `"required"` / `{"name": "get_weather"}`
- 並列ツール呼び出しにも対応（Anthropic・Gemini では結果を1メッセージにまとめて送る）

### プロバイダー固有の機能

共通化できないパラメータは `extra=` でリクエスト本体にそのままマージする。ここだけはプロバイダー依存のコードになる。

```python
client.chat("...", extra={"metadata": {"user_id": "u-1"}})          # Anthropic
client.chat("...", extra={"reasoning": {"effort": "high"}})          # OpenAI Responses
```

### 例外

どのプロバイダーでも同じ例外クラスになる: `AuthenticationError`(401/403) / `RateLimitError`(429) / `BadRequestError`(400系) / `NotFoundError`(404) / `ServerError`(5xx) / `StreamError` / `APIConnectionError` / `ConfigurationError` / `UnsupportedFeatureError`。
429・5xx・接続エラーは指数バックオフで自動リトライする（`max_retries=2`、`Retry-After` ヘッダーを尊重）。ストリーミングは最初のバイトを受け取る前だけリトライする。

### 自前サーバーの追加

```python
from unillm import register_provider
register_provider("myvllm", base_url="http://gpu-box:8000/v1", api_key_env=())
Client("myvllm:Qwen/Qwen3-8B")
```

まったく別の形式の API は `providers/base.py` の `Provider` を継承して `build_request` / `parse_response` / `parse_stream` の3メソッドを書き、`register_adapter()` で登録する。

---

## 設計判断と根拠

### 1. OpenAI 本家は Responses API、互換サーバーは Chat Completions

OpenAI の移行ガイドは Chat Completions を引き続きサポートするとしつつ、「GPT-5.4 以降、Chat Completions は `reasoning_effort` が `none` 以外のときツール呼び出しに対応しない」と明記している。推論とツールを両立するため、`openai:` は Responses API を使う。一方、Ollama・vLLM・OpenRouter などの「OpenAI互換」は Chat Completions 形式なので、そちら用に別アダプター（`openai_chat`）を用意した。

### 2. 生データを `provider_state` に退避し、同じモデルへはそのまま返す

共通型だけで履歴を持つと、ツール往復に必須の情報が落ちる。

- **Gemini 3 以降**: 関数呼び出しの往復で `thoughtSignature` を返さないと 4xx エラー。並列呼び出しでは署名は最初の `functionCall` にだけ付き、`FC1, FC2, FR1, FR2` の順で返す必要がある
- **Anthropic**: thinking とツールを併用するとき、thinking ブロックを signature ごと無加工で返す必要がある
- **OpenAI Responses**: reasoning アイテムを含む output アイテムを、状態を手動管理するなら input に戻す

そこで `res.message.provider_state` に生データを保持し、**同じプロバイダー・同じモデル**に送るときはそれを使う。別モデルに送るときは共通型から変換し直す（thinking は捨てる）。Gemini へ署名のない履歴を送る場合は、公式が「最終手段」として示すダミー署名 `skip_thought_signature_validator` を付ける（性能低下の可能性あり）。

### 3. 各社の「OpenAI互換エンドポイント」だけで済ませない理由

Anthropic も Gemini も OpenAI SDK 互換の入口を持つので、それだけで切り替える手もある。ただし Anthropic は互換レイヤーを「テスト・比較用で、本番向けの長期的な解決策ではない」と位置づけ、プロンプトキャッシュ非対応・thinking の中身が返らないなどの制限を挙げている。Google も、OpenAI ライブラリを既に使っていないなら Gemini API を直接呼ぶよう勧めている。ネイティブ機能を落とさないため、主要3社はネイティブ API のアダプターにした。

### 4. `usage` の意味を揃える

同じ「input_tokens」でも中身が違う。Anthropic の `input_tokens` はキャッシュ読み書き分を含まないので足し合わせ、Gemini は `thoughtsTokenCount` を出力側に含めて、OpenAI と同じ「処理した入力総数 / 推論込みの出力総数」に揃えた。生の内訳は `usage.details` に残している。

### 5. SDK を使わず HTTP 直叩き

リクエスト形式の差がコード上で全部見え、`httpx.MockTransport` でネットワークなしにテストできる。代償として、API 仕様の変更を自分で追う必要がある（mozilla-ai の any-llm は逆に「公式 SDK を使って保守負担を減らす」方針を取っている）。

---

## 既知の制限（正直なところ）

- **実 API ではまだ検証していない**。テストは各社ドキュメントの形式を再現したモックに対するもの。`python scripts/smoke_test.py anthropic:claude-sonnet-5-5 openai:gpt-5.5 gemini:gemini-flash-latest` で、手元のキーを使って疎通を確認してほしい（少額の料金が発生する）
- 非同期（`async`）未対応
- 画像入力は対応、音声・PDF・ファイル入力は未対応。Gemini への画像 URL は非対応（base64 で渡す）
- 構造化出力（JSON Schema 強制）は共通化していない（`extra` で各社の指定を渡す）
- OpenAI Responses の `stop` シーケンスは API 側に存在しないため `UnsupportedFeatureError`
- Responses API は既定でレスポンスを保存する。`extra={"store": False}` にする場合、reasoning アイテムの再送には `include: ["reasoning.encrypted_content"]` も必要
- Gemini の関数スキーマは `parameters`（OpenAPI サブセット）で送る。複雑な JSON Schema を使うなら `Client(..., schema_field="parametersJsonSchema")`
- Google は generateContent を「レガシー」とし、新規開発には Interactions API を勧めている。本ライブラリは広く文書化された generateContent を使う
- 互換サーバーのプリセット URL は第三者のまとめで照合したもので、各社公式で個別に確認していない

## 既存ライブラリとの比較

同じ目的の OSS はすでにある。用途によってはそちらが適切。

| | unillm（これ） | LiteLLM | any-llm (mozilla-ai) |
|---|---|---|---|
| 対応数 | 主要3社 + OpenAI互換 | 100以上 | 主要どころ多数 |
| 出力形式 | 独自の共通型 | OpenAI Chat Completions 形式 | OpenAI 形式 |
| 実装 | HTTP 直叩き、依存 httpx のみ | 独自変換 + Proxy/Gateway | 公式 SDK を利用 |
| 向いている用途 | 中身を理解したい・依存を減らしたい・エージェント自作 | 本番運用、コスト管理、フォールバック | SDK ベースで軽く使いたい |

## 参考資料

- OpenAI: Migrate to the Responses API — https://developers.openai.com/api/docs/guides/migrate-to-responses
- Google: Thought signatures — https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures
- Google: OpenAI compatibility — https://ai.google.dev/gemini-api/docs/openai
- Google: Function calling — https://ai.google.dev/gemini-api/docs/generate-content/function-calling
- Anthropic: Streaming Messages — https://docs.anthropic.com/en/api/messages-streaming
- Anthropic: Extended thinking — https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking
- Anthropic: OpenAI SDK compatibility — https://docs.anthropic.com/en/api/openai-sdk
- LiteLLM — https://docs.litellm.ai/docs/
- any-llm — https://github.com/mozilla-ai/any-llm
- 互換 API の base URL 一覧（第三者まとめ） — https://www.developersdigest.tech/reference/api-compatibility

## ディレクトリ構成

```
src/unillm/
  client.py            Client（唯一の入口）
  types.py             Message / ToolCall / Response / Usage / StreamEvent
  tools.py             @tool デコレーター、run_tool_calls
  errors.py            共通例外
  _http.py             送信・リトライ・SSE パース
  providers/
    base.py            アダプターの基底クラス
    openai_responses.py
    openai_chat.py     OpenAI 互換（Ollama, Groq, OpenRouter ...）
    anthropic.py
    gemini.py
    __init__.py        プリセット表・register_provider
tests/                 モック API に対するテスト
examples/              basic.py, agent_loop.py
scripts/smoke_test.py  実 API 疎通テスト
```
