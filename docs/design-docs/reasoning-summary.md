# 推論の要約は reasoning_summary=True で頼む

- 状態: 採用（2026-11-26、作者の判断）
- 検証: 一次情報で確認、実 API 未検証（U32〜U35。E2E の `test_e2e_reasoning_summary.py`）
- コード: `client.py`（`reasoning_summary=`、型と `reasoning_effort="none"` との組み合わせの検査）、`providers/base.py`（`ChatRequest.reasoning_summary`）、4つのアダプターの `build_request`
- テスト: `tests/test_reasoning_summary.py`
- 計画: [推論の要約を返させる共通の指定](../exec-plans/completed/2026-11-26-reasoning-summary.md)

## 背景

推論するモデルの多くは、頼まなければ推論の本文（要約）を返さない。返させる指定は各社で場所も形も違う（[各社の形式](../references/index.md) の「推論の強さ」）。

| 接続先 | 返させる指定 | 頼まないとき |
|---|---|---|
| OpenAI Responses | `reasoning.summary`（`auto`・`concise`・`detailed`） | 返さない（「This output will not be included unless you explicitly opt in」） |
| Anthropic | `thinking.display: "summarized"`（`type` と一緒に送る。`type` は必須） | Claude 5 系・Opus 4.8・4.7 は返さない（`display` の既定が `omitted`）。Opus 4.6・Sonnet 4.6 以前は返すが、Opus 4.6〜4.8・Sonnet 4.6・4.5 系は `thinking` を送らなければ考えない |
| Gemini | `thinkingConfig.includeThoughts: true` | 既定の値は書かれていない（例はどれも明示している） |
| DeepSeek | ない | 思考モード（既定で有効）では、いつも `reasoning_content` を返す |
| Ollama・LM Studio（Chat Completions 互換） | 互換 API にはない | 返すか、どのフィールドかが書かれていない |
| OpenAI Chat Completions | ない | 返さない（要約の文書は Responses の形だけ） |

- `Response.thinking` と `StreamEvent("thinking")` はあるのに、多くのモデルで `None` になる
- 返させるには、利用者が各社の形式を調べて `extra_by_provider=` に書き分けるしかなかった。Anthropic では、`adaptive` を一緒に送ると、モデルによって考えるようになったり 400 になったりすることも調べる
- 2026-11-21 に [推論の強さ](reasoning-effort.md) を足したときは「共通化しない」とした。[基本方針](core-beliefs.md) の「Client のパラメータ1つだけを変えればよい」に照らし、2026-11-26 に作者の判断で足すことにした

## 決定

- `Client.chat()` / `stream()`（`AsyncClient` も）に `reasoning_summary: bool = False` を足す。`reasoning_effort=` と同じく呼び出しごとに渡す
  - `True` / `False` 以外（`None`、`1`、`"auto"` など）は `TypeError`
- `True` のとき、各社の「要約を返す」指定を送る。返った要約（DeepSeek などは推論の本文）は、今までどおり `Response.thinking` と `thinking` イベントに入る

| アダプター | 送る形 |
|---|---|
| `openai` | `reasoning: {"summary": "auto"}`。`reasoning_effort=` の `effort` と同じオブジェクトに並ぶ |
| `anthropic` | `thinking: {"type": "adaptive", "display": "summarized"}`。`effort` は今までどおり `output_config` |
| `gemini` | `generationConfig.thinkingConfig.includeThoughts: true`。`thinkingLevel` と同じオブジェクトに並ぶ |
| `openai_chat` | 何も足さない。接続先が OpenAI 本家（`https://api.openai.com/v1`）なら、送る前に `UnsupportedFeatureError` |

- **段階は受け付けない**
  - OpenAI の `concise`・`detailed` は OpenAI にしかなく、受け付けるとほかの社では `UnsupportedFeatureError` にするしかなくなり、切り替えを妨げる
  - `auto` は、多くのモデルで `detailed` と同じ
  - 段階を決めたいときは `extra_by_provider=` で渡す（同じ場所なら `extra=` が勝つ。いつもの深いマージ）
