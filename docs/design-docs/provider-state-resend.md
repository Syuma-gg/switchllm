# 生データを ProviderState に退避して再送する

- 状態: 採用
- 検証: 一次情報で確認、実 API 未検証（U1、U5、U8、U11、U17、[実 API 検証](../exec-plans/active/real-api-verification.md)）
- コード: `types.py`（`ProviderState`）、`providers/base.py`（`native_raw`、`resend_across_models`）、各アダプターの `build_request` / `parse_response`
- テスト: `*roundtrip*`、`test_parallel_calls_keep_signature_and_order`、`test_reasoning_content_is_*`、`tests/test_cross_provider.py`（`*_other_model_*`）

## 背景

共通型だけで履歴を持つと、ツール往復に必須の情報が落ちる。

- **Gemini 3 以降**: 関数呼び出しの往復で `thoughtSignature` を返さないと 4xx になる。並列呼び出しでは署名は最初の `functionCall` にだけ付き、`FC1, FC2, FR1, FR2` の順で返す必要がある
- **Anthropic**: thinking とツールを併用するとき、thinking ブロックを signature ごと無加工で返す必要がある。モデルを切り替えるときも取り除かずに送ることを求めていて、新しいモデルが読めないブロックは API がエラーにせず、料金もかけずに捨てる
- **OpenAI Responses**: 状態を手動で管理するなら、reasoning アイテムを含む output アイテムを input に戻す
- **DeepSeek（Chat Completions 互換）**: ツールを使うリクエストでは、過去の `reasoning_content` をすべて送り返さないと 400 になる

## 決定

- 推論情報を持つアダプター（openai / anthropic / gemini）の `parse_response` は、assistant メッセージに `ProviderState(provider, model, raw)` を付ける
- `build_request` は assistant メッセージで最初に `native_raw()` を見る。**同じプロバイダーキー かつ 同じモデル**のときだけ、生データを無加工で送る
- **例外: anthropic はモデルが違っても、同じプロバイダーキーなら送る**（`resend_across_models = True`）。Anthropic の文書がこの場面を名指しで扱い、取り除くなと求めているため（2026-11-17、U8。[詳細](../references/anthropic-messages.md)）
- それ以外（別のプロバイダー、openai・gemini の別のモデル、利用者が手で作ったメッセージ）は共通型から変換し直す。thinking は捨てる
- Chat Completions 互換（`openai_chat`）は、`reasoning_content` を返したときだけ `ProviderState` を付ける。送るときは生データ全体ではなく、`reasoning_content` だけを assistant メッセージに足す（互換サーバーが返す message には、リクエストでは受け付けられないフィールドがありうるため）。条件は同じプロバイダーキー かつ 同じモデル（[詳細](../references/openai-chat-compatible.md)）
- 署名のない履歴を Gemini に送るときは、公式が「最終手段」とするダミー署名 `skip_thought_signature_validator` を最初の functionCall に付ける（`inject_dummy_signatures=False` で無効化できる）

## 却下した案

- **共通型に署名のフィールドを足す**: プロバイダーごとに形が違い、増えるたびに共通型が膨らむ
- **プロバイダーが同じなら、どのアダプターでも別のモデルに再送する**: OpenAI と Gemini には、別のモデルへ送ってよいとする文書が見つからない。署名や reasoning アイテムがモデルをまたいで通じる保証がないので、同じモデルのときだけにする。以前は Anthropic も同じ理由で却下していたが、2026-11-17 に文書で確かめて例外にした（U8）
- **anthropic で、別のモデルへは thinking だけを抜いて送る**: 2026-11-17 までの実装。Anthropic は取り除くなと求めていて、読めるモデル（例: Opus 5.5 から Fable 5.1）に推論を引き継げなくなる

## 影響

- 利用者は `res.message` を加工せずに履歴へ積む必要がある（README に明記）。Anthropic の preserved thinking に対応するモデルは、送り返した thinking ブロックの前の内容が変わっていると 400 にすることがある（[前置きの検査](../references/anthropic-messages.md)）
- Anthropic のモデルを切り替えて費用を分けたり、レート制限のときに別のモデルで続けたりしても、読めるモデルには推論が届く。読めないモデルでは API が捨てるので、料金は増えない
- `openai_chat` の `ProviderState` は、DeepSeek 形式の `reasoning_content` を返したときだけ付く（2026-11-17 に U11 で変えた。それまでは付けていなかった）。別のプロバイダーや別のモデルの履歴には `reasoning_content` がなく、DeepSeek がそれを受け付けるかは未確認（U17）

## 出典

- [Google: Thought signatures](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures)
- [Anthropic: Extended thinking](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking)
- [Anthropic: Preserved thinking](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking)（2026-11-17 確認）
- [DeepSeek: Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)（2026-11-17 確認）
