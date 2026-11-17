# 実 API 検証

## 目的

テストはすべてモック（各社ドキュメントの形式を再現したもの）なので、実際の API で動くかはまだ確かめていない。下の「未検証の前提」をすべて「確認済み」か「修正済み」にしたら完了。

## 手順

- [ ] 各社のキーで `scripts/smoke_test.py` を実行する。**料金が発生するので、実行前にユーザーの確認を取る**
  - 例: `python scripts/smoke_test.py anthropic:claude-sonnet-5-5 openai:gpt-5.5 gemini:gemini-flash-latest deepseek:deepseek-flash`
  - text / ツールの往復 / ストリーミングの3つを試す
- [ ] ローカルの Ollama / LM Studio で同じ確認をする（料金なし）
- [ ] 失敗した箇所を直し、同じ形のレスポンスをモックのテストにも足す
- [ ] 下の表を上から潰す。結果は「結果」列に日付つきで書き、分かった仕様は `docs/references/` に反映する
- [ ] [QUALITY_SCORE.md](../../QUALITY_SCORE.md) の「実 API」列を更新する

## 未検証の前提

ID はほかの文書から参照しているので変えない。

| ID | 箇所 | 現在の実装 | 不確実な理由 | 結果 |
|---|---|---|---|---|
| U1 | OpenAI Responses | 同じモデルへは output アイテム（id 付き）をそのまま input に戻す | 既定の `store`（保存あり）なら問題ないはず。`store: false` では id の参照が解決できずエラーになる可能性がある | |
| U2 | OpenAI Responses | assistant の過去の発言を `{"role": "assistant", "content": "文字列"}` で送る | 文字列の content を assistant ロールで受け付けると理解しているが、実リクエストでは未確認 | |
| U3 | OpenAI Responses | function ツールに `strict: false` を明示する | strict の既定値に依存しないための措置。`true` にする場合は全プロパティ required と `additionalProperties: false` が必要 | |
| U4 | Gemini | 関数スキーマを `parameters`（OpenAPI のサブセット）で送る | `parametersJsonSchema` の方が JSON Schema をそのまま扱えるが、REST での挙動を一次情報で確認できなかった。`schema_field` で切り替えられる | |
| U5 | Gemini | 他プロバイダーの履歴を送るとき、最初の functionCall にダミー署名 `skip_thought_signature_validator` を付ける | 公式は「最終手段で、性能が落ちる」と記載。Gemini 2.5 など Gemini 3 系以外に付けて害がないかは未確認。`inject_dummy_signatures=False` で無効化できる | |
| U6 | Gemini | functionCall に id がなければ仮 ID `switchllm_...` を振り、functionResponse には id を付けない | Gemini 3 では id が常に返るという二次情報がある。古いモデルでの挙動は未確認 | |
| U7 | Gemini | generateContent を使う | Google は generateContent を「レガシー」と表記し、新規開発には Interactions API を勧めている。当面は動くはずだが、将来の移行候補 | |
| U8 | Anthropic | 別のモデルへ切り替えても、同じプロバイダーキーなら thinking ブロックを受け取ったまま送る | 文書は「読めないブロックは API がエラーにせず、料金もかけずに捨てる」とするが、実リクエストでは未確認。ツール往復の途中で切り替えたときと、読めない組み合わせ（例: Sonnet 5.5 から Haiku 5.5）で確かめる | 2026-11-17: 文書で確認し、モデルが違っても送るように直した（それまでは thinking を抜いて変換し直していた）。実 API は未確認 |
| U9 | Anthropic | `tool_choice: "none"` を `{"type": "none"}` にする | 対応済みと理解しているが未確認 | |
| U10 | Chat Completions 互換 | ストリーミングで `stream_options: {include_usage: true}` を送る | 拒否するサーバーがあれば `Client(..., stream_usage=False)` で無効化できる | |
| U11 | Chat Completions 互換（DeepSeek 形式） | 同じプロバイダー・同じモデルが返した `reasoning_content` を、assistant メッセージに足して送り返す（ツールの有無にかかわらず） | DeepSeek の文書どおりに直したが、実リクエストでは未確認。`reasoning_content` を返すほかの互換サーバー（LM Studio など）が、送り返したものを受け付けるかも未確認 | 2026-11-17: 文書で確認し、送り返すように直した（DeepSeek は、ツールを使うリクエストで送り返さないと 400）。実 API は未確認 |
| U12 | プリセット | `ollama` / `lmstudio` の base_url | 公式ドキュメントでは確認した。実際の疎通は未確認 | 文書で確認済み（2026-11-09） |
| U13 | サンプル | モデル ID `gpt-5.5` / `claude-sonnet-5-5` / `gemini-flash-latest` | モデル ID は頻繁に変わる。`gemini-flash-latest` は公式の latest エイリアス | |
| U14 | usage | Anthropic の `input_tokens` はキャッシュ分を含まない、Gemini の `candidatesTokenCount` は `thoughtsTokenCount` を含まない、という前提で合算している | 調査したときの出典が記録されていない | |
| U15 | Ollama | `tool_choice` は明示したときだけ送る | Ollama の公式ドキュメントは `tool_choice` を非対応と記載している。明示したときに無視されるのか拒否されるのかは未確認 | 2026-11-15: 指定がなければ送らないように変えた（[切り替えの精査](../completed/2026-11-15-switching-review.md) の S10）。明示したときの挙動は未確認 |
| U16 | ストリーミング（Chat Completions 互換・Gemini） | 終わりの合図（Chat Completions は `finish_reason` か `[DONE]`、Gemini は最後のチャンクの `finishReason`）が来ないまま閉じたら `StreamError` にする | OpenAI の文書は「`[DONE]` の前に usage のチャンクが来る」と書くだけで、常に `[DONE]` で終わるとは明記していない。Gemini と互換サーバーで、最後のチャンクに必ず合図が入るかは未確認。Anthropic の `message_stop` は文書で確認済み | |
| U17 | DeepSeek（思考モード・ツールあり） | 別のプロバイダーや別のモデルの履歴、手で作ったメッセージは、`reasoning_content` を付けずに送る | 文書は「送り返さないと 400」とだけ書き、ほかで作った履歴やモデルの切り替え（`deepseek-flash` → `deepseek-v4-pro`）の扱いを書いていない。400 になるなら、会話の途中で DeepSeek に切り替えられない | |
| U18 | usage（DeepSeek） | `completion_tokens` を、推論の分を含む出力トークン数として `output_tokens` に入れる | `completion_tokens_details.reasoning_tokens` はあるが、`completion_tokens` がそれを含むかは文書にない | |

