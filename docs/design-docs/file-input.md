# ファイル（PDF など）は FilePart で渡し、各社が受け付ける種類はそのまま送る

- 状態: 採用（2026-11-23、作者の判断）
- 検証: モックで確認。実 API は未確認（[実 API 検証](../exec-plans/active/real-api-verification.md) の U28〜U31）
- コード: `types.py`（`FilePart`）、`client.py`（送る前の検査）、4つのアダプター、`providers/__init__.py`（プリセットの `file_input`）
- テスト: `tests/test_file_input.py`、`tests/test_history.py`
- 計画: [モデルの層の機能を足す](../exec-plans/completed/2026-11-23-model-layer-features.md) の 6

## 背景

画像（`ImagePart`）しか送れなかった。PDF などの文書を読ませる（要約する、項目を抜き出す、質問に答えさせる）のは、LLM をコードで呼ぶときのよくある使い方だが、各社で書き方が違う。

- OpenAI Responses: `{"type": "input_file", "file_data": ..., "filename": ...}`
- OpenAI の Chat Completions: `{"type": "file", "file": {"file_data": ..., "filename": ...}}`
- Anthropic: `{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": ...}}`
- Gemini: `{"inlineData": {"mimeType": "application/pdf", "data": ...}}`

受け付ける種類も違う（出典は `docs/references/` の4つの文書の「ファイルの入力」）。

- PDF は4つすべてが受け付ける
- OpenAI Responses は、文書・表計算・テキストなどの長い一覧を受け付ける。OpenAI の Chat Completions は PDF だけ
- Anthropic は PDF とテキストで、.docx・.xlsx は受け付けない
- Gemini は PDF とテキストなどを受け付け、一覧は網羅的でないとされる

## 決定

### 型

- **型は `FilePart(data, media_type, filename=None)`**（作者の判断）。`data` は base64 の文字列
- `FilePart.from_file(path)` で、ファイルから作る
  - `filename` はファイル名
  - `media_type` は拡張子の表から決め、表にない拡張子は `mimetypes`、それでも分からなければ `application/octet-stream` にする
  - 表を先に引くのは、`mimetypes` の結果が OS で変わるため（Windows では `.csv` が `application/vnd.ms-excel` になることがある）
  - `media_type` は `from_file(path, media_type=...)` で指定もできる
- `FilePart` を直接作るときは、`media_type` を省けない（PDF でないものを PDF として送らないため）

### 種類は、各社が受け付けるものをそのまま送る（作者の判断）

| アダプター | 送る形 | 種類 |
|---|---|---|
| OpenAI Responses | `input_file` の `file_data`（data URL）と `filename` | 確かめずに送る（文書の一覧が長く、拡張子で書かれている） |
| Chat Completions 互換 | `{"type": "file", "file": {"file_data": data URL, "filename"}}` | PDF だけ（OpenAI の文書）。ほかは送る前に `UnsupportedFeatureError` |
| Anthropic | PDF は base64 の `document`。テキストは UTF-8 に戻して `text` の `document`（`media_type` は `text/plain`） | PDF と、テキスト（`text/*`・`application/json`・`application/xml`）。ほかは送る前に `UnsupportedFeatureError`（文書が .docx・.xlsx を受け付けないと書く） |
| Gemini | `inlineData` の `mimeType` と base64 | 確かめずに送る（文書の一覧は「not comprehensive」） |

- 確かめずに送るアダプターでは、受け付けない種類は API のエラー（400 など）になる
- 受け付けないと文書で分かっているものだけ、送る前に止める
- どちらの場合も、黙って落とさない（[CLAUDE.md](../../CLAUDE.md) の不変条件 4）

### 送らないもの・送り方

- **画像・音声・動画は `FilePart` では送らない。** 送る前に `UnsupportedFeatureError` にする。画像には `ImagePart` がある。音声と動画は、この計画に入れていない（[スコープ](../product-specs/scope.md) の「未定」）
- **URL は受け付けず、base64 だけにする**（作者の判断）。あとで足せる
  - URL を受け付けるのは、OpenAI Responses の `file_url` と Anthropic の `url` だけ
  - Gemini の generateContent は文書に例がなく、Chat Completions は受け付けない
