# 設計判断の一覧

各文書は「背景 / 決定 / 却下した案 / 影響」の順に書く。方針を変えたら文書を消さず、状態を「廃止」にして新しい判断へリンクする。

「検証」列は、判断の前提をどこまで確かめたかを表す。実 API での確認は [実 API 検証](../exec-plans/active/real-api-verification.md) で進めている。

| 判断 | 状態 | 検証 |
|---|---|---|
| [基本方針](core-beliefs.md) | 採用 | — |
| [OpenAI 本家は Responses API、互換サーバーは Chat Completions](openai-responses-api.md) | 採用 | 一次情報で確認、実 API 未検証 |
| [生データを ProviderState に退避して再送する](provider-state-resend.md) | 採用 | 一次情報で確認、実 API 未検証 |
| [主要3社はネイティブ API を使う](native-apis.md) | 採用 | 一次情報で確認 |
| [出力は OpenAI 形式ではなく独自の共通型](common-types.md) | 採用 | — |
| [SDK を使わず httpx で直接呼ぶ](http-without-sdk.md) | 採用 | — |
| [履歴を to_dict / from_dict で保存・復元する](history-serialization.md) | 採用 | モックで確認、実 API 未検証 |
| [usage の意味を揃える](usage-normalization.md) | 採用 | 一次情報で確認、実 API 未検証 |
| [@tool の自動変換は対応する型を固定する](tool-schema-scope.md) | 採用（2026-11-09） | — |
| [プリセットは base_url を確認したものだけ](verified-presets.md) | 採用（2026-11-09） | 公式ドキュメントで確認 |
| [公開 API を最小にする](minimal-public-api.md) | 採用（2026-11-09） | — |
| [品質検査をハーネスで回し、数字のために品質を落とさない](quality-gate-harness.md) | 採用（2026-11-14）、テンプレート化（2026-11-15） | — |
| [プロバイダー固有の指定は、各社の分を並べて渡す](provider-specific-params.md) | 採用（2026-11-15）、`Client(...)` のオプションに広げた（2026-11-28） | — |
| [構造化出力は response_schema= で JSON Schema を受け取る](structured-output.md) | 採用（2026-11-20） | 一次情報で確認、実 API 未検証 |
| [推論の強さは reasoning_effort= で段階の名前を受け取る](reasoning-effort.md) | 採用（2026-11-21） | 一次情報で確認、実 API 未検証 |
| [推論の要約は reasoning_summary=True で頼む](reasoning-summary.md) | 採用（2026-11-26） | 一次情報で確認、実 API 未検証 |
| [Anthropic の API の版は 2026-10-21 を既定にし、temperature は送る前に知らせる](anthropic-api-version.md) | 採用（2026-11-21）。temperature の扱いは 2026-11-28 に下の判断へ変えた | 一次情報で確認、実 API 未検証 |
| [Anthropic で max_tokens を省いたら 64000 を送る](anthropic-max-tokens.md) | 採用（2026-11-28） | 一次情報で確認、実 API 未検証 |
| [Anthropic の temperature は送らずに捨て、DroppedParameterWarning で知らせる](dropped-parameter-warning.md) | 採用（2026-11-28） | 一次情報で確認、実 API 未検証 |
| [API が断った指定には、原因とみられる引数と、どのモデルでも動く書き方をエラーに足す](error-hints.md) | 採用（2026-11-28） | 一次情報で確認（Anthropic の文言）、実 API 未検証 |
| [プリセットごとに送れるものを対応表にし、Gemini には URL の画像を送り、Ollama の tool_choice と画像の URL は送る前に止める](provider-capabilities.md) | 採用（2026-11-28） | 一次情報で確認、実 API 未検証 |
| [クラウドの API キーで呼べる経路をプリセットにする](cloud-api-keys.md) | 採用（2026-11-28） | 一次情報で確認、実 API 未検証 |
| [受信の待ちを 600 秒にし、受信の待ちのタイムアウトはリトライしない](read-timeout.md) | 採用（2026-11-28） | モックで確認、公式 SDK の既定は文書で確認 |
| [非同期のクライアントは AsyncClient として分け、アダプターを同期版と共有する](async-client.md) | 採用（2026-11-23） | モックで確認、実 API 未検証 |
| [headers= はすべてのアダプターで受け付け、同じ名前のヘッダーは利用者の値で置き換える](custom-headers.md) | 採用（2026-11-23） | モックで確認（RFC 9110） |
| [ファイル（PDF など）は FilePart で渡し、各社が受け付ける種類はそのまま送る](file-input.md) | 採用（2026-11-23） | モックで確認、実 API 未検証 |
