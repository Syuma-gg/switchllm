# アーキテクチャ

switchllm の構造の地図。設計の理由は [docs/design-docs/index.md](docs/design-docs/index.md) にある。

## 処理の流れ

```
利用者のコード
  │  Client("anthropic:claude-sonnet-5-5").chat(messages, tools=[...])
  │  （非同期なら await AsyncClient(...).chat(...)）
  ▼
client.py      Client / AsyncClient   モデル文字列を解決し、ChatRequest（共通型）を組み立てる
  ▼
providers/     Provider   build_request(): 共通型 → 各社の HTTP リクエスト
  ▼
_http.py                  送信・リトライ・SSE の分解（httpx。同期と非同期の両方）
  ▼
providers/     Provider   parse_response() / stream_parser(): 各社の JSON → Response / StreamEvent
  ▼
利用者のコード            Response（message.provider_state に生データを保持）
```

## モジュールと層

**import してよいのは自分より下の層だけ**（providers の中どうしは可）。`tests/test_architecture.py` が検査する。

| 層 | モジュール | 役割 | import してよいもの |
|---|---|---|---|
| 0 | `types.py` | 共通データ型（Message / ToolCall / Response / Usage / StreamEvent / ProviderState / TextPart / ImagePart / FilePart） | 標準ライブラリ |
| 0 | `errors.py` | 共通例外の階層、HTTP ステータスとの対応 | 標準ライブラリ |
| 1 | `tools.py` | `Tool`、`@tool`（型ヒント → JSON Schema）、`run_tool_calls`、`arun_tool_calls` | 層0 |
| 1 | `_http.py` | 送信、リトライ、SSE パース（同期と非同期。リトライの判断と SSE の分解は共有） | 層0、httpx |
| 2 | `providers/` | 4つのアダプターとプリセット表 | 層0〜1、providers 内 |
| 3 | `client.py` | 利用者の入口 `Client` と `AsyncClient`（共通の部分は `_BaseClient`） | 層0〜2、httpx |
| 4 | `__init__.py` | 公開 API の再エクスポート | すべて |

公開 API は `switchllm` 直下の `__all__` だけで、`providers/` 以下を含むサブモジュールは内部実装（[public-api.md](docs/product-specs/public-api.md)）。

## アダプター

| アダプター | ファイル | プリセット | 仕様メモ |
|---|---|---|---|
| `openai` | `providers/openai_responses.py` | `openai` | [OpenAI Responses](docs/references/openai-responses.md) |
| `openai_chat` | `providers/openai_chat.py` | `openai-chat`、`deepseek`、`ollama`、`lmstudio`、`register_provider()` で足したもの | [Chat Completions 互換](docs/references/openai-chat-compatible.md) |
| `anthropic` | `providers/anthropic.py` | `anthropic` | [Anthropic Messages](docs/references/anthropic-messages.md) |
| `gemini` | `providers/gemini.py` | `gemini`、`google` | [Gemini generateContent](docs/references/gemini-generate-content.md) |

各アダプターは `providers/base.py` の `Provider` を継承し、3つのメソッドを実装する。どれも通信をしないので、`Client` と `AsyncClient` が同じものを使う（[判断](docs/design-docs/async-client.md)）。

- `build_request(req, stream=...)` → `HTTPRequest`
- `parse_response(data)` → `Response`
- `stream_parser()` → `StreamParser`。SSE のイベントを1つずつ `feed(event, data)` で受け取って `StreamEvent` を返し、`finish()` で最後の `done` を1回返す。`Provider.parse_stream()`（同期）と `aparse_stream()`（非同期）がこれを回す

## モデル文字列の解決

1. `"ollama:llama3.2:3b"` を最初の `:` で分けて `("ollama", "llama3.2:3b")` にする（`client.parse_model`）
2. `providers/__init__.py` の `PRESETS["ollama"]` から、アダプター・base_url・APIキーの環境変数を引く
3. `ADAPTERS` からアダプターのクラスを引いてインスタンスを作る

## テスト

何を確かめているかの細かい一覧は、各ファイルにある。状態は [品質評価](docs/QUALITY_SCORE.md)、E2E のケースは [E2E テスト](docs/e2e-tests.md) にある。

