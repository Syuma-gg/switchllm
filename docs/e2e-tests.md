# E2E テスト（実 API）

`e2e/` のテストは、ライブラリの関数（`Client.chat()` / `stream()` / `with_model()` / `run_tool_calls()`）を、本物の API に対して呼ぶ。**料金が発生する**（`ollama` / `lmstudio` は除く）。

`--models` を渡したときだけ実 API を呼ぶ。渡さなければ、実 API で記録したやりとり（`e2e/recordings/`）を再生するだけで、ネットワークを使わない。再生は既定の `python -m pytest`、品質検査（Stop フックと CI）にも入る。記録と再生の決まりは [テストの方針](testing.md) にある。

## 回し方

```powershell
# 何を何回呼ぶかを見るだけ（API は呼ばない。キーも要らない）
python -m pytest e2e --models "anthropic:claude-sonnet-5-5,openai:gpt-5.5" --collect-only -q

# 実行して記録する（料金が発生する）
python -m pytest e2e --models "anthropic:claude-sonnet-5-5,anthropic:claude-haiku-5-5,openai:gpt-5.5,gemini:gemini-flash-latest,deepseek:deepseek-flash,ollama:<model>,lmstudio:<model>" --record

# 記録を再生する（料金なし。python -m pytest でも回る）
python -m pytest e2e

# 一部だけ
python -m pytest e2e --models "deepseek:deepseek-flash" -k "tool"
```

- `--models`（または環境変数 `SWITCHLLM_E2E_MODELS`）に、試すモデル文字列をカンマ区切りで渡す。渡さなければ、記録を再生する
- `--record` を付けると、通ったテストのやりとりを `e2e/recordings/` に記録する。付けずに実 API で回しても、記録は残らない。`--record` だけを付けて `--models` を渡さなければ、使い方の誤りとして止まる
- 実行の前に、すべてのモデルのクライアントを作る。キーが足りなければ、API を呼ぶ前に止まる
- 実 API で回したときは、最後に「U 番号ごとの結果」（[実 API 検証](exec-plans/active/real-api-verification.md) の表と対応）と、テストごとの観察メモ（推論情報が返ったか、API が拒否したときの文言など）を表示する。表の「結果」列にはここから書き写す
- `--vision-models` に挙げた Chat Completions 互換のモデルでは、画像のテストも回す（OpenAI・Anthropic・Gemini のモデルでは常に回す）
- Claude Code が実行するときは、モデルと料金の目安を伝えて、ユーザーの了承を得てから（[CLAUDE.md](../CLAUDE.md)）

## 書き方の決まり

- **文言ではなく構造を確かめる。** モデルの出力は毎回違う。確かめるのは、リクエストが受け付けられたか、ツールを呼んだか、`stop_reason` や usage が定義どおりか、`done` が1回だけか、など
- **モデルが拒否してよい指定は、API のエラーをそのまま返すことを確かめる。** モデルごとの制限の一覧は持たない（[判断](exec-plans/active/real-api-verification.md) の 2026-11-17）。受け付けたか拒否したかは観察メモに残す
- **skip と xfail を使わない。** 対象外のモデル（OpenAI だけの `store`、キーの要らないローカルのサーバーなど）には、最初からテストを作らない（収集のときに外す）
- **呼ぶ回数を `@pytest.mark.calls(n)` で書く。** 収集したときに合計を表示する
- **確かめる前提を `@pytest.mark.premise("U1", ...)` で書く。** プロバイダーに関係のない U 番号は数えない（`e2e/e2e_support.py` の `SCOPE`）
- テストのファイル名は `test_e2e_*.py` にする。`tests/` と同じ名前だと、両方を一度に回したときに pytest が区別できない

## テストケース

「境界」は境界値やふだんと違う入力を試すもの。回数は1モデルあたりの最大。

### 基本（`test_e2e_basic.py`）

| テスト | 確かめること | 境界 | 回数 | 前提 |
|---|---|---|---|---|
| `test_text` | テキストが返り、`stop_reason` が `end`、usage が 0 より大きい | | 1 | U12、U13 |
| `test_system_prompt` | system がモデルに届く（各社で入れる場所が違う） | | 1 | |
| `test_hand_written_history` | 手で作った assistant メッセージ（生データなし）を、共通型から変換して送れる | | 1 | U2 |
| `test_max_tokens_lower_boundary` | `max_tokens=16` で `stop_reason` が `max_tokens` になる。16 は OpenAI の `max_output_tokens` の下限 | ○ | 1 | |
| `test_stop_sequence` | `stop` で止まり、`stop_reason` が `end`。OpenAI Responses は送る前に `UnsupportedFeatureError`（呼ばない） | | 1 | |
| `test_temperature_zero` | `temperature=0`（下限）を送る。thinking が既定で有効なモデルは拒否することがあり、そのときは API のエラーをそのまま返す | ○ | 1 | |
| `test_empty_user_message` | 空の入力。応答か API のエラーのどちらかで、ほかの例外（パースの失敗など）にならない | ○ | 1 | |

### ツール（`test_e2e_tools.py`）

