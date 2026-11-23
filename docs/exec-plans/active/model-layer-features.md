# モデルの層の機能を足す

## 目的

LLM をコードで呼ぶときによく使うのに、今は各社の書き方を書き分けるか、別の手段が要る6つの機能を足す（[基本方針](../../design-docs/core-beliefs.md) の「機能を足すか絞るかの基準」）。6つすべてが、テスト駆動で実装され、文書に反映されたら完了。

足す機能（2026-11-18 に作者がすべて必要と判断した）:

1. 履歴の JSON 保存・復元
2. 構造化出力（JSON Schema で答えの形を決める）
3. 推論の強さ
4. 非同期のクライアント
5. すべてのアダプターに `headers=`
6. PDF・ファイルの入力

## 進め方

- 一つずつ、テスト駆動で進める（[テストの方針](../../testing.md)）。先にモックのテストを書き、変更前のコードで失敗することを確かめてから実装する
- API の仕様に関わるものは、実装の前に各社の公式文書で確かめ、`docs/references/` に出典を書く（[CLAUDE.md](../../../CLAUDE.md) の不変条件 7）
- 公開 API を増やすものは、先に設計の判断を `docs/design-docs/` に残し、`__all__` と [公開 API](../../product-specs/public-api.md) を同じ変更で直す
- 共通パラメータは4アダプターすべてで対応するか、`UnsupportedFeatureError` を明示的に投げる（不変条件 4）
- E2E テスト（`e2e/`）にも、機能ごとのケースを足す。実 API での実行は、6つがそろってから `--record` 付きで1回にまとめる（料金を一度で済ませるため。[実 API 検証](real-api-verification.md)）
- 終わった機能から、README（英語と日本語）の「制限」、[スコープ](../../product-specs/scope.md)、CHANGELOG を直す

## 手順

順番は、公開後に変えにくいもの（保存の形式、公開 API の形）を先にする。

- [x] **1. 履歴の JSON 保存・復元**（2026-11-18。[判断](../../design-docs/history-serialization.md)）
  - 今の問題: `Message.from_dict()` は推論情報（`provider_state`）を捨てる。保存した会話を復元すると、DeepSeek ではツールを使う会話が 400 になり、Gemini はダミー署名に頼り、OpenAI と Anthropic は推論を引き継げない
  - 決めること: API の形（`Message.to_dict()` / `from_dict()` を推論情報ごと往復させるか、会話の一覧を丸ごと保存する関数を足すか）、保存の形式に版を持たせるか、`ProviderState` を公開するか（[負債](../tech-debt-tracker.md) の「`ProviderState` が公開 API にない」も解消する）、画像（base64）を履歴に含めたときの大きさ
  - テスト: 4アダプターそれぞれで、応答 → JSON → 復元 → 次のリクエストが、保存しなかったときと同じになること
- [x] **2. 構造化出力**（2026-11-20。[判断](../../design-docs/structured-output.md)）
  - 今の問題: 「やらない」としていたので、モデルを変えるたびに書き方が変わる（OpenAI `text.format`、Gemini `generationConfig.responseSchema` など）
  - 決めること: 共通パラメータの名前と受け取る形（JSON Schema の dict か、型ヒントからも作るか）、結果を文字列のまま返すか、読んだ値も返すか、ストリーミングとツールとの組み合わせ、JSON Schema に対応しない接続先（JSON モードだけのもの、対応しないもの）の扱い
  - 一次情報で確かめる: OpenAI Responses、Chat Completions の `response_format`、Anthropic、Gemini、DeepSeek、Ollama、LM Studio
- [x] **3. 推論の強さ**（2026-11-21。[判断](../../design-docs/reasoning-effort.md)。あわせて Anthropic の API の版を上げた。[判断](../../design-docs/anthropic-api-version.md)）
  - 今の問題: 同上（OpenAI `reasoning`、Anthropic `thinking`、Gemini `generationConfig.thinkingConfig`、DeepSeek `thinking` / `reasoning_effort`）
  - 決めること: 共通にする段階（例: なし・弱・中・強）と、各社への対応づけ、対応づけられない指定（推論をなしにできないモデルなど）の扱い、`temperature` など推論と一緒に使えない指定との関係
  - 注意: 指定の方法がモデルの世代で変わる。対応づけは、モデルの一覧を持たずに済む形にする（モデルが受け付けない指定は API のエラーをそのまま返す、という [判断](real-api-verification.md) に合わせる）
  - 一次情報で確かめる: 各社の今の形式
