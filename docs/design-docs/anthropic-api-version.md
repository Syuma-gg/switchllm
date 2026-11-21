# Anthropic の API の版は 2026-10-21 を既定にし、temperature は送る前に知らせる

- 状態: 採用（2026-11-21、作者の判断）
- 検証: 一次情報で確認、実 API 未検証（U25）
- コード: `providers/anthropic.py`（`ANTHROPIC_VERSION`、`build_request`）
- テスト: `tests/test_anthropic.py`

## 背景

Anthropic の API は `anthropic-version` ヘッダーで版を選ぶ。switchllm は最初から `2023-06-01` を送っていた。Anthropic は新しい版 `2026-10-21` を出し、「Anthropic recommends using the latest API version whenever possible」としている（[Anthropic Messages](../references/anthropic-messages.md) の「API の版」）。

`2026-10-21` の、Messages API の破壊的変更は3つで、switchllm が組み立てるリクエストのうち当たるのは `temperature` だけ。

- `temperature`・`top_p`・`top_k` を送ると、どのモデルでも、既定の値でも 400
- assistant メッセージの `document` ブロックと、user メッセージの `citations` が 400（switchllm は送らない）

ただし、古い版のままでも、今の Claude（Claude 5 系すべてと Opus 4.8・4.7 の14モデル）は、既定でない `temperature` を thinking の有無にかかわらず 400 にする。古い版で `temperature` が効くのは、Opus 4.6・Sonnet 4.6・4.5 系だけになっている。

2026-11-20 に構造化出力を調べていて見つけ、[負債](../exec-plans/tech-debt-tracker.md) に書いていた。構造化出力の `output_config` を古い版で受け付けるかも、文書にない（U19）。

## 決定

- 既定の版を `2026-10-21` にする。`anthropic_version=` で、`2023-06-01` などほかの版も選べる（今までどおり）
- **版が `2026-10-21` 以降で `temperature=` を渡したら、送る前に `UnsupportedFeatureError` にする。** どのモデルでも 400 になると文書で分かっているので、送って失敗させない（構造化出力で DeepSeek を送る前に弾いたのと同じ考え方）。例外のメッセージで、`temperature=` を外すか、古いモデルなら `anthropic_version="2023-06-01"` を渡すよう案内する
  - 版は `YYYY-MM-DD` の文字列なので、文字列の大小で比べる
  - `anthropic_version="2023-06-01"` なら、今までどおり `temperature` を送る。モデルが拒否すれば API のエラーをそのまま返す
- v0.1 を公開する前に上げる。公開したあとで既定の版を変えると、同じコードが送るリクエストが変わる

## 却下した案

- **`2023-06-01` のままにして、README に書く**: 古いモデルの `temperature` は使い続けられるが、古い版を既定にしたまま公開すると、上げるときに利用者のリクエストが変わる。今のモデルの `temperature` は、どちらの版でも 400 になる。U19 も残る
- **版を上げ、`temperature` は黙って送らない**: 切り替えは楽だが、指定を黙って無視する（基本方針 4）
- **版を上げ、`temperature` をそのまま送って API の 400 を返す**: どのモデルでも必ず 400 になると文書で分かっているものを、料金のかからない失敗とはいえ送ることになる。古い版を選べば使えることも、API のエラーからは分からない
- **モデル名を見て、`temperature` を受け付けないモデルだけ弾く**: モデルの一覧が要る（[実 API 検証](../exec-plans/active/real-api-verification.md) の 2026-11-17 の判断）

## 影響

- Anthropic で `temperature=` を渡していたコードは、`UnsupportedFeatureError` になる。まだ公開していないので、壊れる利用者のコードはない
- モデルを切り替えるとき、`temperature=` を渡したままでは Anthropic へ切り替えられない。今の Claude は古い版でも既定でない値を 400 にするので、切り替えられないのは前からで、送る前に分かるようになった
- E2E の `temperature` のテストは、Anthropic では送る前の例外を確かめる
- U19 は、`anthropic_version="2023-06-01"` を選んだときだけの前提になる。新しい前提として、既定の版で今までのリクエストがすべて通るかを U25 で確かめる
