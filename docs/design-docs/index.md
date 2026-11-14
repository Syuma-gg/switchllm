# 設計判断の一覧

各文書は「背景 / 決定 / 却下した案 / 影響」の順に書く。方針を変えたら文書を消さず、状態を「廃止」にして新しい判断へリンクする。

「検証」列は、判断の前提をどこまで確かめたかを表す。実 API での確認は [実 API 検証](../exec-plans/active/real-api-verification.md) で進めている。

| 判断 | 状態 | 検証 |
|---|---|---|
| [基本方針](core-beliefs.md) | 採用 | — |
| [OpenAI 本家は Responses API、互換サーバーは Chat Completions](openai-responses-api.md) | 採用 | 一次情報で確認、実 API 未検証 |
| [生データを ProviderState に退避して同じモデルへ再送する](provider-state-resend.md) | 採用 | 一次情報で確認、実 API 未検証 |
| [主要3社はネイティブ API を使う](native-apis.md) | 採用 | 一次情報で確認 |
| [出力は OpenAI 形式ではなく独自の共通型](common-types.md) | 採用 | — |
| [SDK を使わず httpx で直接呼ぶ](http-without-sdk.md) | 採用 | — |
| [usage の意味を揃える](usage-normalization.md) | 採用 | 調査済み（出典は未記録）、実 API 未検証 |
| [@tool の自動変換は対応する型を固定する](tool-schema-scope.md) | 採用（2026-11-09） | — |
| [プリセットは base_url を確認したものだけ](verified-presets.md) | 採用（2026-11-09） | 公式ドキュメントで確認 |
| [公開 API を最小にする](minimal-public-api.md) | 採用（2026-11-09） | — |
| [品質検査のために品質を落とさない](quality-gates.md) | 採用（2026-11-14） | — |
