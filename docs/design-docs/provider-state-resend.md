# 生データを ProviderState に退避して同じモデルへ再送する

- 状態: 採用
- 検証: 一次情報で確認、実 API 未検証（U1、U5、U8、[実 API 検証](../exec-plans/active/real-api-verification.md)）
- コード: `types.py`（`ProviderState`）、`providers/base.py`（`native_raw`）、各アダプターの `build_request` / `parse_response`
- テスト: `*roundtrip*`、`test_parallel_calls_keep_signature_and_order`、`tests/test_cross_provider.py`

## 背景

共通型だけで履歴を持つと、ツール往復に必須の情報が落ちる。

- **Gemini 3 以降**: 関数呼び出しの往復で `thoughtSignature` を返さないと 4xx になる。並列呼び出しでは署名は最初の `functionCall` にだけ付き、`FC1, FC2, FR1, FR2` の順で返す必要がある
- **Anthropic**: thinking とツールを併用するとき、thinking ブロックを signature ごと無加工で返す必要がある
- **OpenAI Responses**: 状態を手動で管理するなら、reasoning アイテムを含む output アイテムを input に戻す

## 決定

- 推論情報を持つアダプター（openai / anthropic / gemini）の `parse_response` は、assistant メッセージに `ProviderState(provider, model, raw)` を付ける
- `build_request` は assistant メッセージで最初に `native_raw()` を見る。**同じプロバイダーキー かつ 同じモデル**のときだけ、生データを無加工で送る
- それ以外（別のモデル、利用者が手で作ったメッセージ）は共通型から変換し直す。thinking は捨てる
- 署名のない履歴を Gemini に送るときは、公式が「最終手段」とするダミー署名 `skip_thought_signature_validator` を最初の functionCall に付ける（`inject_dummy_signatures=False` で無効化できる）

## 却下した案

- **共通型に署名のフィールドを足す**: プロバイダーごとに形が違い、増えるたびに共通型が膨らむ
- **プロバイダーが同じなら別モデルにも再送する**: 署名がモデルをまたいで有効かは保証がない。安全側に倒した（U8）

## 影響

- 利用者は `res.message` を加工せずに履歴へ積む必要がある（README に明記）
- Chat Completions 互換には保持すべき推論情報がないので、`openai_chat` は `ProviderState` を付けない（DeepSeek の `reasoning_content` の扱いは U11）

## 出典

- [Google: Thought signatures](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures)
- [Anthropic: Extended thinking](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking)
