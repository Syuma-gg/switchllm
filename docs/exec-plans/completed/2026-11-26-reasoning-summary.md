# 推論の要約を返させる共通の指定

## 目的

`chat()` / `stream()`（`AsyncClient` も）に `reasoning_summary=True` を渡せば、どのプロバイダーでも推論の要約（DeepSeek などは推論の本文）を `Response.thinking` と `StreamEvent("thinking")` で受け取れるようにする。実装・テスト・文書をそろえたら完了。

方針と各社へ送る形は [推論の要約](../../design-docs/reasoning-summary.md) にある。実 API での実行は、[実 API 検証](../active/real-api-verification.md) の `--record` にまとめた（U32〜U35）。コミットは `ee00fb9`。

## 背景

- それまでは、各社の形（OpenAI `reasoning.summary`、Anthropic `thinking.display`、Gemini `includeThoughts`）を利用者が調べ、`extra_by_provider=` に書き分けていた。Anthropic で `adaptive` を一緒に送ったときの副作用（モデルによって考えるようになる、400 になる）も、利用者が調べることになっていた
- 2026-11-21 に [推論の強さ](../../design-docs/reasoning-effort.md) と [スコープ](../../product-specs/scope.md) で「共通化しない」と決めていた。2026-11-26 に作者の判断で覆した

## 手順

- [x] 方針を作者と決める（2026-11-26）
- [x] 一次情報を確かめ、`docs/references/` に書く（2026-11-26）
- [x] 設計文書を書き、推論の強さとスコープの「共通化しない」を直す（2026-11-26）
- [x] テストを先に書き、変更前のコードで失敗することを確かめる（2026-11-26。`tests/test_reasoning_summary.py` の 76 件）
- [x] 実装する（2026-11-26）
- [x] わざと壊した実装で、新しいテストが失敗することを確かめる（2026-11-26。16 通りすべて）
- [x] E2E のケース（`e2e/test_e2e_reasoning_summary.py`）を足し、確かめる前提を U32〜U35 として実 API 検証の前提の一覧に足す（2026-11-26）
- [x] 文書を直す（2026-11-26）
- [x] 実 API での実行を [実 API 検証](../active/real-api-verification.md) に移す（2026-11-27）

## 進捗ログ

- 2026-11-26: 結果を読む側（`Response.thinking` と `StreamEvent("thinking")`）は、4アダプターとも前から対応していた。変えたのは送る側だけ
- 2026-11-26: 最初は作者に「`extra=` だとモデルを変えるとコードも変わる」と伝えたが、不正確だった。`extra_by_provider=` に各社の分を並べれば、切り替えても壊れない。足す理由は「各社の形式と副作用を利用者が調べなくて済む」こと
- 2026-11-26: push のあと、README の「Known limitations」に古い案内（要約は `extra_by_provider=` で頼む）が残っているのを見つけ、直した

## 判断ログ

- 2026-11-26: 名前は `reasoning_summary`（作者の判断）。`reasoning_effort=` と対になる。却下: `include_thinking`（返す先の `Response.thinking` とは合うが、`reasoning_effort=` と並ばない）
- 2026-11-26: 既定は `False`（作者の判断）。`True` にすると、Anthropic 4.5 系が毎回 400 になり、Opus 4.6〜4.8 は考えるようになって料金と待ち時間が増える
- 2026-11-26: `openai_chat` は何も送らず、返ってきたら入れる（作者の判断）。却下: DeepSeek 以外は `UnsupportedFeatureError`（ローカルのモデルを試すときに使えない）
- 2026-11-26: OpenAI 本家の Chat Completions を止めるのは、プリセットのオプションではなく接続先（`base_url`）で決める（実装のときに決めた）。止める理由はサーバーの制約で、プリセットの名前ではないため
- 2026-11-26: Anthropic には `type: "adaptive"` も送る。`type` は必須で、`display` だけでは送れない