- [x] **4. 非同期のクライアント**（2026-11-23。[判断](../../design-docs/async-client.md)）
  - 今の問題: 同期のクライアントしかない。ADK をはじめ、エージェントフレームワークの多くは非同期で動く
  - 決めること: 名前（`AsyncClient`）、`with_model()` と `close()` / `aclose()`、`async with` と `async for` での使い方、同期版とアダプターを共有する形（`_http.py` に非同期の送信・リトライ・SSE の読み取りを足す）
  - テスト: 同期版と同じテストを非同期でも回す。テストのための依存は増やさない（`asyncio.run` で回せるなら pytest のプラグインは入れない）
- [ ] **5. すべてのアダプターに `headers=`**
  - 今の問題: Chat Completions 互換だけにある。ゲートウェイを経由すると、ほかのアダプターへ切り替えられない
  - 決めること: 認証のヘッダー（`Authorization`、`x-api-key`、`x-goog-api-key`）と重なったときにどちらを優先するか
  - テスト: 4アダプターで、足したヘッダーが送られること（`tests/test_consistency.py`）
- [ ] **6. PDF・ファイルの入力**
  - 今の問題: 画像しか送れない
  - 決めること: パーツの型（例: `FilePart` / `DocumentPart`）と作り方（`from_file()`）、最初に対応する種類（PDF から始めるか）、対応しない接続先の扱い（送る前に `UnsupportedFeatureError`）
  - 一次情報で確かめる: OpenAI Responses の `input_file`、Anthropic の `document`、Gemini の `inlineData`、Chat Completions 互換
  - 音声の入力は、この計画に入れない（[スコープ](../../product-specs/scope.md) の「未定」のまま）
- [ ] 6つの E2E のケースを足し、[E2E テスト](../../e2e-tests.md) の一覧を更新する
- [ ] [実 API 検証](real-api-verification.md) の実行（`--record`）に、6つのケースも含める

## 進捗ログ

