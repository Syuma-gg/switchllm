# 文書の整理

## 目的

md の文書を、読みやすく、直しやすくする。次の3つが済んだら完了。

- 終わった計画を `completed/` に移し、計画の経緯は要点だけにする
- 同じ事実を何か所にも書かない。事実ごとに正本を1つ決め、ほかの文書はリンクする
- 長い段落や表のセルを、箇条書きや短い表に分ける。中身（決めたこと、確かめたこと、出典）は変えない

## 背景

- 2026-11-27 の時点で、md は 52 個、合わせて約 450KB ある
- 1つの機能を足すたびに、約 12 個の md（public-api・scope・README 英日・CHANGELOG・QUALITY_SCORE・e2e-tests・ARCHITECTURE・計画・設計・references など）に同じことを書いていた
- 1行が 400 字を超える段落や表のセルが多い。`anthropic-messages.md` と `real-api-verification.md` は 20 か所を超える
- 作者が、上の3つを直すと決めた（2026-11-27）。e2e-tests.md のケースの一覧を薄くすることは選ばなかった

## 正本の決まり

[docs の索引](../../index.md) の「文書の種類と更新のきっかけ」に書いた（2026-11-27）。

## 変えないもの

- `docs/quality-gates.md` と `harness/`。テンプレートの写しで、`tests/test_harness.py` が一致を検査している
- 決めたこと、確かめたこと、出典の URL と日付
- 不変条件と、ほかの文書から参照されている ID（U1〜U35、S1 など）

## 手順

- [x] 終わった計画を片づける（2026-11-27）
  - `model-layer-features.md`（6つとも実装済み）と `reasoning-summary.md`（実装済み）を `completed/` に移す。残りの実 API の実行は [実 API 検証](../active/real-api-verification.md) に集める
  - `v0.1-release.md`・`real-api-verification.md`・`completed/` の計画の進捗ログを、要点だけにする
- [x] `references/` の4つ（2026-11-27）
- [x] `design-docs/`（2026-11-27）
- [x] `product-specs/`（2026-11-27）
- [x] `docs/` 直下（QUALITY_SCORE・testing・e2e-tests・RELIABILITY・SECURITY・index）（2026-11-27）
- [x] ARCHITECTURE.md・CHANGELOG.md・README（英語・日本語）（2026-11-27）
- [x] 正本の決まりを [docs の索引](../../index.md) に移し、CLAUDE.md の「ドキュメントの保守」から指す（2026-11-27）
- [x] `python -m pytest` と品質検査を通す（2026-11-27）

## 進捗ログ

- 2026-11-27: 終わった計画を片づけた
  - `model-layer-features.md` と `reasoning-summary.md` を `completed/` に移し、残りの実 API の実行を [実 API 検証](../active/real-api-verification.md) に集めた。移した計画の方針は、設計文書にあることを確かめてから計画から外した
  - `v0.1-release.md` は、残りの手順を先頭に出し、済んだ手順を1行ずつにした。手元で配布物を確かめる手順は節に分けて残した
  - `real-api-verification.md` の前提の表（1行が長いセルの表）を、前提ごとの節に分けた
  - 古い計画を読み直して、[載せ替えの負担を減らす](../active/model-switch-friction.md) に関わる前の判断（S12 の `max_tokens`、Gemini の画像の URL と SSRF）を見つけ、その計画に書き足した
- 2026-11-27: `references/` の4つを整えた。長い箇条を細目に分け、出典は、同じページを何度か確かめたものを1つにまとめて日付ごとに並べた。各文書の「関連する未検証の前提」に、抜けていた U 番号（U32〜U35 など）を足した
- 2026-11-27: `design-docs/` は、長い箇条の多い4つ（構造化出力・ファイルの入力・ヘッダー・推論の要約）を、結論の1行と細目に分けた。推論の強さは1か所だけ。ほかの文書は、見出しで分かれていて読みにくくないので、そのままにした
- 2026-11-27: `product-specs/` は、scope の「やること」を1行ずつにして細かい振る舞いをリンク先に任せ、もう要らない「足すと決めたもの」の節を消した。public-api は、表の長いセル（`FilePart`・`headers`・推論の指定）を、表の下の箇条に移した。表の形は `tests/test_docs.py` の照合のために変えていない
- 2026-11-27: QUALITY_SCORE の「メモ」の列は、テストの中身を書き写していたので、「まだ確かめていないこと」の列に替えた。アダプターの表に、抜けていた U 番号を足した。カバレッジとテストの件数を測り直した（99.16%、1034 件）
- 2026-11-27: ARCHITECTURE のテストの表を、テストの中身を書き写さずに対象だけにした（`tests/test_history.py` の行が抜けていたので足した）。「よくある変更」を番号付きの手順にした。CHANGELOG は、機能ごとの見出しに分けた。README は、長い3か所（`tool_choice`・推論の要約の既定・`headers=`）を英日とも箇条に分けた。ほかは、[載せ替えの負担を減らす](../active/model-switch-friction.md) の手引きで書き直すので、そのときに見直す
- 2026-11-27: 完了し、`completed/` に移した
- 2026-11-27: 変えなかったもの: `e2e-tests.md` のケースの一覧（作者が選ばなかった）、`quality-gates.md` と `harness/`（テンプレートの写し）、`async-client.md`・`core-beliefs.md` など、見出しで分かれている文書

## 判断ログ

- 2026-11-27: 整理するのは、終わった計画、重複、読みやすさの3つ（作者の判断）。経緯は要点だけ残し、細かい経緯は git の履歴に任せる（作者の判断。推奨の案）
