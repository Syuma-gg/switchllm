# 技術的負債

見つけたら追記する。解消したら行を消し、解消した計画か変更にそのことを書き残す。

| 負債 | 影響 | 対応方針 |
|---|---|---|
| 実 API で検証していない | 実際には動かない箇所があるかもしれない | [実 API 検証](active/real-api-verification.md) |
| 非同期クライアントがない | async のアプリから使いにくい | [スコープ](../product-specs/scope.md) の「今後足す候補」 |
| Gemini が generateContent を使っている | Google はレガシー扱いで、Interactions API を勧めている | 当面は動く見込み。移行するかは要望と Google の動向で決める（U7） |
| 設計文書・CHANGELOG・コメントが日本語だけ（README は英語版がある） | 海外の利用者は、設計の理由や変更履歴を読めない | 要望が出たら、利用者が直接読む CHANGELOG から英語化を検討する |
| `ProviderState` が公開 API にない | `Message.provider_state` の型を利用者が import できない | 型ヒントで必要になったら公開を検討する |
| ストリーミング中のエラーイベントのテストが一部のアダプターにない | 実 API で初めて壊れていると分かる可能性がある | [QUALITY_SCORE.md](../QUALITY_SCORE.md) の「足りないテスト」を参照 |
| 接続エラーのリトライ、`Retry-After` の解釈、エラー本体の解釈にテストがない（カバレッジ 87.68%） | 失敗したときの挙動が壊れても気づけない | テストを足し、`pyproject.toml` の `fail_under` を引き上げる（[品質検査の方針](../quality-gates.md)） |
| `ImagePart` を `data` も `url` もなしで作れる | 中身のない画像（`data: None` や `base64,None`）をそのまま送り、API のエラーで初めて気づく | 作るときに `ValueError` にするか検討する。公開 API の挙動が変わるので、先に判断を残す |
| 品質検査のテンプレート（`harness/quality-gate/`）が switchllm の中にある | switchllm と関係のない道具がリポジトリに入っている。sdist にも入る | 作者のハーネスをまとめるフォルダーが決まったら移す。移したら、switchllm の写しとの照合（`tests/test_harness.py`）を外すか、移した先を見るように直す（[判断](../design-docs/quality-gate-harness.md)） |
