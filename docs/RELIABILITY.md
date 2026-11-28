# 信頼性

失敗したときにどう振る舞うか。実装は `src/switchllm/_http.py` と `src/switchllm/errors.py` にある。非同期の `AsyncClient` も、ここに書いたことはすべて同期の `Client` と同じ（[判断](design-docs/async-client.md)）。

## タイムアウト

- 既定は、接続 10 秒、受信の待ち 120 秒（`Client(timeout=...)` で変更できる）
  - 受信の待ちは、次のデータが届くまでの時間で、全体の時間ではない（httpx の `read`。送信の `write` と接続の取り出しの `pool` も同じ値）
  - ストリーミングなしでは、応答の全体ができるまで何も届かないので、生成に 120 秒を超えるとタイムアウトになる。ストリーミングでは、イベントが届くたびに数え直す
- 自前の `httpx.Client`（`AsyncClient` では `httpx.AsyncClient`）を `http_client=` で渡すと、そちらの設定が使われる

## 自動リトライ

| 対象 | 扱い |
|---|---|
| HTTP 408 / 409 / 429 / 500 / 502 / 503 / 504 / 529 | リトライする |
| 接続エラー、タイムアウト（`httpx.TransportError`） | リトライする |
| それ以外の 4xx | すぐに例外 |

- 回数は `max_retries`（既定 2。初回と合わせて最大3回送る）
- 待ち時間は `retry_backoff`（既定 0.5 秒）× 2^回数。上限は 8 秒で、±25% の揺らぎを入れる
- `Retry-After` ヘッダーが秒数なら、それに従う（0〜60 秒に収める）。秒数として読めないもの（HTTP-date 形式など）は、上の待ち時間にする
- `AsyncClient` は `asyncio.sleep` で待つので、待つ間もほかのタスクが動く

## ストリーミング

- リトライするのは**最初のイベントを受け取る前だけ**。途中まで受信してから切れた場合は、再送すると内容が重複するので `APIConnectionError` にする
- 途中でプロバイダーがエラーイベントを送ってきたら `StreamError`
- 途中で抜けたとき、同期の `stream()` はジェネレーターが片づけられると接続を閉じる。`AsyncClient.stream()` も同じで、すぐに閉じたいときは `contextlib.aclosing()` で囲む
- 最後の `done` は1回だけ届く。終わりの合図（Responses の `response.completed`、Anthropic の `message_stop`、Chat Completions の `finish_reason` か `[DONE]`、Gemini の `finishReason`）が来ないまま接続が閉じたら、どのアダプターも `StreamError` にする。途中までを正常終了として返すと、切れたことに気づけないため（U16）

## 例外の対応

| HTTP | 例外 |
|---|---|
| 401 / 403 | `AuthenticationError` |
| 404 | `NotFoundError` |
| 429 | `RateLimitError` |
| 5xx | `ServerError` |
| その他の 4xx | `BadRequestError` |
| 2xx だが、本文が JSON のオブジェクトでない（間に入るプロキシが HTML を返したときなど） | `APIError` |

どれも `APIError` のサブクラスで、基底は `SwitchLLMError`。リクエスト前に分かる設定ミスは `ConfigurationError`、プロバイダーが対応していない指定は `UnsupportedFeatureError` になる。

## API が返したエラーを見せる

プロバイダーが返したエラーは、手を加えずに利用者へ渡す。ライブラリの側で言い換えたり、別の指定に替えて送り直したりはしない（モデルごとの制限は増え続けるので、先回りして弾くと古くなる）。

| 属性 | 中身 |
|---|---|
| `message` | API が返したエラーメッセージそのもの。`{"error": {"message": ...}}` 系の形から取り出し、知らない形なら本文の先頭 500 文字をそのまま使う。本文が空なら HTTP の理由句 |
| `body` | エラーの本文の全体（JSON なら解釈したもの、そうでなければ文字列）。Anthropic の `error.type` や `request_id` もここに残る |
| `status_code` | HTTP ステータス（ストリーミング途中のエラーイベントでは `None`） |
| `provider` | モデル文字列の `provider:` 部分 |

`str(e)` は `[anthropic] <message> (HTTP 400)` の形で、前後にプロバイダー名と HTTP ステータスを付ける。

`BadRequestError`（400 系）で、送った共通の引数（強制する `tool_choice`・`reasoning_effort`・`reasoning_summary`・`response_schema`・`temperature`）の各社でのフィールド名がエラーの文言か本文に出てくると、`str(e)` の後ろに `\nHint (<引数>): <どのモデルでも動く書き方>` の行を足す。`message` と `body` は変えない。モデル名は見ない（[判断](design-docs/error-hints.md)）。

ストリーミングの途中で届いたエラーイベント（`StreamError`）も、イベントの中の文言を `message` に、イベント全体を `body` に入れる。

## 検証状況

実際の 429 / 5xx / 切断での挙動は未確認（モックのみ。[品質評価](QUALITY_SCORE.md)）。
