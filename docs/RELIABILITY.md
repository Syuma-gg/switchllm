# 信頼性

失敗したときにどう振る舞うか。実装は `src/unillm/_http.py` と `src/unillm/errors.py` にある。

## タイムアウト

- 既定は全体 120 秒、接続 10 秒（`Client(timeout=...)` で変更できる）
- 自前の `httpx.Client` を `http_client=` で渡すと、そちらの設定が使われる

## 自動リトライ

| 対象 | 扱い |
|---|---|
| HTTP 408 / 409 / 429 / 500 / 502 / 503 / 504 / 529 | リトライする |
| 接続エラー、タイムアウト（`httpx.TransportError`） | リトライする |
| それ以外の 4xx | すぐに例外 |

- 回数は `max_retries`（既定 2。初回と合わせて最大3回送る）
- 待ち時間は `retry_backoff`（既定 0.5 秒）× 2^回数。上限は 8 秒で、±25% の揺らぎを入れる
- `Retry-After` ヘッダーがあれば、それに従う（上限 60 秒）

## ストリーミング

- リトライするのは**最初のイベントを受け取る前だけ**。途中まで受信してから切れた場合は、再送すると内容が重複するので `APIConnectionError` にする
- 途中でプロバイダーがエラーイベントを送ってきたら `StreamError`
- 最後の `done` は1回だけ届く。OpenAI Responses で `response.completed` が来ないまま終わったら `StreamError`

## 例外の対応

| HTTP | 例外 |
|---|---|
| 401 / 403 | `AuthenticationError` |
| 404 | `NotFoundError` |
| 429 | `RateLimitError` |
| 5xx | `ServerError` |
| その他の 4xx | `BadRequestError` |

どれも `APIError`（`provider` / `status_code` / `body` を持つ）のサブクラスで、基底は `UniLLMError`。リクエスト前に分かる設定ミスは `ConfigurationError`、プロバイダーが対応していない指定は `UnsupportedFeatureError` になる。

## 検証状況

実際の 429 / 5xx / 切断での挙動は未確認（モックのみ。[品質評価](QUALITY_SCORE.md)）。
