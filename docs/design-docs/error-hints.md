# API が断った指定には、原因とみられる引数と、どのモデルでも動く書き方をエラーに足す

- 状態: 採用（2026-11-28、作者の判断）
- 検証: 一次情報で確認（Anthropic の文言。ほかの社は形だけ）、実 API 未検証（U38）
- コード: `providers/base.py`（`ERROR_HINTS`、`sent_arguments()`、`Provider.error_fields`、`Provider.error_hints()`）、各アダプターの `error_fields`、`client.py`（`_hint_rejected()`）
- テスト: `tests/test_error_hints.py`、`e2e/test_e2e_tools.py`（`test_forced_tool_choice`）
- 計画: [載せ替えの負担を減らす](../exec-plans/completed/2026-11-28-model-switch-friction.md) の 4

## 背景

- モデルによって受け付けない指定がある（[載せ替えの負担を減らす](../exec-plans/completed/2026-11-28-model-switch-friction.md) の 4）
  - 強制する `tool_choice` を、Claude Opus 5.5・Sonnet 5.5 などと、DeepSeek の思考モードが 400 にする
  - gpt-5.5 は `reasoning_effort` の `max` と `minimal` を受け付けない。Claude Opus 5.5 は `none` を使えない
  - Anthropic 4.5 系は、`reasoning_summary=True` で送る adaptive thinking を 400 にする
- モデルの一覧を持たない決まり（[実 API 検証](../exec-plans/active/real-api-verification.md) の 2026-11-17 の判断）があるので、送る前には止められない。同じ社の新しい世代へ移ったときに、本番で初めて気づく
- API のエラーは、各社の書き方とフィールド名で書かれている（`output_config.effort`、`thinking.type.disabled` など）。どの共通の引数が原因で、どう書けばどのモデルでも動くかは、エラーからは分からない
- 各社のエラーの形（[references](../references/index.md) の各文書の「エラー」）
  - Anthropic: 引数を指す項目はない。文言にフィールド名が入る（`tool_choice: type "tool" and "any" are not supported for this model.` など）
  - OpenAI: `error.param` が、原因の引数を指す（`service_tier` の例）
  - Gemini: generateContent のエラーの形は、文書にない。文書は snake_case でフィールド名を書く
  - DeepSeek: 形は書かれていない。400 と 422 は「エラーの文言の案内に従って直す」

## 決定

- **API が 400 系（`BadRequestError`）で断ったら、原因とみられる共通の引数と、どのモデルでも動く書き方を `str(e)` に足す**（作者の判断）
  - 形は、`str(e)` の後ろに `\nHint (<引数>): <案内>` の行を足す
  - `e.message`（API の文言）と `e.body` は変えない
- **案内するかは、モデル名ではなく、送ったものとエラーの中身で決める**
  - 対象の引数は、強制する `tool_choice`（`required` かツール名）・`reasoning_effort`・`reasoning_summary=True`・`response_schema`・`temperature` の5つ（`ERROR_HINTS`）。`tool_choice` の `auto` と `none` は、どのモデルでも受け付けるので数えない
  - 各アダプターは、それぞれの引数を送るときのフィールド名を `error_fields` に持つ（例: Anthropic の `reasoning_effort` は `output_config.effort`・`thinking.type.disabled`・`between_tools`）
  - 送った引数のフィールド名が、エラーの文言か本文（OpenAI の `param` など）に出てきたら、その引数を案内する。大文字と小文字は区別しない（Gemini の camelCase も見つける）
  - 複数の引数が当たれば、`ERROR_HINTS` の順にすべて案内する
- 5xx などの、指定の誤りでないエラーには案内しない
- 案内の中身は引数ごとに1つで、モデルを名指ししない。例: `tool_choice` は「tool_choice を指定せず、プロンプトでツールを使うよう頼む」

## 却下した案

- **原因の引数を、例外の属性（例: `e.param`）にも持たせる**: コードで「その引数を外して送り直す」と書けるが、公開 API が増え、当たらなかったときの約束も生まれる（作者の判断で選ばなかった）
- **載せ替えの確認の関数を用意する**（新しいモデルに、使う指定を付けた小さなリクエストを送り、断られる指定を一覧にする）: 実 API を呼ぶので料金がかかり、薄いモデルの層という位置づけから外れかける（作者の判断で選ばなかった）
- **エラーの文言を、各社の決まった言い回しで照らし合わせる**: 文言は文書にほとんどなく、各社が変えれば当たらなくなる。フィールド名は、ライブラリが自分で送ったものなので確か
- **モデル名を見て、断られる指定を先に知らせる**: モデルの一覧が要る

## 影響

- 載せ替えた先で断られたとき、どの引数をどう書けばよいかが、例外のメッセージで分かる
- 案内は、エラーにフィールド名が出てくるときだけ付く。各社が文言を変えたり、名前を出さなかったりすると、案内は付かない（API のエラーはそのまま見える）。どの社で付くかは、実 API で確かめる（U38）
- 共通の引数を足し、それがモデルによって断られうるなら、`ERROR_HINTS` と各アダプターの `error_fields` にも足す（[ARCHITECTURE.md](../../ARCHITECTURE.md) の「共通パラメータを足す」）
- 避ける書き方の一覧は、README の載せ替えの手引きに載せる（計画の 6）