- **既定は `False`（頼まない）。** `True` にすると次のことが起きる
  - 今のコードで、Anthropic 4.5 系が毎回 400 になる
  - Opus 4.6〜4.8・Sonnet 4.6 は考えるようになり、料金と待ち時間が増える
  - OpenAI は、最新の推論モデルで、要約に組織の認証が要ることがある（U32）
- **`reasoning_effort="none"` と一緒なら、送る前に `ConfigurationError`。** 推論を切りながら本文を頼むのは矛盾する。Anthropic では `display` が `type: "disabled"` で無効（「there is nothing to display」）
- **Anthropic は `type: "adaptive"` も送る。** `type` は必須で、`display` だけでは送れない
  - Claude 5 系は、送らなくても adaptive で考えるので変わらない
  - 変わるのは、Opus 4.6〜4.8・Sonnet 4.6（考えるようになる）と 4.5 系（400）だけ
  - 4.5 系で使うときは、`extra={"thinking": {"type": "enabled", "budget_tokens": N}}` と一緒に渡す。深いマージで `display` が並ぶ（`display` は `enabled` でも使える）
- **Chat Completions 互換のサーバーには何も足さない。** 要約を頼む標準の指定がない
  - DeepSeek は、頼まなくても返すので、これで満たせる
  - Ollama・LM Studio は、返せば `reasoning_content` か `reasoning` から読む（U35）
- **OpenAI 本家の Chat Completions だけは止める。** 推論の本文を返さないので、黙って `None` にせず、Responses API（`openai:`）を案内する
  - 止めるのはサーバーの制約なので、プリセットの名前ではなく接続先で決める（`max_tokens_field` と同じ）
  - `openai-chat:` に別の `base_url`（OpenRouter など）を渡せば使える
- **モデルが受け付けないときは、API のエラーをそのまま返す。** 推論しないモデル、Anthropic 4.5 系の `adaptive` など。推論の強さと同じく、モデルの一覧を持たない（[実 API 検証](../exec-plans/active/real-api-verification.md) の 2026-11-17 の判断）
- **頼んでも `None` のことがある。** 要約を返すかはモデルが決める（Gemini は「thoughts are returned only when available」、Anthropic の adaptive は考えないこともある）。返らなかったことを例外にはしない
- 履歴の再送（`ProviderState`）は変えない。Anthropic は会話の途中で `display` を変えてよく、`signature` は `display` によらず同じ（「Switching `display` values between turns in a conversation is supported」）

## 却下した案

- **名前を `include_thinking=` にする**: 返す先の `Response.thinking` と合い、DeepSeek は要約ではなく本文を返すので意味はより正確。だが `reasoning_effort=` と並ばない（作者の判断で `reasoning_summary`）
- **段階（`"auto"`・`"concise"`・`"detailed"`）を受け取る**: OpenAI にしかない。ほかの社では送れず、`UnsupportedFeatureError` か黙って無視のどちらかになる
- **既定を `True` にする**: 何もしなくても `Response.thinking` が埋まるが、上の理由で今のコードが壊れるか高くつく
- **Chat Completions 互換は DeepSeek 以外を `UnsupportedFeatureError` にする**: 安全だが、ローカルのモデル（Ollama・LM Studio）で試すときに使えない（作者の判断で却下）
- **OpenAI 本家の Chat Completions を、プリセットのオプション（`file_input: False` と同じ形）で止める**: `openai-chat:` に別の `base_url` を渡したときまで止めてしまう。止める理由は接続先のサーバーにある
- **Anthropic で `display` だけを送る**: Opus 4.6〜4.8 を勝手に考えさせずに済むが、`type` が必須なので送れない

## 影響

- `chat()` / `stream()` の引数が増える。`__all__` は変わらない
- `ChatRequest` に `reasoning_summary` が増える（`register_provider()` で足すアダプターも読める）
- 結果を読む側は変えていない。4アダプターとも、要約を `Response.thinking` と `thinking` イベントに入れる処理は前からある
- 推論の強さの「ほかの推論の指定は共通化しない」は、要約についてだけ取り消す（[推論の強さ](reasoning-effort.md)）
- 2026-11-28: Azure OpenAI の Chat Completions（`azure-chat`）も推論の本文を返さないので、送る前に止める。接続先の URL ではなく、Chat Completions 互換のオプション `reasoning_summary=False` で決める（[判断](cloud-api-keys.md)）
