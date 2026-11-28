# 技術的負債

見つけたら追記する。解消したら行を消し、解消した計画か変更にそのことを書き残す。

| 負債 | 影響 | 対応方針 |
|---|---|---|
| 実 API で検証していない | 実際には動かない箇所があるかもしれない | [実 API 検証](active/real-api-verification.md) |
| ストリーミングなしの生成が受信の待ち（既定 120 秒）を超えると、タイムアウトしてリトライする | 同じ生成を最大3回頼み、待ちが最大6分ほどになる。切れた接続の分をプロバイダーが課金するなら、料金も重なる。どの接続先でも起き、Anthropic で `max_tokens` を省いたときの既定を 64000 にしてからは Anthropic でも起きうる | 受信の待ちのタイムアウトはリトライしないか、既定を延ばすか、手引きでストリーミングを勧めるだけにするかを決める（[RELIABILITY.md](../RELIABILITY.md)） |
| 画像やファイルと同じリストに、文字列をそのまま入れると `TypeError` になる | `Message.user(["見て", ImagePart(...)])` と書けず、`TextPart("見て")` で包む必要がある。載せ替えとは関係ない | 要望が出たら、リストの中の `str` を `TextPart` として受け付けるか決める |
| Gemini が generateContent を使っている | Google はレガシー扱いで、Interactions API を勧めている | 当面は動く見込み。移行するかは要望と Google の動向で決める（U7） |
| 設計文書・CHANGELOG・コメントが日本語だけ（README は英語版がある） | 海外の利用者は、設計の理由や変更履歴を読めない | 要望が出たら、利用者が直接読む CHANGELOG から英語化を検討する |
| `ProviderState` が公開 API にない | `Message.provider_state` の型を利用者が import できない。保存・復元は `Message.to_dict()` / `from_dict()` で済むので、困るのは型ヒントだけ | 型ヒントで必要になったら公開を検討する（2026-11-18 に、履歴の保存のためには公開しないと決めた。[判断](../design-docs/history-serialization.md)） |
| 品質検査のテンプレート（`harness/quality-gate/`）が switchllm の中にある | switchllm と関係のない道具がリポジトリに入っている。sdist にも入る | 作者のハーネスをまとめるフォルダーが決まったら移す。移したら、switchllm の写しとの照合（`tests/test_harness.py`）を外すか、移した先を見るように直す（[判断](../design-docs/quality-gate-harness.md)） |
