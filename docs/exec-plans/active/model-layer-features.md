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

- [ ] **1. 履歴の JSON 保存・復元**
  - 今の問題: `Message.from_dict()` は推論情報（`provider_state`）を捨てる。保存した会話を復元すると、DeepSeek ではツールを使う会話が 400 になり、Gemini はダミー署名に頼り、OpenAI と Anthropic は推論を引き継げない
  - 決めること: API の形（`Message.to_dict()` / `from_dict()` を推論情報ごと往復させるか、会話の一覧を丸ごと保存する関数を足すか）、保存の形式に版を持たせるか、`ProviderState` を公開するか（[負債](../tech-debt-tracker.md) の「`ProviderState` が公開 API にない」も解消する）、画像（base64）を履歴に含めたときの大きさ
  - テスト: 4アダプターそれぞれで、応答 → JSON → 復元 → 次のリクエストが、保存しなかったときと同じになること
- [ ] **2. 構造化出力**
  - 今の問題: 「やらない」としていたので、モデルを変えるたびに書き方が変わる（OpenAI `text.format`、Gemini `generationConfig.responseSchema` など）
  - 決めること: 共通パラメータの名前と受け取る形（JSON Schema の dict か、型ヒントからも作るか）、結果を文字列のまま返すか、読んだ値も返すか、ストリーミングとツールとの組み合わせ、JSON Schema に対応しない接続先（JSON モードだけのもの、対応しないもの）の扱い
  - 一次情報で確かめる: OpenAI Responses、Chat Completions の `response_format`、Anthropic、Gemini、DeepSeek、Ollama、LM Studio
- [ ] **3. 推論の強さ**
  - 今の問題: 同上（OpenAI `reasoning`、Anthropic `thinking`、Gemini `generationConfig.thinkingConfig`、DeepSeek `thinking` / `reasoning_effort`）
  - 決めること: 共通にする段階（例: なし・弱・中・強）と、各社への対応づけ、対応づけられない指定（推論をなしにできないモデルなど）の扱い、`temperature` など推論と一緒に使えない指定との関係
  - 注意: 指定の方法がモデルの世代で変わる。対応づけは、モデルの一覧を持たずに済む形にする（モデルが受け付けない指定は API のエラーをそのまま返す、という [判断](real-api-verification.md) に合わせる）
  - 一次情報で確かめる: 各社の今の形式
- [ ] **4. 非同期のクライアント**
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

- 2026-11-18: できることとできないことを整理し、基本方針に「機能を足すか絞るかの基準」を足した。この基準に照らして6つを選び、作者がすべて必要と判断したので、この計画を作った

## 判断ログ

- 2026-11-18: 構造化出力と推論の強さの共通化を、[スコープ](../../product-specs/scope.md) の「やらないこと」から外す（作者の判断）。以前は、指定の方法がモデルの世代で変わり、4アダプターで追い続けるのが重いとして見送っていた（[判断](../../design-docs/provider-specific-params.md)）。今回、「LLM をいろいろ変えて試すとき、パラメータ1つだけを変えればよい」ことを機能を選ぶ基準にした。この2つは、今はモデルを変えるたびに呼び出し側の書き方が変わるので、基準に照らして足す
- 2026-11-18: 実 API の E2E は、6つの機能をそろえてから1回で回す
- 2026-11-18: v0.1 は、この6つを入れてから公開する（作者の判断。[v0.1 公開](v0.1-release.md)）
