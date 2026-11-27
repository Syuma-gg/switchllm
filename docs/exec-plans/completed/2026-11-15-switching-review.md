# 切り替えの精査とテストの追加

## 目的

[基本方針の優先順位](../../design-docs/core-beliefs.md)（切り替えを楽にすることは必須、軽量に保つ）に照らして機能を精査し、見つけた問題を直す。あわせて、振る舞いを確かめていない箇所にテストを足す。下の「精査の結果」がすべて「済み」になり、カバレッジを測り直して `fail_under` を引き上げたら完了。

## 手順

- [x] 精査する（2026-11-15。結果は下の表）
- [x] 一次情報で確かめる（S10・S11。2026-11-15）
- [x] 不具合を直す（S1〜S6。2026-11-15）
- [x] 切り替えの手間を減らす（S7〜S15。2026-11-15）
- [x] テストを足す（2026-11-15。`_http.py` のリトライとエラーの解釈、3つのアダプターのストリーミング中のエラー、上の修正分）
- [x] 文書を直す（2026-11-15。README 2つ、public-api、scope、RELIABILITY、SECURITY、references、CHANGELOG、QUALITY_SCORE、tech-debt-tracker、ARCHITECTURE、設計判断）
- [x] カバレッジを測り直し、`fail_under` を引き上げる（2026-11-15。87.68% → 98.55%、基準 87 → 98）

## 精査の結果

再現は、修正前のコードに対してスクラッチのスクリプトで確かめた（2026-11-15）。

| ID | 区分 | 見つけたこと | 対応 | 状態 |
|---|---|---|---|---|
| S1 | 不具合 | `register_provider("MyCloud")` で登録した名前が使えない。モデル文字列は小文字にしてから引くため | 登録するときに小文字にそろえる。空の名前と `:` を含む名前は `ConfigurationError` || 済み |
| S2 | 不具合 | Anthropic のストリーミングで、tool_use の JSON が途中で切れると `JSONDecodeError` になる（max_tokens で切れたとき） | 他のアダプターと同じく `arguments={}` にし、原文を `raw_arguments` に残す || 済み |
| S3 | 不具合 | `Retry-After` が負の値や `nan` だと `time.sleep` が `ValueError` | 0〜60 秒に収める。数値として読めなければ通常のバックオフ || 済み |
| S4 | 不具合 | content のリストに `TextPart` / `ImagePart` 以外（dict、str）を入れると、黙って落として送る | 送る前に `TypeError` || 済み |
| S5 | 不具合 | `with_model()` が `headers=` などのオプションを別のプロバイダーへ引き継ぐ。認証ヘッダーが別のサーバーに送られる | 別のプロバイダーへは引き継がない || 済み |
| S6 | 不具合 | 2xx で JSON でない本文（プロキシの HTML など）だと、`SwitchLLMError` ではなく `JSONDecodeError` | `APIError` にする || 済み |
| S7 | 切り替え | `with_model()` が同じプロバイダーでも api_key / base_url を引き継がない。`api_key=` で渡していると、同じ社の別モデルへの切り替えで落ちる | 同じプロバイダーなら引き継ぐ || 済み |
| S8 | 切り替え | プロバイダー別のオプション名を打ち間違えても、別のアダプター用のオプションを渡しても、黙って無視する | アダプターごとにオプション名を決め、それ以外は `ConfigurationError` || 済み |
| S9 | 切り替え | ストリームが完了の合図なしに閉じたときの扱いがアダプターで違う。Responses は `StreamError`、Anthropic は途中までを `done`、Chat Completions と Gemini は `stop_reason="end"` の `done`（正常終了に見える） | すべて `StreamError` にそろえる || 済み |
| S10 | 切り替え | `tool_choice` を指定しなくても常に送る。Ollama は `tool_choice` 非対応と明記している（U15） | 指定がなければ送らない。4社とも省略時の既定は `auto`（一次情報で確認） || 済み |
| S11 | 切り替え | `openai-chat` が OpenAI 本家にも `max_tokens` を送る。OpenAI は非推奨で、o-series とは互換性がないと記載している | 接続先が OpenAI 本家のときだけ `max_completion_tokens` を既定にする。Ollama の文書には `max_tokens` しかないので、互換サーバーは今のまま || 済み |
| S12 | 切り替え | Anthropic だけ max_tokens が必須で、指定しないと 4096 になる。同じコードで Anthropic だけ出力が切れることがある | README の「既知の制限」に書く（どの値を既定にしても、対応していないモデルがある） || 済み |
| S13 | 切り替え | `extra=` はプロバイダーごとに中身が違うので、構造化出力や推論の強さを使うコードは、モデルを変えると呼び出し側も直す必要がある | `extra_by_provider=` で各社の分を1か所に書き、選んだプロバイダーの分だけ送る（2026-11-15 にユーザーが選んだ） || 済み |
| S14 | 切り替え | ツール引数の JSON が壊れていると、`run_tool_calls` は「引数が足りない」という `TypeError` をモデルに返す。弱いモデル（ローカルなど）に切り替えたときに起きやすい | 「引数が JSON として壊れている」と返し、モデルが直して呼び直せるようにする || 済み |
| S15 | 切り替え | `tool_choice` が `tools` にない名前を指していても送る。`tools` なしで `"required"` を指定すると黙って無視する | 送る前に `ConfigurationError` || 済み |

