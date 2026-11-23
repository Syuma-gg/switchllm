# スコープ

軽量を保つため、やらないことを先に決めておく。要望が来たらこの文書を根拠に判断する。方針を変えるときは、この文書と [設計判断](../design-docs/index.md) を更新する。

## やること

- テキストの送受信（`chat()`）とストリーミング（`stream()`）
- ツール呼び出し（並列を含む）と、署名・推論情報を落とさない往復
- 会話の途中でのプロバイダー切り替え（履歴の変換）
- 画像入力
- 共通の例外と自動リトライ
- `@tool`（決めた範囲の型から JSON Schema を作る）と `run_tool_calls`
- `extra=` による各社固有パラメータの受け渡しと、`extra_by_provider=` でプロバイダーごとに並べて書くこと（モデルを切り替えても、呼び出し側のコードを直さずに済む）
- `register_provider()` による OpenAI 互換サーバーの追加
- 履歴の JSON 保存・復元（`Message.to_dict()` / `from_dict()`。推論情報を含む。2026-11-18）
- 構造化出力（`response_schema=` に JSON Schema の dict を渡し、`Response.parsed` に読んだ値を入れる。2026-11-20。[判断](../design-docs/structured-output.md)）。スキーマのない JSON モードは共通化しない（`extra=` で渡す）
- 推論の強さ（`reasoning_effort=` に段階の名前を渡し、各社の指定に名前を変えずに入れる。2026-11-21。[判断](../design-docs/reasoning-effort.md)）。推論の要約を返させる指定や、数での予算（Gemini 2.5 の `thinkingBudget`）は共通化しない（`extra=` で渡す）
- 非同期のクライアント（`AsyncClient`。引数とメソッドの名前は `Client` と同じ）と、`async def` のツールを並行に実行する `arun_tool_calls`（2026-11-23。[判断](../design-docs/async-client.md)）

## 足すと決めたもの

2026-11-18 に作者が決めた。選んだ基準は [基本方針](../design-docs/core-beliefs.md) の「機能を足すか絞るかの基準」で、進め方は [計画](../exec-plans/active/model-layer-features.md) にある。できたものから「やること」に移す。

| 機能 | 理由 |
|---|---|
| すべてのアダプターに `headers=` | 今は Chat Completions 互換だけにあり、ゲートウェイを経由するとほかのアダプターへ切り替えられない |
| PDF・ファイルの入力 | 文書を読ませる用途は多い。対応しない接続先には、送る前に `UnsupportedFeatureError` |

## やらないこと

| やらないこと | 代わりの手段 |
|---|---|
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

- 音声入力
- Gemini の Interactions API への移行（[負債](../exec-plans/tech-debt-tracker.md)）
