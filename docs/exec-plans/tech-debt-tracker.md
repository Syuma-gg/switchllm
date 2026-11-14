# 技術的負債

見つけたら追記する。解消したら行を消し、解消した計画か変更にそのことを書き残す。

| 負債 | 影響 | 対応方針 |
|---|---|---|
| 実 API で検証していない | 実際には動かない箇所があるかもしれない | [実 API 検証](active/real-api-verification.md) |
| 非同期クライアントがない | async のアプリから使いにくい | [スコープ](../product-specs/scope.md) の「今後足す候補」 |
| Gemini が generateContent を使っている | Google はレガシー扱いで、Interactions API を勧めている | 当面は動く見込み。移行するかは要望と Google の動向で決める（U7） |
| バージョン番号が2か所にある | 更新漏れが起きる | [v0.1 公開](active/v0.1-release.md) で一本化する |
| CI がない | テストの実行が手元任せになる | [v0.1 公開](active/v0.1-release.md) で GitHub Actions を設定する |
| ドキュメントとコメントが日本語だけ | 海外の利用者に届きにくい | v0.1 公開のときに README の英語版を検討する |
| `ProviderState` が公開 API にない | `Message.provider_state` の型を利用者が import できない | 型ヒントで必要になったら公開を検討する |
| ストリーミング中のエラーイベントのテストが一部のアダプターにない | 実 API で初めて壊れていると分かる可能性がある | [QUALITY_SCORE.md](../QUALITY_SCORE.md) の「足りないテスト」を参照 |
