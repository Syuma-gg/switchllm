# 推論の要約を返させる共通の指定

## 目的

`chat()` / `stream()`（`AsyncClient` も）に `reasoning_summary=True` を渡せば、どのプロバイダーでも推論の要約（DeepSeek などは推論の本文）を `Response.thinking` と `StreamEvent("thinking")` で受け取れるようにする。モデルの文字列を変えるだけで、ほかのコードは変えずに済むようにする。

実装・テスト・文書をそろえ、E2E のケースを [実 API 検証](real-api-verification.md) の記録に含めたら完了。

## 背景

- 今は各社の形（OpenAI `reasoning.summary`、Anthropic `thinking.display`、Gemini `includeThoughts`）を利用者が調べ、`extra_by_provider=` に書き分ける。`extra_by_provider=` なら切り替えても壊れないが、各社の形式と、Anthropic で `adaptive` を一緒に送ったときの副作用（モデルによって考えるようになる、400 になる）を利用者が調べることになる。`extra=` に書くと、ほかの社に知らないフィールドが送られる。[基本方針](../../design-docs/core-beliefs.md) の「Client のパラメータ1つだけを変えればよい」に届いていない（負債の一覧にあった）
- 2026-11-21 に [推論の強さ](../../design-docs/reasoning-effort.md) と [scope.md](../../product-specs/scope.md) で「共通化しない」と決めていた。2026-11-26 に作者の判断で覆す
- 結果を読む側は4アダプターとも対応済み（要約を `Response.thinking` と `StreamEvent("thinking")` に入れる）。変えるのは送る側だけ

## 方針

- 引数は `reasoning_summary: bool = False`。`reasoning_effort=` と同じく呼び出しごとに渡す。bool 以外は `TypeError`
- `True` のとき、各社へ次を送る。`extra=` で同じ場所を指定したら `extra=` が勝つ（いつもの深いマージ）

| アダプター | 送る形 | 備考 |
|---|---|---|
| `openai` | `reasoning: {"summary": "auto"}` | `effort` と同じオブジェクトに並ぶ。`auto` は多くのモデルで `detailed` と同じ |
| `anthropic` | `thinking: {"type": "adaptive", "display": "summarized"}` | `display` だけでは送れない（`type` が必須）。Opus 4.6〜4.8・Sonnet 4.6 は考えるようになる。4.5 系は 400 |
| `gemini` | `generationConfig.thinkingConfig.includeThoughts: true` | `thinkingLevel` と同じオブジェクトに並ぶ |
| `openai_chat` | 何も足さない | Chat Completions に要約を頼む標準の指定がない。DeepSeek は頼まなくても `reasoning_content` を返す。接続先が OpenAI 本家（`https://api.openai.com/v1`）のときだけ `UnsupportedFeatureError` にする（下の判断ログ） |

- 段階（OpenAI の `concise`・`detailed`）は受け付けない。OpenAI にしかないので、受け付けるとほかの社では `UnsupportedFeatureError` になり、切り替えを妨げる。使うなら `extra=`
- `reasoning_effort="none"` と一緒に指定したら、送る前に `ConfigurationError`。推論を切って本文を頼むのは矛盾する。Anthropic では `display` が `type: "disabled"` で無効（「there is nothing to display」）
- モデルが受け付けないときは、API のエラーをそのまま返す（推論しないモデル、Anthropic 4.5 系の `adaptive`）。推論の強さと同じく、モデルの一覧を持たない
- Anthropic 4.5 系で使うときは `extra={"thinking": {"type": "enabled", "budget_tokens": N}}` と一緒に渡す。深いマージで `{"type": "enabled", "budget_tokens": N, "display": "summarized"}` になる（`display` は `enabled` でも使える）
- 履歴の再送（`ProviderState`）は変えない。Anthropic は会話の途中で `display` を変えてよく、`signature` は `display` によらず同じ

## 手順

- [x] 方針を作者と決める（2026-11-26。下の判断ログ）
- [x] 一次情報を確かめ、`docs/references/` に書く（2026-11-26）
- [x] 設計文書 [推論の要約](../../design-docs/reasoning-summary.md) を書き、[推論の強さ](../../design-docs/reasoning-effort.md) の「共通化しない」の項目と [scope.md](../../product-specs/scope.md) を直す（2026-11-26）
- [x] テストを先に書き、変更前のコードで失敗することを確かめる（2026-11-26。`tests/test_reasoning_summary.py` の 76 件が、引数がないために全部失敗した）
- [x] 実装する（2026-11-26。`ChatRequest.reasoning_summary`、`Client` / `AsyncClient`、4アダプター）
- [x] わざと壊した実装で、新しいテストが失敗することを確かめる（2026-11-26。16通りすべて）
- [x] E2E のケースを足す（2026-11-26。`e2e/test_e2e_reasoning_summary.py`。1モデル最大 3 回）。下の「確かめる前提」を、実 API 検証の表に U32〜U35 として足した
- [x] 文書を直す（2026-11-26。public-api.md、references、README 英日、CHANGELOG、e2e-tests.md、QUALITY_SCORE.md、ARCHITECTURE.md、負債の行を消した）
- [ ] 実 API 検証の `--record` に含める

