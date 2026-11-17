# 履歴を to_dict / from_dict で保存・復元する

- 状態: 採用（2026-11-18、作者の判断）
- 検証: モックで確認（4アダプター）、実 API 未検証（E2E の `test_restored_history_continues`）
- コード: `types.py`（`Message.to_dict()` / `Message.from_dict()`）
- テスト: `tests/test_history.py`
- 計画: [モデルの層の機能を足す](../exec-plans/active/model-layer-features.md) の 1

## 背景

Web アプリやエージェントは、会話をファイルやデータベースに保存し、次のリクエストで読み戻す。以前は `Message.from_dict()` が推論情報（`provider_state`）を捨てていたので、復元すると次のことが起きた。

- DeepSeek は、ツールを使う会話で過去の `reasoning_content` を送り返さないと 400 を返す
- Gemini はダミー署名に頼る（公式は性能が落ちうると書いている）
- OpenAI と Anthropic は、推論を引き継げない

推論情報の再送はこのライブラリの中心の機能（[判断](provider-state-resend.md)）なので、保存をはさむと失われるのは穴だった。pickle なら保存できたが、JSON にする方法はなかった。

## 決定

- `Message.to_dict()` を足す。JSON にそのまま書ける dict を返し、推論情報（`provider_state` の `provider` / `model` / `raw`）、画像のパーツ、ツール呼び出し（`raw_arguments` を含む）、ツールの結果（`tool_call_id` / `name` / `is_error`）を含める。既定値の項目は省く
- `Message.from_dict()` を広げ、`to_dict()` の結果を元に戻せるようにする（`from_dict(m.to_dict()) == m`）。以前から受け付けていた簡単な形（`{"role": "user", "content": "..."}`、tool の `id` の別名）もそのまま読める。`content` が `None`（OpenAI の形で、ツールだけを呼んだ assistant）なら空にする
- `Client.chat()` / `stream()` はもともと dict のリストを受け取るので、`json.loads()` した結果をそのまま渡せる
- 保存の形式に版の番号は付けない。項目を足すだけなら互換を保てる。互換を壊す変更が要るようになったら、そのときに版を足す（版がないものを今の形式として読む）
- `ProviderState` は公開 API にしない。利用者が作る必要はなく、保存と復元は `to_dict()` / `from_dict()` で済む（[公開 API を絞る](minimal-public-api.md)）
- 知らない種類のパーツの dict は変換せずに残し、送る前の検査で `TypeError` にする（黙って落とさない）

## 却下した案

- **保存用の関数を足す（`dumps_messages()` / `loads_messages()`）**: 公開 API が2つ増え、文字列でしか受け渡せない。データベースに1メッセージずつ保存するときに使いにくい
- **各メッセージに版の番号を入れる**: 将来の読み分けはしやすいが、保存するデータが増え、今は使い道がない
- **`ProviderState` を公開する**: 保存のためには要らない。型ヒントで要るようになったら検討する（[負債](../exec-plans/tech-debt-tracker.md)）

## 影響

- 生データ（`provider_state.raw`）は、各社が返したものをそのまま保存する。プロバイダーが形式を変えても、保存した古いデータは同じプロバイダー・同じモデルへ送るときにそのまま使われる（送る相手が受け付けなければ API のエラーになる）
- 復元した生データはそのままプロバイダーに送られる。読み込むのは自分で保存した履歴だけにする（[SECURITY.md](../SECURITY.md)）
- 画像を base64 で履歴に入れると、保存するデータも大きくなる
