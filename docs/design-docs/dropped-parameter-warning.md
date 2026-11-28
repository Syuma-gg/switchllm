# Anthropic の temperature は送らずに捨て、DroppedParameterWarning で知らせる

- 状態: 採用（2026-11-28、作者の判断）。[Anthropic の API の版](anthropic-api-version.md) の「temperature は送る前に `UnsupportedFeatureError`」を置き換える
- 検証: 一次情報で確認、実 API 未検証（U25）
- コード: `errors.py`（`DroppedParameterWarning`）、`providers/base.py`（`warn_dropped()`）、`providers/anthropic.py`（`build_request`）
- テスト: `tests/test_anthropic.py`（`temperature` を含むテスト）、`e2e/test_e2e_basic.py`（`test_temperature_zero`）、`e2e/test_e2e_errors.py`（`test_temperature_above_range`）
- 計画: [載せ替えの負担を減らす](../exec-plans/active/model-switch-friction.md) の 2

## 背景

- Anthropic の API の版 `2026-10-21`（switchllm の既定）では、`temperature` を送ると、どのモデルでも、既定の値でも 400 になる。古い版 `2023-06-01` でも、今の Claude（Claude 5 系と Opus 4.8・4.7）は既定でない値を 400 にする（[判断](anthropic-api-version.md)）
- 2026-11-21 からは、送る前に `UnsupportedFeatureError` にしていた。そのため、`temperature=` を付けたコードは、Anthropic へ載せ替えたときだけ止まった
- ほかの接続先は止まらない（[載せ替えの負担を減らす](../exec-plans/active/model-switch-friction.md) の 2）
  - Gemini 3.6 以降と DeepSeek の思考モードは、API の側がエラーにせず黙って無視する
  - OpenAI などは、そのまま受け付ける
- `temperature` は出力のばらつきを変えるだけで、指定が効かなくても、応答の形と正しさは変わらない。一方、`stop` や画像の URL を捨てると、応答の中身や、モデルに届く情報が変わる

## 決定

- **Anthropic で、API の版が `2026-10-21` 以降なら、`temperature=` を送らずに捨て、`DroppedParameterWarning` を出す**（作者の判断）
  - リクエストの残りは、そのまま送る
  - 0 も、既定と同じ値も「指定あり」として捨てる（その版では既定の値でも 400 になるため）
  - メッセージには、捨てた値と、古いモデルなら `anthropic_version="2023-06-01"` で送れることを書く
  - `anthropic_version="2023-06-01"` なら、今までどおり送り、警告しない。モデルが拒否すれば、API のエラーをそのまま返す
- **`DroppedParameterWarning` は `UserWarning` を継承し、`switchllm` から import できる**（名前は作者の判断）
  - 送る前に止めたい利用者は、`warnings.filterwarnings("error", category=DroppedParameterWarning)` で例外にできる。そのときはリクエストを送らない
  - 警告は、ライブラリの中ではなく、`chat()` / `stream()` を呼んだ行を指す（`warn_dropped()` が `stacklevel` を数える）。Python の既定では同じ場所の警告は1回しか表示されないので、指す場所がライブラリの中だと、どこで捨てたかが分からなくなる
- **捨ててよいのは、次の2つを満たす指定だけ。** 今は Anthropic の `temperature` だけで、ほかは今までどおり `UnsupportedFeatureError` にする
  - 送ると、どのモデルでも API が拒否すると文書で分かっている
  - 捨てても、応答の形と正しさが変わらない
- 基本方針 4（黙って無視しない）と不変条件 4 は、「捨てるなら警告する」と書き直した。警告は黙っていないので、方針そのものは保っている

## 却下した案

- **今のまま止め、手引きに「`temperature` に頼らない」と書く**: 不変条件はそのままでよいが、`temperature=` を付けたコードは、載せ替えるたびに Anthropic で止まる。どのモデルでも効かない指定のために、利用者がコードを直すことになる
- **黙って捨てる**: Gemini 3.6 以降や DeepSeek と同じ扱いになるが、ライブラリが自分で捨てるのに知らせない。2026-11-21 にも、基本方針 4 に反するとして却下した
- **`Client(...)` のオプションで、止める・警告して捨てる・黙って捨てるを選ばせる**: 公開 API にオプションが増え、既定を「止める」にすると、載せ替えで止まるのは変わらない。警告なら、Python の `warnings` の仕組みで、利用者が例外にも、表示しないようにもできる
- **名前を `IgnoredParameterWarning` や `SwitchLLMWarning` にする**: 前者は API の側が無視したようにも読める。後者は、何を知らせる警告かが名前から分からない

## 影響

- `temperature=` を付けたコードを Anthropic へ載せ替えても止まらず、警告が1回表示される
- Gemini 3.6 以降と DeepSeek の思考モードでは、API が黙って無視するので、警告は出ない。どのモデルで無視されるかはモデルの世代で決まり、見分けるにはモデルの一覧が要るため（[実 API 検証](../exec-plans/active/real-api-verification.md) の 2026-11-17 の判断）。載せ替えの手引きで「`temperature` に頼らない」と案内する
- 公開 API に `DroppedParameterWarning` が増える。0.1.0 は未公開なので、壊れる利用者のコードはない
