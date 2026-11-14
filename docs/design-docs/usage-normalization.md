# usage の意味を揃える

- 状態: 採用
- 検証: 調査済み（出典は未記録）、実 API 未検証（U14、[実 API 検証](../exec-plans/active/real-api-verification.md)）
- コード: 各アダプターの `parse_response`

## 背景

同じ「input_tokens」でも、プロバイダーによって中身が違う。

- Anthropic の `input_tokens` は、キャッシュの読み書き分を含まない
- Gemini は推論トークンを `thoughtsTokenCount` として、出力とは別に返す

## 決定

`Usage.input_tokens` は「処理した入力トークンの総数（キャッシュの読み書きを含む）」、`output_tokens` は「推論トークンを含む出力の総数」に揃える。生の内訳は `Usage.details` に残す。

| プロバイダー | `input_tokens` | `output_tokens` |
|---|---|---|
| OpenAI Responses | `input_tokens` | `output_tokens` |
| Chat Completions 互換 | `prompt_tokens` | `completion_tokens` |
| Anthropic | `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` | `output_tokens` |
| Gemini | `promptTokenCount` | `candidatesTokenCount + thoughtsTokenCount` |

## 影響

- 料金を計算する利用者は `details` を見る必要がある（キャッシュは単価が違う）
