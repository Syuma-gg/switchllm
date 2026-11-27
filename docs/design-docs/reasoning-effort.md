# 推論の強さは reasoning_effort= で段階の名前を受け取る

- 状態: 採用（2026-11-21、作者の判断）
- 検証: 一次情報で確認、実 API 未検証（U23・U24・U26・U27。E2E の `test_e2e_reasoning.py`）
- コード: `client.py`（`reasoning_effort=`、段階の検査）、`providers/base.py`（`REASONING_EFFORTS`）、4つのアダプターの `build_request`
- テスト: `tests/test_reasoning_effort.py`
- 計画: [モデルの層の機能を足す](../exec-plans/completed/2026-11-23-model-layer-features.md) の 3

## 背景

推論するモデルは、答える前にどれだけ考えるかを指定できる。4社とも指定があるが、場所も段階の名前も違う（[各社の形式](../references/index.md) の「推論の強さ」）。

| 接続先 | 指定 | 段階 |
|---|---|---|
| OpenAI（Responses / Chat Completions） | `reasoning.effort` / `reasoning_effort` | `none`・`minimal`・`low`・`medium`・`high`・`xhigh`・`max` |
| DeepSeek | `reasoning_effort` | `none`・`low`・`high`・`max`（`minimal`・`medium`・`xhigh` は API の側で近い段階として受け付ける） |
| Ollama | `reasoning_effort` | モデルが決める（知らない段階はモデルの既定になる） |
| Anthropic | `output_config.effort` | `low`・`medium`・`high`・`xhigh`・`max`。推論を切るのは `thinking: {"type": "disabled"}` |
| Gemini | `generationConfig.thinkingConfig.thinkingLevel` | `minimal`・`low`・`medium`・`high`・`xhigh`。推論を切る段階はない（Gemini 2.5 は数の `thinkingBudget`） |

同じ社の中でも、モデルによって使える段階が違う（gpt-5.5 に `max` はない、Gemini 4 に `minimal` はない、Claude Opus 5.5 は推論を切れない）。

以前は「やらないこと」にしていたので（[判断](provider-specific-params.md)）、利用者は `extra_by_provider=` で各社の形式を書き分けるしかなかった。モデルを変えて試すたびに、呼び出し側の書き方も変わる。2026-11-18 に、機能を選ぶ基準（[基本方針](core-beliefs.md)）に照らして足すことにした。

## 決定

- `Client.chat()` / `stream()` に `reasoning_effort=` を足す。受け取るのは `"none"`・`"minimal"`・`"low"`・`"medium"`・`"high"`・`"xhigh"`・`"max"` の文字列。`None`（既定）なら何も送らず、各モデルの既定の段階になる
  - 7つ以外の文字列は `ConfigurationError`、文字列でなければ `TypeError`（送る前に分かる誤り。打ち間違いを API の 400 で知るより早い）
- **段階の名前は OpenAI の7つにする。** Anthropic・Gemini・DeepSeek の段階の名前は、どれもこの中にある。DeepSeek と Ollama も、この名前を別名として受け付ける
- **各社へは、名前を変えずに送る。** `extra=` で同じ場所を指定したら、`extra=` が勝つ（いつもの深いマージ）

| アダプター | 送る形 | 名前のない段階 |
|---|---|---|
| `openai` | `reasoning: {"effort": 段階}` | ない |
| `openai_chat` | `reasoning_effort: 段階` | ない（DeepSeek・Ollama は API の側で近い段階に置き換える） |
| `anthropic` | `output_config: {"effort": 段階}` | `none` は `thinking: {"type": "disabled"}`。`minimal` は `UnsupportedFeatureError` |
| `gemini` | `generationConfig: {"thinkingConfig": {"thinkingLevel": 段階}}` | `none` と `max` は `UnsupportedFeatureError` |

