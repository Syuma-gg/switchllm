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
- 共通の例外（基底は `SwitchLLMError`）と自動リトライ。プロバイダーが返したエラーは、文言（`message`）と本文の全体（`body`）をそのまま持つ
- `extra=` による、各社固有のパラメータの受け渡し
- `extra_by_provider=` で、各社固有のパラメータをプロバイダーごとに並べて書ける。選んだプロバイダーの分だけを送るので、モデルを切り替えても呼び出し側のコードは変わらない
- `with_model()` で、接続を共有したまま別のモデルに切り替える。同じプロバイダーなら APIキー・base_url・オプションを引き継ぐ
- 送る前に分かる誤り（知らないオプション名、`tools` にない `tool_choice`、`TextPart` / `ImagePart` 以外のパーツ）は、リクエストを送らずに例外にする
- 型情報（`py.typed`）
