# スコープ

軽量を保つため、やらないことを先に決めておく。要望が来たらこの文書を根拠に判断する。方針を変えるときは、この文書と [設計判断](../design-docs/index.md) を更新する。

## やること

細かい振る舞いは、各行のリンク先の設計判断と [公開 API](public-api.md) にある。

- テキストの送受信（`chat()`）とストリーミング（`stream()`）
- ツール呼び出し（並列を含む）と、署名・推論情報を落とさない往復（[判断](../design-docs/provider-state-resend.md)）
- 会話の途中でのプロバイダー切り替え（履歴の変換）
- 画像の入力
- 共通の例外と自動リトライ（[信頼性](../RELIABILITY.md)）
- `@tool`（決めた範囲の型から JSON Schema を作る）と `run_tool_calls`（[判断](../design-docs/tool-schema-scope.md)）
- `extra=` による各社に固有のパラメータの受け渡しと、`extra_by_provider=` でプロバイダーごとに並べて書くこと（[判断](../design-docs/provider-specific-params.md)）
- `register_provider()` による OpenAI 互換サーバーの追加
- 履歴の JSON 保存・復元（`Message.to_dict()` / `from_dict()`。2026-11-18。[判断](../design-docs/history-serialization.md)）
- 構造化出力（`response_schema=`。2026-11-20。[判断](../design-docs/structured-output.md)）。スキーマのない JSON モードは共通化しない（`extra=` で渡す）
- 推論の強さ（`reasoning_effort=`。2026-11-21。[判断](../design-docs/reasoning-effort.md)）。数での予算（Gemini 2.5 の `thinkingBudget`）は共通化しない（`extra=` で渡す）
- 推論の要約（`reasoning_summary=True`。2026-11-26。[判断](../design-docs/reasoning-summary.md)）。段階は受け付けない
- すべてのアダプターで `headers=`（2026-11-23。[判断](../design-docs/custom-headers.md)）
- ファイルの入力（`FilePart`。2026-11-23。[判断](../design-docs/file-input.md)）。ファイルの URL とアップロードの API は扱わない
- 非同期のクライアント（`AsyncClient`）と、`async def` のツールを並行に実行する `arun_tool_calls`（2026-11-23。[判断](../design-docs/async-client.md)）

2026-11-18〜23 に足した6つの機能の経緯は、[モデルの層の機能を足す](../exec-plans/completed/2026-11-23-model-layer-features.md) にある。

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
- ファイルの URL（OpenAI Responses の `file_url`、Anthropic の `url`）と、各社のアップロードの API（Files API）
- Gemini の Interactions API への移行（[負債](../exec-plans/tech-debt-tracker.md)）