- **Chat Completions 互換は OpenAI の形で送り、受け付けないと分かっているプリセットでは送る前に止める**（作者の判断）
  - プリセット別のオプション `file_input`（既定は `True`）を足した
  - DeepSeek（文書は `file_id` でだけ受け付け、形も違う）・Ollama・LM Studio（文書にファイルの記述がない）のプリセットを `False` にした
  - 2026-11-28 に見直した。DeepSeek の Chat Completions のリファレンスに `file_data` が載ったが、文書の読み込みのガイドと食い違うので `False` のまま（[判断](provider-capabilities.md)）
  - `json_schema: False` と同じ仕組みで、`register_provider(..., file_input=False)` や `Client(..., file_input=False)` でも指定できる
- **`filename` は、`FilePart` にあるときだけ送る**（OpenAI の2つのアダプター）
  - 文書では省ける（U28）。`from_file()` は必ず付ける
  - Anthropic の `title` には入れない（モデルに渡す内容を、利用者に断らずに足さない）
  - Gemini の `inlineData` には、送る場所がない
- パーツの順は変えない。Anthropic の文書は PDF を文字より先に置くよう勧めるので、README の例でも先に置く
- 履歴の保存（`to_dict()`・`from_dict()`）では `{"type": "file", "data", "media_type", "filename"}` にする（[判断](history-serialization.md)）

### 送る前の検査

- **送る前に分かる誤りは、送る前に知らせる**
  - `data` と `media_type` が空でない文字列でなければ `TypeError`
  - Anthropic でテキストとして送るときに、`data` が base64 として読めなければ `ValueError`、UTF-8 として読めなければ `UnsupportedFeatureError`
- **中身のない `ImagePart` も、送る前に知らせる**（2026-11-24、作者の判断）
  - アダプターは、`url` があれば `url` だけを送り、なければ `data` と `media_type` を送る
  - そこで、`url` があればそれが文字列であることを、なければ `data` と `media_type` が空でない文字列であることを確かめ、違えば `TypeError` にする
  - 以前は `ImagePart()` を `data:image/png;base64,None` のまま送り、API のエラーで初めて気づいた
  - `url` を送るときは `media_type` を使わないので、空でも止めない
  - JSON から戻した履歴も、同じ検査を通る
- **画像とファイルは、user のメッセージにだけ入れられる。** system・assistant のメッセージに `ImagePart` か `FilePart` を入れると `TypeError`。どのアダプターも、user 以外のメッセージからは文字しか読まず、黙って落としていた（`ImagePart` で確かめた。2026-11-23）

## 却下した案

- **PDF だけを送る**（推奨していた案）: 4つで同じに動くが、OpenAI Responses と Gemini で使える種類（.docx・.csv など）が使えない（作者の判断で却下）
- **種類の一覧を持ち、アダプターごとに照合する**: OpenAI Responses と Gemini の一覧は長く、網羅的でないとされ、増えていく。一覧が古くなると、送れるものを止めてしまう
- **URL も受け付ける**: 受け付けるのは2つのアダプターだけで、ほかでは送る前に止めることになる（作者の判断で、あとに回した）
- **Chat Completions 互換では、すべて止める**: OpenAI の Chat Completions は PDF を受け付けるので、`openai-chat:` でも使えるほうがよい
- **Chat Completions 互換では、形を確かめずに送り、サーバーに任せる**: DeepSeek・Ollama・LM Studio では、送る前に分かることが API のエラーになる
- **`DocumentPart` という名前**: Anthropic の `document` と同じ名前だが、文書でない種類（表計算など）を足すと名前が合わない（作者の判断で `FilePart`）
- **中身のない `ImagePart` を、作るときに `ValueError` にする**: いちばん早く気づける。ただ、`FilePart`（送る前に止める）と止める時期がそろわず、`ImagePart()` を作ること自体ができなくなる（2026-11-24、作者の判断で却下）
- **中身のない `ImagePart` を、そのまま送る**: API のエラーで初めて気づくことになり、エラーの文面も各社で違う

## 影響

- 公開 API に `FilePart` が増える（`__all__`、[公開 API](../product-specs/public-api.md)）
- 送る前の検査（`client.py` の `_check_content`）が `FilePart` を受け付け、user 以外のメッセージの画像とファイルを `TypeError` にする
  - 今まで黙って落としていた `ImagePart` も、送る前の例外になる
  - 2026-11-24 から、中身のない `ImagePart` も `TypeError` にする
- プリセットの `deepseek`・`ollama`・`lmstudio` に `file_input: False` が付く
