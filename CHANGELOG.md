# 変更履歴

このプロジェクトの主な変更を記録する。形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) にならい、版番号は [セマンティック バージョニング](https://semver.org/lang/ja/) に従う。

## 0.1.0（未公開）

最初の版。

### 追加

#### 基本

- `Client("provider:model")` の文字列、または環境変数 `SWITCHLLM_MODEL` でプロバイダーを選ぶクライアント
- 対応するプロバイダー: OpenAI（Responses API / Chat Completions）、Anthropic、Google Gemini、DeepSeek（`deepseek` のプリセット）、OpenAI 互換サーバー（`ollama` / `lmstudio` のプリセットと `register_provider()`）
- クラウドの API キーで呼ぶプリセット: Azure OpenAI（`azure` / `azure-chat`）、Claude in Microsoft Foundry（`foundry`）、Claude in Amazon Bedrock（`bedrock`）、Vertex AI express mode（`vertex`）
  - URL に入るリソース名やリージョンは、各社の SDK と同じ環境変数（`AZURE_OPENAI_ENDPOINT`・`ANTHROPIC_FOUNDRY_RESOURCE`・`AWS_REGION` など）から組み立てる。`base_url=` を渡せばそちらを使う。値がないか使えない値なら、`Client` を作るときに `ConfigurationError`
  - Azure にはキーを `api-key` ヘッダーで送る。`bedrock` は URL の画像を、`azure-chat` は `reasoning_summary=True` を、送る前に `UnsupportedFeatureError` にする
  - クラウドの認証（IAM・SigV4、OAuth、Entra ID）には対応しない
- オプション `image_url=False`（`anthropic` のアダプター）と `reasoning_summary=False`（Chat Completions 互換）。対応しない接続先で、URL の画像と推論の要約を送る前に止める
- テキストの送受信（`chat()`）とストリーミング（`stream()`）
- 画像の入力（`ImagePart`）。URL の画像は各社の API が取りに行く（Gemini は `fileData.fileUri`。Ollama は base64 だけなので、URL は送る前に `UnsupportedFeatureError`）
- プリセットごとに送れるものと、送る前に止まるものの対応表（public-api.md）。表とライブラリの振る舞いが一致することをテストで確かめる
- Chat Completions 互換のオプション `tool_choice=False`・`image_url=False`（`ollama` のプリセットは両方 `False`）。対応しない接続先で、`tool_choice` の `"none"` と強制、URL の画像を送る前に止める
- 受信の待ちの既定は 600 秒（公式の OpenAI・Anthropic の SDK と同じ）。受信の待ちのタイムアウトはリトライしない（API が生成している最中のことが多く、送り直すと同じ生成を重ねて頼むため）。接続できないときなどは、これまでどおりリトライする
- 共通の例外（基底は `SwitchLLMError`）と自動リトライ。プロバイダーが返したエラーは、文言（`message`）と本文の全体（`body`）をそのまま持つ
- 型情報（`py.typed`）

#### ツール

- ツール呼び出し（並列を含む）。`@tool` で関数から JSON Schema を作り、`run_tool_calls()` でまとめて実行する
- ツールを往復させるときに、署名や推論情報（Gemini の thoughtSignature、Anthropic の thinking、OpenAI の reasoning、DeepSeek の reasoning_content）を落とさない
- `arun_tool_calls()`
  - `async def` のツールを await し、呼び出しを並行に実行して、結果を呼び出しの順に返す
  - `run_tool_calls()` は `async def` のツールを実行せず、`arun_tool_calls()` を使うようエラーの結果を返す

#### 切り替え

- 会話の途中でのプロバイダーの切り替え（履歴の変換）。Anthropic のモデルどうしの切り替えでは、thinking ブロックを捨てずにそのまま送る
- `with_model()` で、接続を共有したまま別のモデルに切り替える。同じプロバイダーなら、APIキー・base_url・オプションを引き継ぐ
- `extra=` による、各社に固有のパラメータの受け渡し
- `extra_by_provider=` で、各社に固有のパラメータをプロバイダーごとに並べて書ける。選んだプロバイダーの分だけを送るので、モデルを切り替えても呼び出し側のコードは変わらない
- `Client(options_by_provider=...)` で、接続先ごとのオプション（Anthropic の `default_max_tokens` など）を並べて書ける。選んだ接続先の分だけを使い、ほかの分も `Client` を作るときに検査する。`with_model()` は別の接続先へも引き継ぐ
- モデルによって断られる指定（強制する `tool_choice`、`reasoning_effort` の段階など）を API が 400 系で断ったら、原因とみられる引数と、どのモデルでも動く書き方を `str(e)` に足す（`e.message` は API の文言のまま）
- README に、コードを直さずにモデルを載せ替える書き方の手引き（英語版と日本語版）
- Anthropic で `max_tokens` を省いたときは 64000 を送る（Anthropic は必須）。今の Claude のモデルすべての上限に収まる値で、ほかの社と同じく、省いたコードが短い上限で切れない

#### 履歴の保存・復元

- `Message.to_dict()` / `from_dict()` で、履歴を JSON に保存・復元する
  - 推論情報（署名・推論）も残すので、復元した会話でも送り返せる
  - `chat()` は、保存した dict のリストをそのまま受け取る

#### 構造化出力

- `chat(response_schema=...)` / `stream(response_schema=...)`
  - JSON Schema の dict を各社の形式で送り、答えをその形に縛る。読んだ値は `Response.parsed` に入る
  - JSON Schema を受け付けない DeepSeek では、送る前に `UnsupportedFeatureError`

#### 推論

- 推論の強さ（`chat(reasoning_effort=...)` / `stream(reasoning_effort=...)`）
  - `none`〜`max` の7段階の名前を、各社の指定（OpenAI `reasoning.effort`、Anthropic `output_config.effort`、Gemini `thinkingLevel`、Chat Completions `reasoning_effort`）に変えずに入れる
  - 各社の API に名前のない段階（Anthropic の `minimal`、Gemini の `none`・`max`）は、送る前に `UnsupportedFeatureError`
- 推論の要約（`chat(reasoning_summary=True)` / `stream(reasoning_summary=True)`）
  - 各社の「要約を返す」指定（OpenAI `reasoning.summary`、Anthropic `thinking.display`、Gemini `includeThoughts`）を送り、返った要約を `Response.thinking` と `thinking` イベントに入れる
  - Chat Completions 互換には何も足さない（DeepSeek は頼まなくても返す）
  - `reasoning_effort="none"` との組み合わせは `ConfigurationError`、OpenAI 本家の Chat Completions は `UnsupportedFeatureError`（どちらも送る前）
- Anthropic の API の版は、最新の `2026-10-21` を送る（`anthropic_version=` で変えられる）。この版はどのモデルでも `temperature` を拒否するので、`temperature=` は送らずに捨て、`DroppedParameterWarning` で知らせる。載せ替えてもコードは止まらず、警告を例外にすれば送る前に止められる

#### ファイルの入力

- `FilePart`（`FilePart.from_file()`）で、PDF などを base64 で送る
  - OpenAI（Responses）と Gemini には、種類を確かめずに送る
  - Anthropic は PDF とテキスト、Chat Completions 互換は PDF だけ
  - DeepSeek・Ollama・LM Studio のプリセットと、受け付けないと分かっている種類は、送る前に `UnsupportedFeatureError`

#### ヘッダー

- `headers=` を、すべてのプロバイダーで受け付ける（ゲートウェイなどに足すヘッダー）
  - ライブラリが付けるヘッダー（APIキーのヘッダーを含む）と同じ名前なら、大文字と小文字を区別せずに置き換え、同じヘッダーを2本送らない
  - APIキーのヘッダーを渡せば、`api_key=` も環境変数もなくてよい
  - 値に `None` を渡すと、ライブラリが付けるそのヘッダーを送らない（ゲートウェイがキーを別の名前のヘッダーで受けるとき、プロバイダーのキーを送らないため）

#### 非同期

- 非同期のクライアント `AsyncClient`
  - 引数とメソッドの名前は `Client` と同じで、`await client.chat(...)`、`async for ev in client.stream(...)`、`async with` / `aclose()`
  - アダプターは同期版と同じものを使う
- `Client` に `httpx.AsyncClient` を、`AsyncClient` に `httpx.Client` を渡すと、作った時点で `TypeError`

#### 送る前の検査

- 送る前に分かる誤りは、リクエストを送らずに例外にする
  - 知らないオプション名
  - `tools` にない `tool_choice`
  - `TextPart` / `ImagePart` / `FilePart` 以外のパーツ
  - user 以外のメッセージに入れた画像とファイル
  - 中身のない画像とファイル