- **段階の意味まではそろえない。** 同じ `high` でも、考える量はモデルによって違う。各社とも段階は目安で、Anthropic も「a behavioral signal, not a strict token budget」とする。switchllm はトークン数に換算しない
- **各社の API に名前のない段階は、送る前に `UnsupportedFeatureError` にする**（作者の判断。不変条件 4）。例外のメッセージで、近い段階と `extra=` での指定を案内する
  - Anthropic の `none` だけは、`thinking: {"type": "disabled"}` で同じ意味になるので送る。effort は送らない
  - Gemini の `none`: `thinkingLevel` には、推論を切る段階がない。`thinkingBudget: 0` で切れるのは Gemini 2.5 だけ。Gemini 4 は、`thinkingBudget` を近い段階に置き換えて警告を返すだけ（黙って考える）
- **モデルが受け付けない段階は、API のエラーをそのまま返す。** gpt-5.5 の `max`、Opus 5.5 の `none` などを、先回りして弾かず、別の段階に替えもしない。モデルの一覧を持たない（[実 API 検証](../exec-plans/active/real-api-verification.md) の 2026-11-17 の判断）
- **ほかの推論の指定は共通化しない。** Anthropic の `thinking: {"type": "adaptive"}`、Gemini 2.5 の `thinkingBudget` は、`extra=` で渡す。深いマージなので、`reasoning_effort=` と一緒に使える
  - 推論の要約を返させる指定（OpenAI の `reasoning.summary`、Anthropic の `thinking.display`、Gemini の `includeThoughts`）も、ここでは共通化しないとしたが、2026-11-26 に作者の判断で `reasoning_summary=True` として足した（[判断](reasoning-summary.md)）
- **`temperature` との組み合わせは、先回りして弾かない。** Gemini 3.6 Flash 以降と DeepSeek の思考モードは黙って無視し、OpenAI は書いていない（U23）。Anthropic は API の版で決まる（[判断](anthropic-api-version.md)）
- `response_schema=`・ツール・ストリーミングとも一緒に使える。Anthropic では `output_config` の `format` と `effort` が同じオブジェクトに並ぶ

## 却下した案

- **段階を4つ（なし・弱・中・強）にそろえ、各社の段階へ対応づける**: `xhigh` と `max` が使えなくなる。対応づけ（中を Anthropic のどれにするか）は、各社が段階や既定を変えるたびに直すことになる
- **名前のない段階を近い段階に替えて送る**（Anthropic の `minimal` を `low`、Gemini の `max` を `xhigh`）: 切り替えは楽になるが、指定と違う段階を黙って送る（作者の判断で却下）。DeepSeek と Ollama は API の側でこの置き換えをしているが、それは各社の約束で、switchllm が足すものではない
- **数（推論のトークンの予算）で受け取る**: OpenAI には予算の指定がない。Anthropic の `budget_tokens` は新しいモデルで 400、Gemini の `thinkingBudget` は 2.5 のためのもの
- **Anthropic で `thinking: {"type": "adaptive"}` も一緒に送る**: Opus 4.6〜4.8 も考えるようになるが、4.5 系は `adaptive` を 400 にする。Claude 5 系は送らなくても adaptive で考え、Anthropic は effort で強さを決めることを勧めている
- **Gemini 2.5 では `thinkingBudget` に換算する**: モデルの世代を見分けるには、モデルの一覧が要る。`gemini-2.5-flash` と `gemini-2.5-pro` は 2026-12-01 から 3.x のモデルへ回される
- **Gemini の `none` を `thinkingBudget: 0` で送る**: Gemini 3・4 では切れず、黙って考える
- **引数の名前を `reasoning=` にする**: 短いが、OpenAI Responses の `reasoning` は要約なども持つオブジェクトで、あとで要約の指定を足すと意味が広がる
- **引数の名前を `effort=` にする**: Anthropic の名前だが、Anthropic の effort は本文とツール呼び出しにも効き、「推論の強さ」より広い

## 影響

- `chat()` / `stream()` の引数が増える。`__all__` は変わらない
- `ChatRequest` に `reasoning_effort` が増える（`register_provider()` で足すアダプターも読める）
- Claude 5 系・Gemini・OpenAI の推論モデルは、推論の要約を既定では返さない。`reasoning_effort=` を指定しても `Response.thinking` は `None` のことがある。返させるには `reasoning_summary=True` も渡す（2026-11-26 に足した。[判断](reasoning-summary.md)）
- LM Studio が `reasoning_effort` を受け付けるかは分からない（U27）。受け付けないサーバーのためのオプションは、必要になってから足す
