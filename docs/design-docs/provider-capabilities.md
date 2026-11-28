# プリセットごとに送れるものを対応表にし、Gemini には URL の画像を送り、Ollama の tool_choice と画像の URL は送る前に止める

- 状態: 採用（2026-11-28、作者の判断）
- 検証: 一次情報で確認、実 API 未検証（U39）
- コード: `providers/gemini.py`（`_url_media_type()`、`_user_parts`）、`providers/openai_chat.py`（`tool_choice`・`image_url` のオプション、`_reject_tool_choice()`）、`providers/__init__.py`（`ollama` のプリセット）
- テスト: `tests/test_support_table.py`（対応表の全セル）、`tests/test_gemini.py`、`tests/test_openai_chat.py`、`e2e/test_e2e_images.py`（`test_image_url`）、`e2e/test_e2e_tools.py`
- 計画: [載せ替えの負担を減らす](../exec-plans/active/model-switch-friction.md) の 5

## 背景

- 接続先にない機能は、送る前に `UnsupportedFeatureError` にし、メッセージで代わりの書き方を案内している（基本方針 4）。困るのは次の2つ
  - 載せ替える前に、どの接続先で何が止まるかが分からない
  - 代わりの書き方を利用者が考えることになる
- 2026-11-28 に各社の文書を見直すと、今の実装と食い違う点が3つあった（[references](../references/index.md)）
  - **Gemini**: 「You can pass publicly accessible HTTPS URLs or pre-signed URLs directly in your request. The Gemini API will fetch the content securely during processing.」
    - 1ファイル 15 MB まで。Gemini 2.0 系は不可
    - 「supported in all of the Gemini API endpoints」とあるが、例は Interactions API だけ
    - switchllm は、URL の画像を送る前に止めていた
  - **DeepSeek**: Chat Completions のリファレンスに、`file` パート（`file_data` に base64）が載った。文書の読み込みのガイドは「can be referenced only by `file_id`」のままで、食い違っている
  - **Ollama**: OpenAI 互換の対応表で、`tool_choice` と画像の URL が未対応（`[ ]`）。switchllm はどちらもそのまま送っていた。黙って無視されると、`tool_choice="none"` でもツールを呼ぶ
- 2026-11-15 に Gemini の画像の URL を止めたのは、ライブラリが URL を取りに行くと、通信先が増え、SSRF の心配も出るからだった（[切り替えの精査](../exec-plans/completed/2026-11-15-switching-review.md)）

## 決定

- **プリセットごとに送れるものの対応表を、[public-api.md](../product-specs/public-api.md) の「プリセットごとに送れるもの」に置く**（作者の判断）
  - 指定ごとに、送る（○）・送る前に止める（×）・送らずに捨てて警告する（△）を書く
  - `tests/test_support_table.py` が、表の全セルを、そのプリセットで実際に呼んで確かめる。表とライブラリの振る舞いが食い違うと落ちる
  - README の載せ替えの手引き（計画の 6）から、この表へリンクする
- **Gemini には、URL の画像を `fileData.fileUri` で送る**（作者の判断）
  - URL を取りに行くのは Google なので、ライブラリの通信先は増えず、SSRF の心配もない
  - `mimeType` は必ず送る（文書は「Always provide the correct MIME type」と勧める）。`ImagePart.media_type` が既定（`image/png`）のままなら、URL の拡張子から推す。利用者が URL の画像に `media_type` を書くことは少ないため
  - generateContent が受け付けるかは、実 API で確かめる（U39）。受け付けなければ API のエラーになる
- **DeepSeek のファイルは、今のまま送る前に止める**（作者の判断）。文書が食い違い、形も OpenAI と違う（`file` で包まない）。文書がそろったら見直す
- **Ollama は、`tool_choice` と画像の URL を送る前に止める**（作者の判断）
  - Chat Completions 互換のオプションに `tool_choice`（既定 `True`）と `image_url`（既定 `True`）を足し、`ollama` のプリセットでは `False` にする。`file_input`・`json_schema` と同じ形で、ほかの互換サーバーでも使える
  - `tool_choice=False` のとき:
    - `"auto"` は既定と同じなので、止めずに送らないだけ
    - `"none"` は、「ツールを渡さない」と案内して止める
    - 強制（`"required"` とツール名）は、「指定せずにプロンプトで頼む」と案内して止める
  - `image_url=False` のとき、URL の画像は base64 で渡すよう案内して止める。履歴に入っていても同じ
- `client.supports()` と、履歴を載せ替え先に合わせる補助は、作らない（作者の判断）

## 却下した案

- **`client.supports(...)` を用意する**: コードで確かめられるが、公開 API が増える。分かるのはプリセットで決まることだけで、モデルごとの制限（計画の 4）は分からない
- **履歴を載せ替え先に合わせる補助**（送れないパーツを外すか置き換える）: 公開 API が増え、何に置き換えるかも決める必要がある
- **Gemini の画像の URL を、ライブラリが取りに行って base64 にする**: 通信先が増え、SSRF の心配が出る（2026-11-15 の判断のまま）
- **DeepSeek のファイルを `file_data` で送る**: 文書が食い違う。送れなかったときに、送る前の案内ではなく API のエラーになる
- **Ollama の `tool_choice` をそのまま送り、実 API で振る舞いを見てから決める**: 黙って無視されるなら、`tool_choice="none"` でもツールを呼ぶ。対応表に未対応とあるので、`file_input` と同じく文書で決める

## 影響

- Gemini で、URL の画像がそのまま使える。対応表で、画像の URL を止めるのは Ollama だけになる
- Ollama で `tool_choice` の `"none"` や強制、URL の画像を使っていたコードは、`UnsupportedFeatureError` になる。0.1.0 は未公開なので、壊れる利用者のコードはない
- E2E の `test_image_url` は、このリポジトリの `e2e/assets/red.png` を GitHub の raw の URL で読ませる。実 API で回す前に、main に push しておく
- 対応表に行や列を足すときは、`tests/test_support_table.py` の `FEATURES` と `MODELS` にも足す
