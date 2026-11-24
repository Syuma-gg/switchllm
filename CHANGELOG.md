# 変更履歴

このプロジェクトの主な変更を記録する。形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) にならい、版番号は [セマンティック バージョニング](https://semver.org/lang/ja/) に従う。

## 0.1.0（未公開）

最初の版。

### 追加

- `Client("provider:model")` の文字列、または環境変数 `SWITCHLLM_MODEL` でプロバイダーを選ぶクライアント
- 対応するプロバイダー: OpenAI（Responses API / Chat Completions）、Anthropic、Google Gemini、DeepSeek（`deepseek` のプリセット）、OpenAI 互換サーバー（`ollama` / `lmstudio` のプリセットと `register_provider()`）
- テキストの送受信（`chat()`）とストリーミング（`stream()`）
- ツール呼び出し（並列を含む）。`@tool` で関数から JSON Schema を作り、`run_tool_calls()` でまとめて実行する
- ツールを往復させるときに、署名や推論情報（Gemini の thoughtSignature、Anthropic の thinking、OpenAI の reasoning、DeepSeek の reasoning_content）を落とさない
- 履歴の JSON 保存・復元（`Message.to_dict()` / `from_dict()`）。推論情報（署名・推論）も残すので、復元した会話でも送り返せる。`chat()` は保存した dict のリストをそのまま受け取る
- 構造化出力（`chat(response_schema=...)` / `stream(response_schema=...)`）。JSON Schema の dict を各社の形式で送り、答えをその形に縛る。読んだ値は `Response.parsed` に入る。JSON Schema を受け付けない DeepSeek では、送る前に `UnsupportedFeatureError`
- 推論の強さ（`chat(reasoning_effort=...)` / `stream(reasoning_effort=...)`）。`none`〜`max` の7段階の名前を、各社の指定（OpenAI `reasoning.effort`、Anthropic `output_config.effort`、Gemini `thinkingLevel`、Chat Completions `reasoning_effort`）に変えずに入れる。各社の API に名前のない段階（Anthropic の `minimal`、Gemini の `none`・`max`）は、送る前に `UnsupportedFeatureError`
- Anthropic の API の版は、最新の `2026-10-21` を送る（`anthropic_version=` で変えられる）。この版はどのモデルでも `temperature` を拒否するので、`temperature=` は送る前に `UnsupportedFeatureError`
- `headers=` を、すべてのプロバイダーで受け付ける（ゲートウェイなどに足すヘッダー）。ライブラリが付けるヘッダー（APIキーのヘッダーを含む）と同じ名前なら、大文字と小文字を区別せずに置き換え、同じヘッダーを2本送らない。APIキーのヘッダーを渡せば、`api_key=` も環境変数もなくてよい。値に `None` を渡すと、ライブラリが付けるそのヘッダーを送らない（ゲートウェイがキーを別の名前のヘッダーで受けるとき、プロバイダーのキーを送らないため）
- 非同期のクライアント `AsyncClient`。引数とメソッドの名前は `Client` と同じで、`await client.chat(...)`、`async for ev in client.stream(...)`、`async with` / `aclose()`。アダプターは同期版と同じものを使う
- `arun_tool_calls()`。`async def` のツールを await し、呼び出しを並行に実行して、結果を呼び出しの順に返す。`run_tool_calls()` は `async def` のツールを実行せず、`arun_tool_calls()` を使うようエラーの結果を返す
- `Client` に `httpx.AsyncClient` を、`AsyncClient` に `httpx.Client` を渡すと、作った時点で `TypeError`
- 会話の途中でのプロバイダーの切り替え（履歴の変換）。Anthropic のモデルどうしの切り替えでは、thinking ブロックを捨てずにそのまま送る
- 画像入力（`ImagePart`）
- ファイルの入力（`FilePart`。`FilePart.from_file()`）。PDF などを base64 で送る。OpenAI（Responses）と Gemini には種類を確かめずに送り、Anthropic は PDF とテキスト、Chat Completions 互換は PDF だけ。DeepSeek・Ollama・LM Studio のプリセットと、受け付けないと分かっている種類は、送る前に `UnsupportedFeatureError`
- 共通の例外（基底は `SwitchLLMError`）と自動リトライ。プロバイダーが返したエラーは、文言（`message`）と本文の全体（`body`）をそのまま持つ
- `extra=` による、各社固有のパラメータの受け渡し
- `extra_by_provider=` で、各社固有のパラメータをプロバイダーごとに並べて書ける。選んだプロバイダーの分だけを送るので、モデルを切り替えても呼び出し側のコードは変わらない
- `with_model()` で、接続を共有したまま別のモデルに切り替える。同じプロバイダーなら APIキー・base_url・オプションを引き継ぐ
- 送る前に分かる誤り（知らないオプション名、`tools` にない `tool_choice`、`TextPart` / `ImagePart` / `FilePart` 以外のパーツ、user 以外のメッセージに入れた画像とファイル、中身のない画像とファイル）は、リクエストを送らずに例外にする
- 型情報（`py.typed`）
