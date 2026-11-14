# 変更履歴

このプロジェクトの主な変更を記録する。形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) にならい、版番号は [セマンティック バージョニング](https://semver.org/lang/ja/) に従う。

## 0.1.0（未公開）

最初の版。

### 追加

- `Client("provider:model")` の文字列、または環境変数 `SWITCHLLM_MODEL` でプロバイダーを選ぶクライアント
- 対応するプロバイダー: OpenAI（Responses API / Chat Completions）、Anthropic、Google Gemini、OpenAI 互換サーバー（`ollama` / `lmstudio` のプリセットと `register_provider()`）
- テキストの送受信（`chat()`）とストリーミング（`stream()`）
- ツール呼び出し（並列を含む）。`@tool` で関数から JSON Schema を作り、`run_tool_calls()` でまとめて実行する
- ツールを往復させるときに、署名や推論情報（Gemini の thoughtSignature、Anthropic の thinking、OpenAI の reasoning）を落とさない
- 会話の途中でのプロバイダーの切り替え（履歴の変換）
- 画像入力（`ImagePart`）
- 共通の例外（基底は `SwitchLLMError`）と自動リトライ
- `extra=` による、各社固有のパラメータの受け渡し
- 型情報（`py.typed`）
