# 載せ替えの負担を減らす

## 目的

switchllm を入れたあとにモデルを載せ替えるとき、モデルの文字列（または `SWITCHLLM_MODEL`）のほかに直すコードを減らす。直す箇所が残るなら、本番で呼ぶ前に分かるようにする。

下の「直すこと」の6つそれぞれが、直したか、直さないと作者が決めたら完了。

## 背景

- 2026-11-26 に、作者が描くペルソナで検査した。どんな人かは次のとおり
  - 作るもの: LLM を API で呼ぶ開発全般
  - 載せ替える先: 主要3社の間、安いモデル、ローカルとクラウドの間、同じ社の新しい世代
  - 切り替え方: 設定で切り替える、並べて比べる、会話の途中で切り替える。フォールバックは選ばなかった
  - 「最小限の変更」の意味: 既存の SDK から移る手間ではなく、switchllm を入れたあとにモデルを変えたときの影響（作者の言葉）
- 検査の方法: 同じアプリのコードを一度だけ書き、モデルの文字列だけを替えて7つの接続先（`openai`・`anthropic`・`gemini`・`deepseek`・`ollama`・`lmstudio`・`openai-chat`）で回した。ネットワークは使わず、`httpx.MockTransport` で見た。API の側で断られるものは `docs/references/` の記述から拾った
- 文字列だけで動いたもの:
  - テキスト、ストリーム、ツール、構造化出力、base64 の画像、`reasoning_summary=True`
  - 会話の途中の切り替え（ツールの往復を5社の総当たり 20 通りで回した）
- 文字列だけでは動かないものが、下の6つ。作者が6つとも直すと決めた（2026-11-26）

## 直すこと

### 1. `max_tokens` を省くと、Anthropic にだけ 4096 を送る

- 今: Anthropic は `max_tokens` が必須なので、省くと `DEFAULT_MAX_TOKENS = 4096` を送る（`providers/anthropic.py`）。ほかの社は送らず、モデルの既定に任せる。例外にならず結果だけが変わるのは、ここだけ
- 困ること:
  - 推論のトークンも `max_tokens` に含まれる（Messages API「Thinking tokens count toward your `max_tokens` limit」）
  - effort が `high` 以上なら、`max_tokens` を大きく取るよう勧められている（[references](../../references/anthropic-messages.md)）
  - そのため、考える問いや長い答えが途中で切れる
- 調べること: 各モデルの出力の上限。大きな `max_tokens` をストリーミングなしで送ったときの扱い。既定のタイムアウト 120 秒との関係
- 決めること: 既定の値と決め方。値を大きくしすぎると、400 になるか、ストリーミングなしの呼び出しがタイムアウトするおそれがある。不変条件の「モデルの一覧を持たない」との関係も決める
- 前の判断: 2026-11-15 に同じ問題を見つけ（[切り替えの精査](../completed/2026-11-15-switching-review.md) の S12）、「どの値を既定にしても、対応していないモデルがある」として README の「既知の制限」に書くだけにした。今回は、Claude 5 系が推論も上限に含むことを踏まえて見直す
- 結果（2026-11-28）: 省いたら 64000 を送る。提供中のモデルすべての上限（64K〜128K）に収まる。レート制限と料金は、実際に出した分だけなので変わらない（[判断](../../design-docs/anthropic-max-tokens.md)）。上限を超える値の扱い（U36）と、ストリーミングなしで送れるか（U37）は、実 API で確かめる

### 2. `temperature` がほとんどの接続先で効かない

- 今:
  - Anthropic は API の版 `2026-10-21` 以降、どのモデルでも `temperature` が 400 になる。そのため、送る前に `UnsupportedFeatureError` にしている（[判断](../../design-docs/anthropic-api-version.md)）
  - Gemini 3.6 以降と DeepSeek の思考モードは、API がエラーにせず無視する（各社の文書による）
  - ほかの接続先には、そのまま送る
- 困ること: 一番よく書く引数なのに、主要3社の間で載せ替えると Anthropic で止まる
- 案:
  - (a) 今のまま止め、手引き（6）に「頼らない」と書く
  - (b) Anthropic には送らずに捨てる
  - (c) 警告を出して捨てる
  - (d) 捨てるかどうかを利用者が選ぶ