- 2026-11-23: 4（非同期のクライアント）を終えた。httpx の公式文書（Async Support・Transports・Developer Interface・Exceptions）を読み、`AsyncClient.request()`、`async with client.stream()`、`aiter_lines()`・`aread()`・`aclose()` を使うこと、`MockTransport` が非同期でも同期の handler のまま動くこと（文書には書いておらず、入っている httpx 0.28.1 のソースで確かめた）を確かめた。分かったこと: 各アダプターの `parse_stream()` は同期のイテレーターからイベントを引き出す作りで、非同期の読み取りからは呼べない（[スコープ](../../product-specs/scope.md) の「`_http.py` に async 版の送信関数を足すだけで済む」は誤りだった）。そこで、ストリームの解析を、イベントを1つずつ渡す `StreamParser`（`feed()` / `finish()`）に書き直した。この書き直しは、変更前のテスト 389 件がすべて通ることを確かめてから先へ進めた。テストは、`tests/conftest.py` の `api` を同期と非同期の2通りで回すように変え（`AsyncDriver`）、`tests/test_async_client.py` と `tests/test_tools.py` に非同期にしかないことを足した。変更前のコードでは、`AsyncClient` を import できずに失敗し、同期の `run_tool_calls` は `async def` のツールを実行せず `"<coroutine object ...>"` を成功の結果として返していた（「never awaited」の警告も出た）。実装のあと、リトライを `time.sleep` で待つ実装と、`stream()` の中で `aclosing()` を使わない実装を一時的に作り、新しいテストがどちらも失敗として見つけることを確かめた。E2E に `test_e2e_async.py`（1つ、1モデル 2 回）と、非同期の記録（`AsyncRecorder`）を足した。[負債](../tech-debt-tracker.md) の「非同期クライアントがない」を解消した
- 2026-11-23: 4 のテストを見直した。非同期の経路にわざと誤りを入れて（再送しない、途中まで受け取ってから再送する、解析器を使い回す、`with_model()` で設定を落とす、`parsed` を入れない、ツールを順に実行する、結果を終わった順に返す）、テストが見つけるかを確かめた。解析器を使い回す誤り（ストリームどうしで本文や終わりの合図が混ざる。エージェントのループで同じクライアントの `stream()` を繰り返すと起きる）は、同期・非同期のどちらでも見つけられなかったので、`tests/test_consistency.py` にテストを足した。あわせて、利用者が困る場面なのにテストがなかった、取り消し（再送しない、応答を閉じる）と、`aclosing()` を使わずに抜けたときの後片づけ（文書に書いた約束）のテストを足した。足したテストは、どれも対応する誤りで失敗することを確かめた
- 2026-11-21: 3（推論の強さ）を終えた。各社の公式文書（OpenAI の Reasoning models・Responses と Chat Completions のリファレンス・gpt-5.5、Anthropic の Effort・Thinking・Messages・Migrating to 2026-10-21・Versioning・Models overview、Gemini の Thinking・generateContent のリファレンス・Models、DeepSeek の Thinking Mode と Chat Completions、Ollama の OpenAI compatibility と Thinking、LM Studio の Chat Completions）を読み、`docs/references/` の4つの文書に「推論の強さ」を、Anthropic には「API の版」も書いた。分かったこと: 段階の名前は OpenAI の7つ（`none`〜`max`）がほかの社のものをすべて含む。Anthropic は `output_config.effort`（`low`〜`max`）で、推論を切るのは `thinking: {"type": "disabled"}`。Gemini は `thinkingLevel`（`minimal`〜`xhigh`）で、Gemini 3・4 は推論を切れない。DeepSeek と Ollama は `reasoning_effort` で、文書にない段階を API の側で近い段階に置き換える。LM Studio の文書には `reasoning_effort` がない。使える段階はモデルによって違う（gpt-5.5 に `max` はない、Opus 5.5 は推論を切れない）。文書で決めきれないものを U23〜U27 として [実 API 検証](real-api-verification.md) に足した。テスト（`tests/test_reasoning_effort.py` と、`tests/test_anthropic.py` の版と `temperature` のテスト）を先に書き、変更前のコードで 74 件が、`reasoning_effort` の引数がないこと・既定の版が `2023-06-01` のままなこと・`temperature` を送ってしまうことで失敗するのを確かめてから実装した。Anthropic の版を上げたので、`tests/test_consistency.py` の `temperature` を送るテストは、Anthropic だけ `anthropic_version="2023-06-01"` で回すようにした。E2E に `test_e2e_reasoning.py`（5つ、1モデル最大 7 回）を足し、`temperature` の2つの E2E は Anthropic では送る前の例外を確かめるようにした。README の例から `temperature=0` を外した（Anthropic で例外になるため）。推論の要約を返させる指定が共通でないことを [負債](../tech-debt-tracker.md) に書いた
- 2026-11-20: 2（構造化出力）を終えた。テスト（`tests/test_structured_output.py`）を先に書き、変更前のコードで、`response_schema` の引数・`Response.parsed`・`json_schema` のオプションがないことで失敗するのを確かめてから実装した（引数の型のテストは、Python の「unexpected keyword argument」の文言にも当たって通ってしまったので、ライブラリの文言で確かめるように直した）。4アダプター（Ollama・LM Studio を含む）で、各社の形式で送ること、`chat()` と `stream()` の `done` が同じ `parsed` を返すこと、拒否・上限・ツールを呼んだときは `parsed` が `None` であることを確かめた。E2E に `test_e2e_structured.py`（3つ、1モデル最大 4 回）を足した。README の「制限」にあった `store: false` の記述（`include` が要る）が、2026-11-17 に文書で確かめた内容（要らない）と食い違っていたので直した
- 2026-11-20: 2（構造化出力）の一次情報を確かめ、各社の形式・スキーマの制約・拒否と途中で切れたときの返り方・ストリーミングとツールとの併用を `docs/references/` の4つの文書の「構造化出力」に書いた。分かったこと: 指定の場所は4社で違う（OpenAI `text.format`、Chat Completions `response_format`、Anthropic `output_config.format`、Gemini `responseJsonSchema`）。スキーマの条件は OpenAI（strict）が最も厳しく、全オブジェクトに `additionalProperties: false`、全プロパティを `required`。Anthropic も `additionalProperties: false` は必須。Gemini は使えないキーワードを黙って無視する。DeepSeek は JSON Schema を受け付けず、JSON モードだけ。どの社も、HTTP の応答では JSON を文字列で返す。文書で決めきれないものを U19〜U22 として [実 API 検証](real-api-verification.md) に足した。Anthropic の API の新しい版（`2026-10-21`）で `temperature` が 400 になることを見つけ、[負債](../tech-debt-tracker.md) に書いた
- 2026-11-18: 1（履歴の JSON 保存・復元）を終えた。テスト（`tests/test_history.py`）を先に書き、`to_dict` がないことと、`content: null` を空にしないことで失敗するのを確かめてから実装した。4アダプター（Anthropic・OpenAI・Gemini・DeepSeek）で、JSON を経て復元した履歴から、元の履歴と同じリクエストを送り、推論情報（署名・暗号化された推論・reasoning_content）を送り返すことを確かめた。E2E にも `test_restored_history_continues` を足した。`from_dict()` が `{"type": "text"}` / `{"type": "image"}` のパーツの dict を読めるようになったので、`tests/test_client.py` で「読めないパーツの dict」の例に使っていた `{"type": "text"}` を、今も読めない OpenAI の `image_url` の形に替え、読めるようになった形が送られることを確かめるテストを足した
- 2026-11-18: できることとできないことを整理し、基本方針に「機能を足すか絞るかの基準」を足した。この基準に照らして6つを選び、作者がすべて必要と判断したので、この計画を作った

