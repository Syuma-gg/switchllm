# 非同期のクライアントは AsyncClient として分け、アダプターを同期版と共有する

- 状態: 採用（2026-11-23、作者の判断）
- 検証: モックで確認、実 API 未検証（E2E の `test_e2e_async.py`）
- コード: `client.py`（`AsyncClient`、同期版と共有する `_BaseClient`）、`_http.py`（`arequest_json`・`astream_sse`）、`providers/base.py`（`StreamParser`、`parse_stream`・`aparse_stream`）、4つのアダプターのストリームの解析、`tools.py`（`arun_tool_calls`）
- テスト: `tests/test_async_client.py`。`tests/conftest.py` の `api` を使うテストは、同期と非同期の両方で回る
- 計画: [モデルの層の機能を足す](../exec-plans/completed/2026-11-23-model-layer-features.md) の 4

## 背景

同期の `Client` しかなかった。ADK をはじめ、エージェントフレームワークの多くや FastAPI は非同期で動く。そこから同期の `Client` を呼ぶと、応答を待つ間イベントループが止まり、ほかの処理も止まる。

アダプターのうち `build_request()`（共通型 → HTTP リクエスト）と `parse_response()`（JSON → `Response`）は通信をしないので、同期でも非同期でもそのまま使える。ストリーミングの解析（`parse_stream()`）だけは、同期のイテレーターから SSE のイベントを1つずつ引き出す作りで、非同期の読み取りからは呼べなかった。

## 決定

### 使い方

- **`AsyncClient` を別のクラスとして足す**（作者の判断）。コンストラクターの引数、`chat()`・`stream()` の引数、`with_model()`・`model` は `Client` と同じ。同期から非同期へは、クラス名を替えて `await` を足すだけで移れる
  - `await client.chat(...)` が `Response` を返す
  - `client.stream(...)` は `async for` で直接回す（作者の判断）。`await` は付けない。同期版の `for ev in client.stream(...)` と同じ形
  - 閉じるのは `await client.aclose()` か `async with AsyncClient(...) as client:`。同期の `close()` と `with` は持たない（httpx の `AsyncClient` と同じ）
  - `http_client=` には `httpx.AsyncClient` を渡す。`with_model()` で作ったクライアントは、この接続を共有する
- **`http_client=` の取り違えは、作った時点で `TypeError` にする。** `Client` に `httpx.AsyncClient` を、`AsyncClient` に `httpx.Client` を渡すと、最初の送信で分かりにくいエラーになるため。`Client` にも同じ検査を足す
- **ストリームを途中で抜けたとき**: 最後まで読めば接続は閉じる。途中で `break` したときは、ジェネレーターが片づけられるときに閉じる。すぐに閉じたいときは `contextlib.aclosing()` で囲む、と文書に書く
- **リトライは同期版と同じ**（再送するステータス、`Retry-After`、バックオフ、ストリームは最初のイベントの前だけ。[信頼性](../RELIABILITY.md)）。待つのは `asyncio.sleep` で、待つ間もイベントループを止めない

### ツールの実行

- **`arun_tool_calls(tools, calls)` を足す**（作者の判断）。`async def` のツールは `await` し、普通の関数はそのまま呼ぶ。複数の呼び出しは `asyncio.gather` で並行に実行し、結果は呼び出しの順に返す。例外を `is_error=True` の結果にすること、壊れた引数で関数を呼ばないことは `run_tool_calls` と同じ
  - 普通の関数は、イベントループの中でそのまま呼ぶ。重い処理なら、ツールの中で `asyncio.to_thread` を使う
- **同期の `run_tool_calls`（`Tool.execute`）に `async def` のツールが来たら、`is_error=True` の結果を返す。** これまでは関数を実行せず、コルーチンを文字列にしたもの（`<coroutine object ...>`）を結果としてモデルに返していた。作ったコルーチンは閉じて、「never awaited」の警告も出さない

### 同期版との共有

