# スコープ

軽量を保つため、やらないことを先に決めておく。要望が来たらこの文書を根拠に判断する。方針を変えるときは、この文書と [設計判断](../design-docs/index.md) を更新する。

## やること

- テキストの送受信（`chat()`）とストリーミング（`stream()`）
- ツール呼び出し（並列を含む）と、署名・推論情報を落とさない往復
- 会話の途中でのプロバイダー切り替え（履歴の変換）
- 画像入力
- 共通の例外と自動リトライ
- `@tool`（決めた範囲の型から JSON Schema を作る）と `run_tool_calls`
- `extra=` による各社固有パラメータの受け渡し
- `register_provider()` による OpenAI 互換サーバーの追加

## 今後足す候補

| 候補 | 理由 |
|---|---|
| 非同期クライアント（`AsyncClient`） | FastAPI などで使うのに必要。アダプターは同期版と共有でき、`_http.py` に async 版の送信関数を足すだけで済む設計になっている |

## やらないこと

| やらないこと | 代わりの手段 |
|---|---|
| 推論の強さの共通パラメータ | `extra=` で各社の形式を渡す（OpenAI `reasoning` / Anthropic `thinking` / Gemini `generationConfig.thinkingConfig`）。指定方法がモデルの世代ごとに変わり、4アダプターで追い続けるのは重い |
| 構造化出力の共通化 | `extra=` で各社の形式を渡す（OpenAI `text.format` / Gemini `generationConfig.responseSchema` など） |
| 複数モデル間のフォールバック | `RateLimitError` などを捕まえて、`client.with_model()` で別モデルに切り替える |
| 料金計算 | `Usage.details` の生の内訳を使う |
| プロンプトキャッシュの制御 | なし（必要になったら再検討） |
| Bedrock / Vertex AI / Azure の認証 | なし |
| エージェントのループ制御、記憶、RAG | 利用者のコード、またはエージェントフレームワーク |
| ゲートウェイ、プロキシサーバー | LiteLLM の Proxy など |
| `@tool` の対応型の追加（Pydantic、dataclass など） | `@tool(parameters=...)` で JSON Schema を渡す（[判断](../design-docs/tool-schema-scope.md)） |
| 独自形式のアダプターを外から登録する仕組み | このリポジトリにアダプターとして足す（[判断](../design-docs/minimal-public-api.md)） |
| 確認していない互換 API のプリセット | `register_provider()`（[判断](../design-docs/verified-presets.md)） |

## 未定（要望があれば検討）

- PDF・ファイル入力、音声入力
- Gemini の Interactions API への移行（[負債](../exec-plans/tech-debt-tracker.md)）
