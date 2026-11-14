# OpenAI 本家は Responses API、互換サーバーは Chat Completions

- 状態: 採用
- 検証: 一次情報で確認、実 API 未検証（U1〜U3、[実 API 検証](../exec-plans/active/real-api-verification.md)）
- コード: `providers/openai_responses.py`、`providers/openai_chat.py`

## 背景

OpenAI の移行ガイドは Chat Completions を引き続きサポートするとしつつ、「GPT-5.4 以降、Chat Completions は `reasoning_effort` が `none` 以外のときツール呼び出しに対応しない」と明記している。

一方、Ollama・vLLM・LM Studio などの「OpenAI 互換」サーバーが話すのは Chat Completions 形式。

## 決定

- `openai:` は Responses API（`POST /v1/responses`）を使う
- 互換サーバー用に Chat Completions のアダプター（`openai_chat`）を別に持つ。OpenAI 本家を Chat Completions で呼びたいときは `openai-chat:` を使う

## 却下した案

- **Chat Completions だけで済ませる**: アダプターは1つ減るが、推論とツールを両立できないモデルが出てくる

## 影響

- アダプターが4つになる
- Responses API には `stop` シーケンスがないので、`stop=` は `UnsupportedFeatureError` になる

## 出典

- [OpenAI: Migrate to the Responses API](https://developers.openai.com/api/docs/guides/migrate-to-responses)
