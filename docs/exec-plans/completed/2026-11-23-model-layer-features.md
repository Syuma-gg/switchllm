# モデルの層の機能を足す

## 目的

LLM をコードで呼ぶときによく使うのに、各社の書き方を書き分けるか、別の手段が要った6つの機能を足す。選んだ基準は、[基本方針](../../design-docs/core-beliefs.md) の「機能を足すか絞るかの基準」。6つすべてをテスト駆動で実装し、文書に反映したら完了。

実 API での実行は、[実 API 検証](../active/real-api-verification.md) の `--record` にまとめた（U19〜U31）。

## 手順

公開後に変えにくいもの（保存の形式、公開 API の形）を先にした。

| # | 機能 | 終えた日 | 設計 | コミット |
|---|---|---|---|---|
| 1 | 履歴の JSON 保存・復元 | 2026-11-18 | [履歴の保存](../../design-docs/history-serialization.md) | `0c25b65` |
| 2 | 構造化出力 | 2026-11-20 | [構造化出力](../../design-docs/structured-output.md) | `9bbe34b` |
| 3 | 推論の強さ（あわせて Anthropic の API の版を上げた） | 2026-11-21 | [推論の強さ](../../design-docs/reasoning-effort.md)・[API の版](../../design-docs/anthropic-api-version.md) | `d63b06a` |
| 4 | 非同期のクライアント | 2026-11-23 | [非同期](../../design-docs/async-client.md) | `8ac0119` |
| 5 | すべてのアダプターに `headers=` | 2026-11-23 | [ヘッダー](../../design-docs/custom-headers.md) | `26e0b6d` |
| 6 | PDF・ファイルの入力 | 2026-11-23 | [ファイルの入力](../../design-docs/file-input.md) | `f6675c5` |

- [x] 6つを実装する（上の表）
- [x] 6つの E2E のケースを足し、[E2E テスト](../../e2e-tests.md) の一覧を直す（2026-11-23）
- [x] 実 API での実行を [実 API 検証](../active/real-api-verification.md) に移す（2026-11-27）

## 進捗ログ

細かい経緯は、上の表のコミットにある。ここには、あとで効く気づきだけを残す。

- 2026-11-18: 基本方針に「機能を足すか絞るかの基準」を足し、それに照らして6つを選んだ
- 2026-11-20: どの社も、構造化出力の JSON を HTTP の応答では文字列で返す。Anthropic の API の新しい版（`2026-10-21`）で `temperature` が 400 になると分かった
- 2026-11-21: 推論の強さの段階の名前は、OpenAI の7つ（`none`〜`max`）がほかの社のものをすべて含む。使える段階はモデルによって違う
- 2026-11-23: 非同期のために、ストリームの解析を、イベントを1つずつ渡す `StreamParser`（`feed()` / `finish()`）に書き直した。前のテスト 389 件が通ることを確かめてから進めた。[スコープ](../../product-specs/scope.md) にあった「`_http.py` に async 版の送信関数を足すだけで済む」は誤りだった
- 2026-11-23: 解析器を使い回す誤り（エージェントのループで同じクライアントの `stream()` を繰り返すと、本文や終わりの合図が混ざる）を、同期・非同期のどちらのテストも見つけられなかった。`tests/test_consistency.py` にテストを足した
- 2026-11-23: Chat Completions 互換の `headers=` が、大文字と小文字の違う `authorization` を渡すと、認証のヘッダーを2本送っていた。直した（RFC 9110 の 5.1・5.3・11.6.2）
- 2026-11-23: 公式 SDK（openai 3.57.0・anthropic 2.12.0・google-genai 3.6.1）が利用者のヘッダーをどう重ねるかを、ローカルの HTTP サーバーで確かめた（結果は [ヘッダー](../../design-docs/custom-headers.md) の表）
- 2026-11-23: どのアダプターも、user 以外のメッセージ（system・assistant）の画像を黙って落としていた。送る前に `TypeError` にした
- 2026-11-23: base64 のまま4つのアダプターすべてで送れるファイルは PDF だけ。Windows の Python 3.12 の `mimetypes` は、`.csv` に `application/vnd.ms-excel` を返し、`.md` は判定できない

## 判断ログ

どれも作者の判断。理由は各設計文書にある。

- 2026-11-18: 構造化出力と推論の強さの共通化を、[スコープ](../../product-specs/scope.md) の「やらないこと」から外す。「LLM をいろいろ変えて試すとき、パラメータ1つだけを変えればよい」を機能を選ぶ基準にしたため（以前の判断は [各社に固有のパラメータ](../../design-docs/provider-specific-params.md)）
- 2026-11-18: v0.1 は、この6つを入れてから公開する。実 API の E2E は、6つをそろえてから1回で回す
- 2026-11-18: 履歴の保存・復元は、`Message.to_dict()` を足し、`Message.from_dict()` を推論情報ごと戻せるようにする。版の番号は付けず、`ProviderState` は公開しない
- 2026-11-20: 構造化出力は `response_schema=` に JSON Schema の dict を受け取り、結果を `Response.parsed` にも入れる。DeepSeek では送る前に `UnsupportedFeatureError`
- 2026-11-21: 推論の強さは `reasoning_effort=` に OpenAI の7段階の名前を受け取る。各社に名前のない段階は、近い段階に替えずに `UnsupportedFeatureError`
- 2026-11-21: Anthropic の API の版の既定を `2026-10-21` に上げ、`temperature=` を送る前に `UnsupportedFeatureError` にする
- 2026-11-23: 非同期は別のクラス `AsyncClient`。`async def` のツールのために `arun_tool_calls()` を足し、並行に実行する
- 2026-11-23: `headers=` は、ライブラリが付けるヘッダーと同じ名前なら、大文字と小文字を区別せずに置き換える。値の `None` で、そのヘッダーを送らない
- 2026-11-23: ファイルの入力は `FilePart`。各社が受け付ける種類をそのまま送り、受け付けないと分かっているものだけ送る前に止める。base64 だけで、URL は受け付けない
