# 受信の待ちを 600 秒にし、受信の待ちのタイムアウトはリトライしない

- 状態: 採用（2026-11-28）
- 検証: モックで確認（`tests/test_http.py`）。公式 SDK の既定は文書で確認
- コード: `client.py`（`DEFAULT_TIMEOUT`・`CONNECT_TIMEOUT`）、`_http.py`（`_retry_transport`・`_connection_error`）
- 振る舞いの正本: [RELIABILITY.md](../RELIABILITY.md)

## 背景

- これまでの既定は、接続 10 秒・受信の待ち 120 秒で、接続の失敗（`httpx.TransportError`）はどれも最大2回リトライしていた
- 受信の待ちは、次のデータが届くまでの時間。ストリーミングなしでは、応答の全体ができるまで何も届かない。推論の長いモデルや長い出力では、生成に 120 秒を超えることがある
- 超えると `httpx.ReadTimeout` になり、同じ生成を最大3回頼んでいた
  - 待ちは最大で約6分になり、最後は `APIConnectionError` になる
  - 切れた接続の分をプロバイダーが課金するなら、料金も重なる
  - Anthropic で `max_tokens` を省いたときの既定を 64000 にしてから（[判断](anthropic-max-tokens.md)）、Anthropic でも起きうる
- 2026-11-28 の精査で、負債の中で利用者の不満に一番つながると判断した（[tech-debt-tracker](../exec-plans/tech-debt-tracker.md) にあった行）
- 公式の SDK（2026-11-28 に README と文書で確認）
  - OpenAI Python SDK: 「By default requests time out after 10 minutes」。接続エラー・408・409・429・5xx をリトライし、「requests that time out are retried twice by default」
  - Anthropic Python SDK: 「By default requests time out after 10 minutes」。「It retries connection errors, timeouts, 408 Request Timeout, 409 Conflict, 429 Too Many Requests, and 5xx server errors」。ストリーミングなしで約 10 分を超えそうな `max_tokens` を送ると `ValueError` にし、「Avoid setting a large `max_tokens` value without using streaming」と書く
  - 出典: https://github.com/openai/openai-python 、https://platform.claude.com/docs/en/api/sdks/python

## 決定

作者の判断（2026-11-28）。

- 受信の待ちの既定を 600 秒にする（公式の2つの SDK と同じ 10 分）。接続の待ちは 10 秒のまま。`Client(timeout=...)` で変えられるのも同じ
- 受信の待ちのタイムアウト（`httpx.ReadTimeout`）は、リトライしない。ストリーミングでも、最初のイベントの前に起きたらリトライしない
  - API が受け付けて生成している最中のことが多く、送り直すと同じ生成を重ねて頼むため
  - `APIConnectionError` にし、メッセージで「リトライしなかったこと」と「長い生成は `stream()` で受けるか、`timeout=` を延ばすこと」を伝える（ストリーミングでは `timeout=` だけ）。元の `httpx.ReadTimeout` は `__cause__` で辿れる
- ほかの接続の失敗は、これまでどおりリトライする
  - 接続できない・接続のタイムアウト・接続の取り出しの待ち（`ConnectError`・`ConnectTimeout`・`PoolTimeout`）: リクエストが API に届いていない
  - 受信の途中で切れた（`ReadError` など）: 中継のサーバーが切ることが多く、送り直せば通ることが多い。ストリーミングの途中で切れたときは、これまでどおりリトライしない

## 却下した案

- **リトライしないだけにする（120 秒のまま）**: 重ねて頼むことはなくなるが、推論の長い呼び出しは 120 秒で切れたまま
- **待ちを延ばすだけにする（リトライは残す。公式 SDK と同じ）**: 切れにくくなるが、切れたときは最大3回頼み、待ちは最大30分になる
- **手引きでストリーミングを勧めるだけにする**: 既定のまま使った利用者が、知らずに重ねて頼む
- **Anthropic の SDK のように、`max_tokens` から時間を見積もって送る前に止める**: モデルごとの出力の速さの一覧が要る（[基本方針](core-beliefs.md) の、モデルごとの一覧を持たない方針に合わない）

## 影響

- 既定の待ちが長くなるので、応答しないサーバーに気づくまで最長 10 分かかる。早く知りたい利用者は `timeout=` を短くする
- 受信の待ちのタイムアウトを、自分でリトライしていた利用者はいない（0.1.0 は未公開）
- E2E の待ちも、ライブラリの既定と同じにした（`e2e/conftest.py` の `TIMEOUT`）
- 公開 API の形は変わらない。既定の値と振る舞いが変わるので、CHANGELOG に書く