| ファイル | 対象 |
|---|---|
| `tests/conftest.py` | `FakeAPI`（httpx.MockTransport）。応答を積み、送られたリクエストを記録する。`api` を使うテストは、`Client` と `AsyncClient`（`AsyncDriver` で包んだもの）の2通りで回る |
| `tests/test_<adapter>.py` | アダプターごとのリクエストの形 / ツールの往復 / ストリーミング / エラー |
| `tests/test_cross_provider.py` | 会話の途中でプロバイダーを替えたときの履歴の変換 |
| `tests/test_consistency.py` | 同じテストを4アダプターで回し、切り替えても同じ振る舞いになること |
| `tests/test_reasoning_effort.py` | 推論の強さ（`reasoning_effort=`） |
| `tests/test_reasoning_summary.py` | 推論の要約（`reasoning_summary=`） |
| `tests/test_structured_output.py` | 構造化出力（`response_schema=`） |
| `tests/test_file_input.py` | ファイルの入力（`FilePart`）と、画像・ファイルの送る前の検査 |
| `tests/test_history.py` | 履歴の保存・復元（`to_dict()` / `from_dict()`） |
| `tests/test_async_client.py` | 非同期にしかないこと（並行の送信、取り消し、`aclose()`、`http_client=` の取り違えなど） |
| `tests/test_client.py` | モデル文字列、APIキー、`with_model()`、プロバイダー別のオプション、`extra_by_provider`、送る前の検査、プロバイダーの登録 |
| `tests/test_http.py` | リトライ、`Retry-After`、例外への対応づけ、エラーの本文の解釈、接続エラー |
| `tests/test_tools.py` | `@tool` のスキーマの生成と対応する型、`run_tool_calls`、`arun_tool_calls` |
| `tests/test_types.py` | 共通型（`Message`、`ToolCall`、`ImagePart`） |
| `tests/test_architecture.py` | 層の規則、ランタイム依存が httpx だけであること |
| `tests/test_docs.py` | リンク切れ、索引の漏れ、公開 API・プリセット・オプションの一覧の一致、CLAUDE.md の長さ、2つの README のそろい方 |
| `tests/test_quality_gate.py` | 品質検査（`scripts/quality_gate.py`）の判定、`noqa`・`pragma: no cover` に理由があること |
| `tests/test_harness.py` | 品質検査のファイル・Stop フック・CLAUDE.md の節が、テンプレート（`harness/quality-gate/`）と同じであること |
| `tests/test_recording.py` | 実 API の記録と再生（秘密を残さない、記録と違うリクエストを見逃さない）、リポジトリの記録にキーがないこと |
| `tests/sample_tools.py` | テストで共有するツール定義（`conftest` からは import しない） |
| `e2e/test_e2e_*.py` | 実 API に対する E2E テスト。`--models` を渡すと実 API で回し、渡さなければ記録を再生する |

- `e2e/` の E2E テストだけは、`--models` を渡すと実 API を叩く（料金が発生する。[E2E テスト](docs/e2e-tests.md)）。渡さなければ、`e2e/recordings/` に記録したやりとりを再生し、既定の `python -m pytest` にも入る（[テストの方針](docs/testing.md)）
- `scripts/quality_gate.py` は、ruff・pytest・カバレッジをまとめて回す（Stop フックと CI から呼ぶ。[方針](docs/quality-gates.md)）。`harness/quality-gate/` にある、他のプロジェクトでも使うテンプレートの写しなので、直すときはテンプレートも同じ変更で直す（[テンプレート](harness/quality-gate/README.md)）

## よくある変更

どの変更でも、関係する文書を同じ変更で直す。どの文書が何の正本かは、[docs の索引](docs/index.md) の「文書の種類」にある。

### OpenAI 互換サーバーをプリセットに足す

1. base_url を公式の文書で確かめる。確かめられないものはプリセットにせず、利用者に `register_provider()` を案内する（[判断](docs/design-docs/verified-presets.md)）
2. `PRESETS` に1行足す
3. [Chat Completions 互換](docs/references/openai-chat-compatible.md) に出典を書く
4. [public-api.md](docs/product-specs/public-api.md) のプリセット表に足す
5. テストを書く

### 新しい形式の API を足す

1. `Provider` を継承して3つのメソッドを実装する（ストリームは `StreamParser` の `feed()` / `finish()`）
2. `ADAPTERS` と `PRESETS` に登録する
3. 既存に倣って `tests/test_<name>.py` を足す（リクエストの形 / ツールの往復 / ストリーミング / エラー）
4. `tests/test_consistency.py` の `CASES` に1件足す
5. `docs/references/` に仕様メモを書く

### 共通パラメータを足す

1. まず [scope.md](docs/product-specs/scope.md) で、共通化してよいか確かめる
2. `ChatRequest` と、`Client` / `AsyncClient` の `chat()` / `stream()` に足す（引数が同じことは `tests/test_async_client.py` が検査する）
3. 4アダプターすべてで対応する。対応できないアダプターは `UnsupportedFeatureError`

### プロバイダー別のオプションを足す

1. アダプターの `option_names` に名前を足す。すべてのアダプターで受け付けるものは、`providers/base.py` の `COMMON_OPTIONS` に足して基底クラスで読む
2. [public-api.md](docs/product-specs/public-api.md) のオプション表に1行足す（ソースとの一致は `tests/test_client.py`、文書との一致は `tests/test_docs.py` が検査する）