## 進捗ログ

- 2026-11-09: 旧 `docs/HANDOFF.md`（2026-11-08 時点）の「次にやること」と「未検証の前提」から作成
- 2026-11-09: プリセットの整理にあわせて U12 を `ollama` / `lmstudio` に絞り、公式ドキュメントで base_url を確認した。U14・U15 を追加した
- 2026-11-15: [切り替えの精査](../completed/2026-11-15-switching-review.md) で、U15 の実装を変え（指定がなければ `tool_choice` を送らない）、U16 を足した
- 2026-11-16: 作業を再開した。API キーはどの環境変数のスコープにもなく、Ollama と LM Studio も起動していなかった。料金のかからない準備として、各社の公式文書で前提を調べた。文書で確認できたのは U2・U9・U13・U14（Anthropic 側）。U1 は、今の文書では `store: false` でも reasoning アイテムに `encrypted_content` が既定で付き、`include` の指定は要らない（`docs/references/openai-responses.md` の記述が古い）。U11 は、DeepSeek がツールを使うリクエストでは過去の `reasoning_content` をすべて送り返すことを求め、送らないと 400 になる（今の実装は送り返さないので失敗する）。U8 は、Anthropic が「切り替えるときも thinking ブロックを捨てるな。読めないものは API が料金なしで捨てる」としていて、今の設計（別のモデルには捨てる）と食い違う。調べた結果の表と出典への反映はまだ
- 2026-11-16: `scripts/smoke_test.py` は `tool_choice="required"` を使っているので、Sonnet 5.5 や Opus 5.5 では 400 になる（Anthropic はこれらのモデルでツールの強制を受け付けない）。また、今の smoke test で確かめられるのは表の一部（U3・U4・U6・U10・U12・U13・U15・U16 など）だけなので、残りも1回の実行で確かめられるように拡張する
- 2026-11-17: API が返したエラーを、手を加えずに渡すようにした。`APIError.message` に API の文言だけを入れ、知らない形の本文は HTTP の理由句ではなく本文をそのまま見せる（[RELIABILITY.md](../../RELIABILITY.md)）
- 2026-11-17: DeepSeek に対応した。公式文書（Thinking Mode・Chat Completions API・Tool Calls・Models & Pricing）を読み直し、出典を [Chat Completions 互換](../../references/openai-chat-compatible.md) に書いた。U11 を直し、同じプロバイダー・同じモデルが返した `reasoning_content` を assistant メッセージに足して送り返すようにした。`reasoning`（OpenRouter の形式）は、ストリーミングでもフィールド名を変えずに組み直し、送り返さない。`deepseek` のプリセット（`https://api.deepseek.com`、`DEEPSEEK_API_KEY`）を足した（[判断](../../design-docs/verified-presets.md)）。文書に書かれていない、別の履歴を受け付けるか（U17）と usage の内訳（U18）を表に足した。2026-11-17 時点のモデルは `deepseek-flash` と `deepseek-v4-pro` で、どちらも思考モードが既定。思考モードでは `tool_choice` の `required` とツール名の指定が 400 になるので、smoke test のツールの往復では `auto` にする
- 2026-11-17: U8 を直した。Anthropic の文書（Thinking・Preserved thinking・Prefix checks）を読み、出典を [Anthropic Messages](../../references/anthropic-messages.md) に書いた。文書は「切り替えるときに thinking ブロックを取り除くな。読めないものは API が料金なしで捨て、エラーにしない」「捨てたブロックは会話の変更と数えないので、元のモデルに戻しても両方のブロックが有効なまま」としている。anthropic アダプターだけ `resend_across_models = True` にし、同じプロバイダーキーならモデルが違っても生データを送るようにした。openai と gemini は同じモデルのときだけのまま（別のモデルで変換し直すことをテストで固定した）。あわせて、preserved thinking に対応するモデル（Opus 5.5・Sonnet 5.5・Haiku 5.5 など）は、送り返した thinking ブロックより前の内容（system・tools・それより前のメッセージ）が変わっていると 400 にすることがあると分かった。switchllm は同じ履歴から毎回同じリクエストを組み立てるので、`res.message` を加工しなければ当たらない