| テスト | 確かめること | 境界 | 回数 | 前提 |
|---|---|---|---|---|
| `test_tool_roundtrip_three_turns` | ツールを呼ぶ → 結果を渡して答える → 次のターンでもう一度ツール。推論情報を送り返しても拒否されない | | 3 | U3、U4、U6、U7、U11 |
| `test_tool_choice_none` | `tool_choice="none"` ではツールを呼ばない | | 1 | U9、U15 |
| `test_forced_tool_choice[required/named]` | ツールの強制。受け付けるモデルでは指定どおりに呼び、拒否するモデルでは API のエラーをそのまま返す | | 2 | U15 |
| `test_tool_without_parameters` | 引数のないツール（空の `properties`）を受け付け、`arguments == {}` で呼ぶ | ○ | 2 | U4 |
| `test_tool_schema_types` | str・int・float・bool・Literal・`list[str]`・省略できる引数を持つスキーマを受け付け、型どおりの値で呼ぶ | ○ | 1 | U4 |
| `test_parallel_tool_calls` | 2つの都市を調べさせる。並列の呼び出しと、まとめて返す結果を受け付ける | ○ | 3 | |
| `test_tool_error_result` | ツールの例外を `is_error` の結果として返しても受け付ける | ○ | 2 | |
| `test_unicode_arguments` | 引用符・バックスラッシュ・日本語・絵文字を含む引数が JSON として読め、結果として送り返せる | ○ | 2 | |
| `test_store_false`（OpenAI だけ） | `store: false` でツールを往復できる | | 2 | U1 |
| `test_parameters_json_schema`（Gemini だけ） | `schema_field="parametersJsonSchema"` でツールを往復できる | | 2 | U4 |

### ストリーミング（`test_e2e_stream.py`）

| テスト | 確かめること | 境界 | 回数 | 前提 |
|---|---|---|---|---|
| `test_stream_text` | `done` が最後に1回だけ来る。`text` の差分をつなぐと `done` の本文になる。usage が入る | | 1 | U10、U16 |
| `test_stream_tool_call_then_resend` | ストリーミングで組み立てたツール呼び出しと推論情報を、次のリクエストで送り返せる | | 2 | U11、U16 |
| `test_stream_max_tokens` | 出力の上限で止まったストリームも `done` で終わり、`stop_reason` が `max_tokens` | ○ | 1 | U16 |

### 画像（`test_e2e_images.py`）

| テスト | 確かめること | 境界 | 回数 | 前提 |
|---|---|---|---|---|
| `test_image_base64` | 64×64 の赤い PNG（base64）の色を答えられる | ○ | 1 | |
| `test_image_url_is_rejected_before_sending`（Gemini だけ） | URL の画像は送る前に `UnsupportedFeatureError` | | 0 | |

### エラー（`test_e2e_errors.py`）

失敗するリクエストは、多くのプロバイダーで課金されない。

| テスト | 確かめること | 境界 | 回数 | 前提 |
|---|---|---|---|---|
| `test_invalid_api_key`（キーの要るものだけ） | 認証の失敗が `APIError` になり、API の文言と本文をそのまま持つ。Gemini は無効なキーを 400 で返すことがあるので、`AuthenticationError` か `BadRequestError` のどちらかとし、どちらだったかを残す | | 1 | |
| `test_unknown_model` | 存在しないモデルが `NotFoundError` か `BadRequestError` になり、API の文言を持つ | ○ | 1 | |
| `test_temperature_above_range` | `temperature=5`（どの API の上限も超える）。API のエラーをそのまま返すか、黙って無視される（DeepSeek の思考モード） | ○ | 1 | |

### 切り替え（`test_e2e_switch.py`）

`--models` で隣どうしのモデル（3つ以上なら最後から最初へも）で、A でツールを呼ぶ → B で結果を渡して答える → A に戻して次のターン、を試す。同じプロバイダーの2つのモデルを隣に置くと U8（Anthropic）と U17（DeepSeek）を、Gemini の隣に別のプロバイダーを置くと U5 を確かめられる。

| テスト | 確かめること | 回数 | 前提 |
|---|---|---|---|
| `test_switch_and_back[A->B]` | 別のモデルが作った履歴を、どちらの向きでも受け付ける | 3 | U5、U8、U17（組み合わせによる） |

### usage（`test_e2e_usage.py`）

| テスト | 確かめること | 回数 | 前提 |
|---|---|---|---|
| `test_usage_breakdown` | 推論させる質問で、usage の内訳が正規化の前提どおりか（Gemini は `totalTokenCount` と内訳の和、Chat Completions 互換は `total_tokens` と `prompt + completion`、`reasoning_tokens` が出力を超えない）。ストリーミングでも同じ | 2 | U14、U18 |

## ネットワークを使わないテストとの分担

形式の細かい組み合わせ（壊れた JSON、途中で切れたストリーム、知らないイベント、リトライの境界、`Retry-After` の上限など）は、`tests/` のモックで確かめる。E2E では、モックの前提（各社がその形式を本当に受け付け、本当にその形で返すか）を確かめる。E2E で分かった形の違いは、同じ形の応答をモックのテストにも足す。