- 決めること: どれにするか。(b)〜(d) は不変条件4の「黙って無視しない」に関わる。採るなら、作者の判断と理由を設計文書に残し、不変条件の書き方も直す
- 前の判断: 2026-11-21 に、(b) の「版を上げ、`temperature` は黙って送らない」を、基本方針 4 に反するとして却下した（[Anthropic の API の版](../../design-docs/anthropic-api-version.md)）

### 3. `Client(...)` に渡す接続先ごとのオプションが、ほかの接続先では `ConfigurationError` になる

- 今: `check_options`（`providers/base.py`）は、アダプターが知らないオプション名を `ConfigurationError` にする。アダプターごとのオプションは次のとおり
  - `anthropic`: `default_max_tokens`・`anthropic_version`・`beta`
  - `gemini`: `schema_field`・`inject_dummy_signatures`
  - `openai_chat`: `max_tokens_field`・`stream_usage`・`json_schema`・`file_input`
- 困ること: これらを付けたまま設定で載せ替えると、`Client` を作るところで落ちる。呼び出しの引数には `extra_by_provider=` があるが、`Client(...)` に渡すオプションには、接続先ごとに書き分ける仕組みがない
- 案: `options_by_provider={"anthropic": {...}}`。呼び出しの `extra_by_provider=` と同じ形にする
- 前の判断: 知らないオプション名を `ConfigurationError` にしたのは、打ち間違いや別のアダプター用のオプションに気づけるようにするため（2026-11-15。[各社に固有の指定](../../design-docs/provider-specific-params.md)）。書き分ける仕組みを足しても、この検査は残す
- 決めること:
  - 名前と形
  - キーにプロバイダーキーとプリセット名のどちらを使うか（`extra_by_provider=` に合わせる）
  - 選ばれなかった接続先の分も、オプション名を検査するか（打ち間違いに気づけるかどうか）

### 4. モデルごとの制限は、API を呼ぶまで分からない

- 例:
  - 強制する `tool_choice` を 400 で断るモデルがある: Claude Opus 5.5・Sonnet 5.5・Fable・Mythos 5.x と、DeepSeek の思考モード（`required`）
  - Ollama は `tool_choice` に対応しない
  - gpt-5.5 は `reasoning_effort` の `max` と `minimal` を受け付けない。Claude Opus 5.5 は `none` を使えない
  - Anthropic 4.5 系に `reasoning_summary=True` を送ると 400 になる
- 不変条件の「モデルの一覧を持たない」があるので、送る前に止めることはできない。同じ社の新しい世代へ移ったときに、本番で初めて気づく
- 案:
  - (a) 手引き（6）に、避ける書き方として載せる
  - (b) API のエラーを例外にするとき、よくある原因の案内をメッセージに足す（モデル名ではなく、エラーの中身で判断する）
  - (c) 載せ替えたときに一度だけ回す確認の手順を README に載せるか、小さな関数を用意する（実 API を呼ぶので料金がかかる）
- 決めること: どれにするか（組み合わせてもよい）

### 5. 各社にない機能は、送る前に止まる

- 止まるもの:
  - `stop`: OpenAI Responses
  - 画像の URL: Gemini
  - ファイル: DeepSeek・Ollama・LM Studio
  - CSV などのテキストのファイル: `openai-chat`（受け付けるのは PDF だけ）
  - JSON Schema（`response_schema=`）: DeepSeek
  - `reasoning_effort` の `minimal`: Anthropic
  - `reasoning_effort` の `none` と `max`: Gemini
  - `reasoning_summary=True`: OpenAI 本家の Chat Completions
  - 会話の途中の載せ替えでも、履歴に PDF や画像の URL があると、それを送れない接続先へは移れない
- 止めること自体は不変条件4のとおりで、メッセージも代わりの手段を案内している。困るのは、載せ替える前に分からないことと、代わりの書き方を利用者が考えること
- 案:
  - (a) どの接続先で何が使えるかの一覧を、public-api.md と README に載せる
  - (b) 送る前に確かめる手段を用意する（例: `client.supports(...)`）
  - (c) ライブラリが代わりに変換する（例: 画像の URL を取ってきて base64 にする）。2026-11-15 に、Gemini の画像の URL は「ライブラリが URL を取りに行くと、通信先が増え、SSRF の心配も出る」として直さなかった（[切り替えの精査](../completed/2026-11-15-switching-review.md) の「今回は直さないもの」）。OpenAI Responses の `stop` も「API に機能がない」として直さなかった
  - (d) 載せ替え先で送れない部分を、履歴から外すか置き換える補助を用意する