### 今回は直さないもの

| 見つけたこと | 理由 |
|---|---|
| Gemini は画像の URL を受け付けない（`UnsupportedFeatureError`） | API の制限。ライブラリが URL を取りに行くと、通信先が増え、SSRF の心配も出る |
| OpenAI Responses は `stop` を受け付けない（`UnsupportedFeatureError`） | API に機能がない |
| `APIConnectionError` に `provider` 属性がない | メッセージには入っている。フォールバックのコードで要るという要望が出たら足す |
| 会話の履歴を保存して読み戻す仕組み（`Message.to_dict`）がない | 切り替えそのものではない。[scope.md](../../product-specs/scope.md) の「未定」に足すかは別に決める |
| 構造化出力の共通化 | 今回は S13 で、各社の分を1か所に書けるようにするだけにした（ユーザーの判断） |

## 進捗ログ

細かい経緯は、コミット `0942986` にある。

- 2026-11-15: コード全体とテストを読み、上の表の S1〜S15 を見つけた。S1〜S8 はスクラッチのスクリプトで再現した
- 2026-11-15: `tool_choice` の省略時の既定は、4社とも `auto` だった。OpenAI Chat Completions の `max_tokens` は非推奨で、o-series とは互換性がない。Ollama は `tool_choice` を非対応とし、`max_completion_tokens` は載っていない。出典は `docs/references/` にある
- 2026-11-15: S1〜S11・S13〜S15 は、修正前のコードに新しいテストを当て、落ちることを確かめた（S12 は文書だけ）。S5 のテストは、最初は引き継ぎ先を Anthropic にしていたので、修正前でも通ってしまった（Anthropic は `headers` オプションを読まない）。同じアダプターを使う Ollama への切り替えに書き直した
- 2026-11-15: テストを 76 件から 216 件にし、カバレッジは 87.68% から 98.55% になった。新しいファイルは `tests/test_http.py`（リトライと例外）、`tests/test_consistency.py`（同じテストを4アダプターで回す）、`tests/test_types.py`（共通型）
- 2026-11-15: U16 を [実 API 検証](../active/real-api-verification.md) に足した。完了し、`completed/` に移した

## 判断ログ

- 2026-11-15: 基本方針の優先順位を決めた（作者の指示）。切り替えを楽にすることは必須、軽量に保つのはその次（[core-beliefs.md](../../design-docs/core-beliefs.md)）
- 2026-11-15: `extra=` のプロバイダー依存は、意味を共通化せず、各社の分を1か所に書ける `extra_by_provider=` で解く（ユーザーが4つの案から選んだ。ほかの案は、構造化出力の共通化、その両方、今のまま）。キーはモデル文字列の `provider:` 部分。知らない名前は `ConfigurationError`
- 2026-11-15: S11 は、プリセットの既定を変えるのではなく、接続先が OpenAI 本家（`https://api.openai.com/v1`）のときだけ `max_completion_tokens` にする。`Client("openai-chat:<model>", base_url=...)` で互換サーバーを使う書き方を README で案内しているため、プリセットで変えると互換サーバーに `max_completion_tokens` が届く
- 2026-11-15: S9 で、ストリームが合図なしに閉じたら例外にする。途中までの応答を正常終了として返すと、利用者は切れたことに気づけないため。完了の合図は、Responses が `response.completed` / `response.incomplete`、Anthropic が `message_stop`、Chat Completions が `finish_reason` か `[DONE]`、Gemini が `finishReason` か `promptFeedback.blockReason`。Chat Completions と Gemini の合図が必ず届くかは実 API で確かめる（U16）
