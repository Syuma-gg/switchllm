# 技術的負債

見つけたら追記する。解消したら行を消し、解消した計画か変更にそのことを書き残す。

| 負債 | 影響 | 対応方針 |
|---|---|---|
| 実 API で検証していない | 実際には動かない箇所があるかもしれない | [実 API 検証](active/real-api-verification.md) |
| 推論の本文（要約）を返させる指定が共通でない | OpenAI の推論モデル・Claude 5 系・Gemini は、頼まなければ推論の要約を返さないので、`Response.thinking` は多くのモデルで `None` になる。頼むには各社の形式を書き分ける（OpenAI `reasoning.summary`、Anthropic `thinking.display`、Gemini `includeThoughts`）。DeepSeek は常に返す | 共通の指定（例: `Client.chat(..., reasoning_summary=True)`）を足すか、要望が出たら決める。Anthropic は `thinking: {"type": "adaptive"}` も一緒に送る必要があり、4.5 系は 400、Opus 4.6〜4.8 では考えるようになる。2026-11-21 に推論の強さを調べていて見つけた（[判断](../design-docs/reasoning-effort.md)） |
| Gemini が generateContent を使っている | Google はレガシー扱いで、Interactions API を勧めている | 当面は動く見込み。移行するかは要望と Google の動向で決める（U7） |
| 設計文書・CHANGELOG・コメントが日本語だけ（README は英語版がある） | 海外の利用者は、設計の理由や変更履歴を読めない | 要望が出たら、利用者が直接読む CHANGELOG から英語化を検討する |
| `ProviderState` が公開 API にない | `Message.provider_state` の型を利用者が import できない。保存・復元は `Message.to_dict()` / `from_dict()` で済むので、困るのは型ヒントだけ | 型ヒントで必要になったら公開を検討する（2026-11-18 に、履歴の保存のためには公開しないと決めた。[判断](../design-docs/history-serialization.md)） |
| `ImagePart` を `data` も `url` もなしで作れる | 中身のない画像（`data: None` や `base64,None`）をそのまま送り、API のエラーで初めて気づく | 作るときに `ValueError` にするか検討する。公開 API の挙動が変わるので、先に判断を残す |
| 品質検査のテンプレート（`harness/quality-gate/`）が switchllm の中にある | switchllm と関係のない道具がリポジトリに入っている。sdist にも入る | 作者のハーネスをまとめるフォルダーが決まったら移す。移したら、switchllm の写しとの照合（`tests/test_harness.py`）を外すか、移した先を見るように直す（[判断](../design-docs/quality-gate-harness.md)） |