## 判断ログ

- 2026-11-23: 非同期のクライアントは、別のクラス `AsyncClient` にする。引数とメソッドの名前は `Client` と同じで、`stream()` は `async for` で直接回す。`async def` のツールのために `arun_tool_calls()` を足し、呼び出しを並行に実行する（作者の判断。3つとも推奨の案。[判断](../../design-docs/async-client.md)）
- 2026-11-21: 推論の強さは、`reasoning_effort=` に OpenAI の7段階の名前を受け取り、各社の指定に名前を変えずに入れる。各社の API に名前のない段階（Anthropic の `minimal`、Gemini の `none`・`max`）は、近い段階に替えずに、送る前に `UnsupportedFeatureError` にする。Anthropic の `none` は thinking を切る（作者の判断。どれも推奨の案。[判断](../../design-docs/reasoning-effort.md)）
- 2026-11-21: Anthropic の API の版の既定を `2026-10-21` に上げ、この版以降では `temperature=` を送る前に `UnsupportedFeatureError` にする（作者の判断。推奨の案。[判断](../../design-docs/anthropic-api-version.md)）。[負債](../tech-debt-tracker.md) の「Anthropic の API の版が古い」を解消した
- 2026-11-20: 構造化出力は、`response_schema=` に JSON Schema の dict を受け取る。スキーマには、プロパティを並べたオブジェクトへの `additionalProperties: false` だけを足す。結果は `Response.parsed` にも入れる。JSON Schema を受け付けない DeepSeek では送る前に `UnsupportedFeatureError` にする（作者の判断。4つとも推奨の案。[判断](../../design-docs/structured-output.md)）
- 2026-11-18: 履歴の保存・復元は `Message.to_dict()` を足し、`Message.from_dict()` を推論情報ごと元に戻せるように広げる形にする。版の番号は付けず、`ProviderState` は公開しない（作者の判断。[判断](../../design-docs/history-serialization.md)）

- 2026-11-18: 構造化出力と推論の強さの共通化を、[スコープ](../../product-specs/scope.md) の「やらないこと」から外す（作者の判断）。以前は、指定の方法がモデルの世代で変わり、4アダプターで追い続けるのが重いとして見送っていた（[判断](../../design-docs/provider-specific-params.md)）。今回、「LLM をいろいろ変えて試すとき、パラメータ1つだけを変えればよい」ことを機能を選ぶ基準にした。この2つは、今はモデルを変えるたびに呼び出し側の書き方が変わるので、基準に照らして足す
- 2026-11-18: 実 API の E2E は、6つの機能をそろえてから1回で回す
- 2026-11-18: v0.1 は、この6つを入れてから公開する（作者の判断。[v0.1 公開](v0.1-release.md)）