- 決めること: どれにするか。機能ごとに違ってもよい

### 6. 載せ替えの手引きがない

- 今: どう書けばどの接続先でも動くかが、README のあちこちに散らばっている
- 案: README（英語・日本語）に、モデルを載せ替える節を足す。中身は、持ち運べる書き方と、5 の一覧へのリンク。持ち運べる書き方の例:
  - `max_tokens` は明示する
  - `temperature` と `stop` に頼らない
  - 画像は base64 で渡す
  - `reasoning_effort` は `low`・`medium`・`high` を使う
  - 強制する `tool_choice` は避ける
  - 接続先ごとの指定は `extra_by_provider=`（と 3 で足すもの）に書く
- 中身は 1〜5 の結果で変わるので、最後に書く

## 手順

1〜5 はそれぞれ、いつもの順で進める。一次情報を確かめて references に書く → 選択肢を作者に選んでもらう → 設計文書 → テストを先に書いて失敗を確かめる → 実装 → わざと壊した実装で新しいテストが失敗することを確かめる → 文書。

- [x] 作者のペルソナで検査し、6つを見つける（2026-11-26）
- [x] 作者が6つとも直すと決める（2026-11-26）
- [x] 1. Anthropic の `max_tokens` の既定（2026-11-28。64000）
- [ ] 2. `temperature` の扱い
- [ ] 3. `Client(...)` のオプションを接続先ごとに書き分ける
- [ ] 4. モデルごとの制限を、本番より前に分かるようにする
- [ ] 5. 各社にない機能を、載せ替える前に分かるようにする
- [ ] 6. README（英語・日本語）に載せ替えの手引きを足す（1〜5 の後）
- [ ] 送るリクエストが変わったものは、E2E のケースと [実 API 検証](real-api-verification.md) の前提の一覧を直す（`--record` の前に）

順番: 送るリクエストが変わる 1〜3 を先にする。実 API の記録（`--record`）は、この計画が終わってから取る。記録を取ったあとに送る中身を変えると、取り直すことになり、料金がもう一度かかるため。

## 進捗ログ

- 2026-11-26: 検査した（上の背景）。検査に使ったスクリプトはその場限りのもので、リポジトリには入れていない。ほかに小さな気づきが1つあり、負債の一覧に書いた（画像やファイルと同じリストに、文字列をそのまま入れると `TypeError` になる）。README の「Known limitations」に推論の要約の古い案内（`extra_by_provider=` で頼む）が残っていたので、`reasoning_summary=True` の案内に直した
- 2026-11-28: 1 を終えた。一次情報（Messages API・Models overview・Get a Model・Context windows・Rate limits・Errors・Streaming）を references に書いた。テストを先に書き、変更前のコードで6件が失敗することを確かめた。わざと壊した実装（4096 に戻す、ストリーミングだけ・ストリーミングなしだけ 4096、ストリーミングの指定を落とす）を4通りとも見つけた。E2E に `test_max_tokens_omitted`（U37）と `test_max_tokens_above_model_limit`（U36）を足し、実 API 検証の前提の一覧に入れた
- 2026-11-28: `docs/RELIABILITY.md` の「全体 120 秒」は誤りだった。httpx の既定は受信の待ち（次のデータが届くまで）が 120 秒で、全体の時間ではない。直した。ストリーミングなしの長い生成がタイムアウトするとリトライすることを、負債の一覧に書いた

## 判断ログ

- 2026-11-26: 6つとも直す（作者の判断）
- 2026-11-28: 1 は、省いたら 64000 を送る（作者の判断。ほかの案は、Models API に聞く、32000、4096 のまま）。モデルの一覧を持たず、リクエストを1回のまま、提供中のモデルすべてで上限に収まるため
- 2026-11-26: この計画は、実 API の記録の前に行う。作者が E2E の前に検査を頼んだことと、記録を取り直す料金を避けるため
