# 技術的負債

見つけたら追記する。解消したら行を消し、解消した計画か変更にそのことを書き残す。

| 負債 | 影響 | 対応方針 |
|---|---|---|
| 実 API で検証していない | 実際には動かない箇所があるかもしれない | [実 API 検証](active/real-api-verification.md) |
| Bedrock の短期のキー（AWS が本番に勧めるもの）は 12 時間で切れるが、`Client` は作ったときのキーを持ち続け、`with_model()` も引き継ぐ | 長く動くアプリは、12 時間たつと `AuthenticationError` になり、`Client` を作り直すしかない | キーを返す関数を `api_key=` に渡せるようにするか、作り直す書き方を手引きに書くかを決める（公開 API が変わるので作者が決める。2026-11-28 の精査で見つけた） |
| クラウドの経路で、環境変数から組み立てる URL と `base_url=` の扱いが違う | `AZURE_OPENAI_ENDPOINT` には `/openai/v1` を足すが、`base_url=` はそのまま使う。Azure のエンドポイントをそのまま `base_url=` に渡すと 404 になる（`foundry` の `/anthropic` も同じ） | `base_url=` も同じように直すか、README に書くかを決める（2026-11-28 の精査で見つけた） |
| クラウドの経路が、会社での使い方に届きにくい | Vertex AI express mode は個人の @gmail.com アカウントだけで、Workspace の会社では使えない。Azure はキーの認証を止めている会社が多い（Entra ID だけ） | `headers=` に自分で取ったトークンを入れる書き方（期限は利用者が管理する）を確かめて手引きに書くか、要望を待つ（2026-11-28 の精査で見つけた） |
| OpenAI 本家かどうかを、`base_url` が `https://api.openai.com/v1` と同じかで見ている | OpenAI の別の URL（地域のエンドポイントや、OpenAI へ中継するゲートウェイ）では `max_tokens` を送り、推論モデルが 400 にする。`max_tokens_field=` で直せるが気づきにくい | 400 のエラーの案内に `max_tokens_field` を足すか、要望を待つ（2026-11-28 の精査で見つけた） |
| E2E を `--record` で回して失敗したテストは記録されず、再生では黙って外れる | 一度も通っていないテストやモデルがあっても、`python -m pytest` と CI は通る | 記録のないテストを一覧にして見せるか、期待する記録の一覧を持つかを決める（2026-11-28 の精査で見つけた） |
| 画像やファイルと同じリストに、文字列をそのまま入れると `TypeError` になる | `Message.user(["見て", ImagePart(...)])` と書けず、`TextPart("見て")` で包む必要がある。載せ替えとは関係ない | 要望が出たら、リストの中の `str` を `TextPart` として受け付けるか決める |
| Gemini が generateContent を使っている | Google はレガシー扱いで、Interactions API を勧めている | 当面は動く見込み。移行するかは要望と Google の動向で決める（U7） |
| 設計文書・CHANGELOG・コメントが日本語だけ（README は英語版がある） | 海外の利用者は、設計の理由や変更履歴を読めない | 要望が出たら、利用者が直接読む CHANGELOG から英語化を検討する |
| `ProviderState` が公開 API にない | `Message.provider_state` の型を利用者が import できない。保存・復元は `Message.to_dict()` / `from_dict()` で済むので、困るのは型ヒントだけ | 型ヒントで必要になったら公開を検討する（2026-11-18 に、履歴の保存のためには公開しないと決めた。[判断](../design-docs/history-serialization.md)） |
| 品質検査のテンプレート（`harness/quality-gate/`）が switchllm の中にある | switchllm と関係のない道具がリポジトリに入っている。sdist にも入る | 作者のハーネスをまとめるフォルダーが決まったら移す。移したら、switchllm の写しとの照合（`tests/test_harness.py`）を外すか、移した先を見るように直す（[判断](../design-docs/quality-gate-harness.md)） |