- **アダプターは同期と非同期で同じものを使う。** ストリームの解析は、イベントを1つずつ受け取る `StreamParser`（`feed(event, data)` と `finish()`）に書く。同期の `Provider.parse_stream()` と非同期の `Provider.aparse_stream()` は、どちらもこれを回すだけで、アダプターは解析を1回だけ書く
  - `feed()` はイベント1つから作った `StreamEvent` を返す。終わりの合図（`[DONE]`、`message_stop`、`response.completed`）を受け取ったら `closed` を立て、残りのイベントは読まない
  - `finish()` はストリームが終わったら呼び、最後の `done` を返す。終わりの合図がなければ `StreamError`。`done` を1回だけ返し、非ストリーミングと同じ形の dict から `parse_response()` で作ることは変えない（[CLAUDE.md](../../CLAUDE.md) の不変条件 2）
- **`_http.py` に非同期の送信（`arequest_json`）と SSE の読み取り（`astream_sse`）を足す。** SSE の分解とリトライの判断は同期版と共有し、違うのは `await` するところだけにする
- **`Client` と `AsyncClient` は、モデル文字列の解決、送る前の検査、`ChatRequest` の組み立て、`with_model()` の引き継ぎを、共通の `_BaseClient` で共有する。** `chat()`・`stream()` の引数は公開 API なので両方のクラスに書き、同じであることをテストで確かめる

### テスト

- `tests/conftest.py` の `api` を、同期と非同期の2通りで回す。非同期のときの `api.client()` は、`AsyncClient` を同期のテストから呼べるように包んだものを返す（`chat()` は1つのイベントループで完了まで回し、`stream()` は `async for` を1つずつ進める）。同期版と同じテストで、非同期版の振る舞いを確かめる
- テストのための依存は増やさない（pytest-asyncio などのプラグインは入れず、`asyncio` の標準の機能で回す）
- ストリームの解析を、ストリームごとに作るオブジェクトにしたので、状態が別のストリームへ漏れる誤りが起こりうる。同じクライアントで複数のストリームを読んでも混ざらないことを、4アダプターと同期・非同期の両方で確かめる（`tests/test_consistency.py`）
- 取り消し（`asyncio.wait_for` のタイムアウト、FastAPI の切断）では、再送せずに止まり、HTTP の応答を閉じる。`asyncio.CancelledError` は `Exception` の子ではないので、リトライの `except httpx.TransportError` と、ツールの例外を結果にする `except Exception` のどちらにも入らない

## 却下した案

- **`Client` に `achat()` / `astream()` を足す**: 1つのクラスが同期と非同期の HTTP クライアントを両方持ち、閉じ方と `http_client=` の意味が分かりにくくなる（作者の判断で却下）
- **`async with client.stream(...) as events:` の形にする**: 抜けたらすぐ閉じるが、同期版と書き方が変わる（作者の判断で却下）。すぐ閉じたいときは `contextlib.aclosing()` で同じことができる
- **`AsyncClient` を、同期の `Client` をスレッドで回す形にする**（`asyncio.to_thread`）: 呼び出しごとにスレッドを使い、取り消し（cancel）しても HTTP の送信は止まらない
- **同期の `Client` を、`AsyncClient` を `asyncio.run` で回す形にする**: イベントループが動いている中（Jupyter、FastAPI）から同期版を呼べなくなる
- **`parse_stream()` を同期のジェネレーターのまま残し、非同期の側からスレッドとキューで渡す**: 解析が別のスレッドで動き、例外と後片づけの扱いが込み入る
- **非同期では、ストリームを最後まで受け取ってから解析する**: ストリーミングの意味がなくなる
- **`arun_tool_calls` を1つずつ順に実行する**: 同期版と同じ順序で副作用が起きるが、モデルが一度に複数の呼び出しを返すのは、互いに独立したものだからで、並行に実行したほうが速い（作者の判断で却下）
- **`arun_tool_calls` で、普通の関数をスレッドで実行する**: 速くなることはあるが、利用者の関数をいつの間にか別のスレッドで動かすことになる。必要ならツールの中で `asyncio.to_thread` を使える
- **同期の `run_tool_calls` に `async def` のツールが来たら、例外にする**: ほかの失敗と同じく `is_error=True` の結果にそろえた（作者の判断）

## 影響

- 公開 API に `AsyncClient` と `arun_tool_calls` が増える（[公開 API](../product-specs/public-api.md)）
- アダプターが実装するメソッドは `build_request()`・`parse_response()`・`stream_parser()` の3つになる（[アーキテクチャ](../../ARCHITECTURE.md)）
- `api` を使うテストは、同期と非同期の2回ずつ回る
- E2E に `test_e2e_async.py` を足す（1モデル 2 回）