## 判断ログ

- 2026-11-09: 公式ドキュメントで確認できていなかった互換クラウド API のプリセット（openrouter / groq / deepseek / xai / mistral / together）は、確認するのではなく削除した（[判断](../../design-docs/verified-presets.md)）
- 2026-11-17: 対応できるプロバイダーはすべて実 API で試す（OpenAI・Anthropic・Gemini・DeepSeek・Ollama・LM Studio）（作者の判断）
- 2026-11-17: DeepSeek に対応する（作者の判断）。U11 を直し、ツールを使う会話で `reasoning_content` を送り返す
- 2026-11-17: Anthropic のモデルどうしで切り替えるときも、thinking ブロックを捨てずに送る（U8）。作者の基準は「一般的に使われる機能なら対応する」。費用での使い分け（難しい部分を大きいモデル、残りを小さいモデル）や、混雑・レート制限のときに別のモデルで続ける使い方は広く使われていて、`with_model()` はこのライブラリの中心の機能であり、Anthropic の文書もこの場面を名指しで扱っているため、対応すると判断した。OpenAI と Gemini は、別のモデルへ送ってよいとする文書が見つからないので、同じモデルのときだけ送る今の形のままにする
- 2026-11-17: モデルが受け付けない指定（Anthropic の一部のモデルでのツールの強制など）は、ライブラリの側で先回りして弾かず、黙って別の指定に替えもせず、API のエラーをそのまま返す（作者の判断）。モデルごとの制限は増え続け、一覧を持つとすぐに古くなるため
