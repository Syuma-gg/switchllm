# 技術的負債

見つけたら追記する。解消したら行を消し、解消した計画か変更にそのことを書き残す。

| 負債 | 影響 | 対応方針 |
|---|---|---|
| 実 API で検証していない | 実際には動かない箇所があるかもしれない | [実 API 検証](active/real-api-verification.md) |
| 非同期クライアントがない | async のアプリから使いにくい | [モデルの層の機能を足す](active/model-layer-features.md) の 4 |
| Anthropic の API の版が古い（既定は `2023-06-01`） | Anthropic は最新の `2026-10-21` を勧めている。`2026-10-21` では `temperature`・`top_p`・`top_k` を送ると、どのモデルでも 400 になる。`2023-06-01` のままなら今は動く | 上げるなら、共通の `temperature` を Anthropic でどう扱うか（`UnsupportedFeatureError` にするか）を先に決める。`anthropic_version=` で利用者が選べる。2026-11-20 に構造化出力を調べていて見つけた（[Anthropic Messages](../references/anthropic-messages.md) の出典） |
| Gemini が generateContent を使っている | Google はレガシー扱いで、Interactions API を勧めている | 当面は動く見込み。移行するかは要望と Google の動向で決める（U7） |
| 設計文書・CHANGELOG・コメントが日本語だけ（README は英語版がある） | 海外の利用者は、設計の理由や変更履歴を読めない | 要望が出たら、利用者が直接読む CHANGELOG から英語化を検討する |
| `ProviderState` が公開 API にない | `Message.provider_state` の型を利用者が import できない。保存・復元は `Message.to_dict()` / `from_dict()` で済むので、困るのは型ヒントだけ | 型ヒントで必要になったら公開を検討する（2026-11-18 に、履歴の保存のためには公開しないと決めた。[判断](../design-docs/history-serialization.md)） |
| `ImagePart` を `data` も `url` もなしで作れる | 中身のない画像（`data: None` や `base64,None`）をそのまま送り、API のエラーで初めて気づく | 作るときに `ValueError` にするか検討する。公開 API の挙動が変わるので、先に判断を残す |
| 品質検査のテンプレート（`harness/quality-gate/`）が switchllm の中にある | switchllm と関係のない道具がリポジトリに入っている。sdist にも入る | 作者のハーネスをまとめるフォルダーが決まったら移す。移したら、switchllm の写しとの照合（`tests/test_harness.py`）を外すか、移した先を見るように直す（[判断](../design-docs/quality-gate-harness.md)） |