## 確かめる前提（U32 以降の候補）

- OpenAI: 組織の認証をしていないときに要約を頼むと、エラーか、要約が返らないだけか（文書は「you may need to complete organization verification」とだけ書く）。推論しないモデルに `reasoning.summary` を送ったとき
- Anthropic: Claude 5 系で `adaptive` + `summarized` が要約を返すこと。Opus 4.6・Sonnet 4.6 で `temperature` と一緒に送ったとき（考えている間は `temperature` と使えない、と要約でしか読めていない）
- Gemini: 推論しないモデルに `includeThoughts` を送るとエラーか（API リファレンスの要約でしか読めていない）。「thoughts are returned only when available」なので、頼んでも返らないことがある
- Ollama・LM Studio: 何も送らなくても、互換 API が推論テキストを返すか。返すならどのフィールドか（どちらの文書にも書かれていない）

## 進捗ログ

- 2026-11-26: 作者と方針を決め、各社の文書（Anthropic Thinking・Extended thinking・Messages API、OpenAI Reasoning models・Create a response、Gemini Thinking・API リファレンス、Ollama・LM Studio の互換 API）を読んで references に書いた。分かったこと: Anthropic の `display` は `type` と一緒にしか送れない。Opus 4.8・4.7 も `display` の既定は `"omitted"`（references の記述を直した）。要約でも省いても料金は同じで、省くと待ち時間が減る。OpenAI は要約に組織の認証が要ることがある。Gemini の要約は「returned only when available」
- 2026-11-26: 実装した。先に書いた `tests/test_reasoning_summary.py`（76 件。`api` の fixture で同期と非同期の両方）は、変更前のコードで全部失敗した。実装のあと全体で 1034 件が通った。実装にわざと誤りを16通り入れ（要約の値・場所・`effort` の上書き・`extra=` に負ける・OpenAI 本家で止めない／接続先を見ずに止める・互換サーバーに独自のフィールドを足す・`none` との組み合わせ・bool 以外・`ChatRequest` に渡さない など）、どれも新しいテストが見つけた。結果を読む側は変えていない
- 2026-11-26: 背景を直した。最初は「`extra=` はどのプロバイダーにもマージするので、モデルを変えるとコードも変わる」と作者に伝えたが、`extra_by_provider=` に各社の分を並べれば切り替えても壊れない（README もそう案内していた）。足す理由は「各社の形式と副作用を利用者が調べなくて済む」こと

## 判断ログ

- 2026-11-26: 名前は `reasoning_summary`（作者の判断）。`reasoning_effort=` と対になる。却下: `include_thinking`（返す先の `Response.thinking` と合い、DeepSeek は要約ではなく本文を返すので意味は正確だが、`reasoning_effort=` と並ばない）
- 2026-11-26: 既定は `False`（作者の判断）。`True` にすると、今のコードで Anthropic 4.5 系が毎回 400 になり、Opus 4.6〜4.8 は考えるようになって料金と待ち時間が増える。OpenAI で組織の認証が要るなら、そこでも失敗する
- 2026-11-26: `openai_chat` は何も送らず、返ってきたら入れる（作者の判断）。却下: DeepSeek 以外は `UnsupportedFeatureError`（安全だが、ローカルのモデルを試すときに使えない）
- 2026-11-26: OpenAI 本家の Chat Completions を止めるのは、プリセットのオプションではなく接続先（`base_url`）で決める（実装のときに決めた。`max_tokens_field` と同じ）。止める理由はサーバーの制約で、プリセットの名前ではない。`openai-chat:` に別の `base_url`（OpenRouter など）を渡したときは使える。新しいオプション名も要らない
- 2026-11-26: Anthropic は `type: "adaptive"` も送る。`type` は必須で、`display` だけでは送れない（Messages API のスキーマ）。Claude 5 系は送らなくても adaptive で考えるので、変わるのは Opus 4.6〜4.8・Sonnet 4.6（考えるようになる）と 4.5 系（400）だけ
