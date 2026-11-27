# headers= はすべてのアダプターで受け付け、同じ名前のヘッダーは利用者の値で置き換える

- 状態: 採用（2026-11-23、作者の判断）
- 検証: モックで確認（HTTP の決まりは RFC 9110 で確認）
- コード: `providers/base.py`（`COMMON_OPTIONS`、`request_headers()`、`headers=` の検査）、4つのアダプターの `build_request`
- テスト: `tests/test_consistency.py`（4アダプター）、`tests/test_client.py`、`tests/test_anthropic.py`
- 計画: [モデルの層の機能を足す](../exec-plans/completed/2026-11-23-model-layer-features.md) の 5

## 背景

`headers=`（リクエストに足すヘッダー）は、Chat Completions 互換のアダプターにだけあった。

- ゲートウェイ（社内のプロキシ、利用量を記録するサービスなど）が独自のヘッダーを求めると、ほかのアダプター（OpenAI Responses、Anthropic、Gemini）へは、`Client` のパラメータを変えるだけでは切り替えられなかった
- ほかのアダプターに渡すと、知らないオプションとして `ConfigurationError` になる

さらに、Chat Completions 互換の `headers=` には不具合があった。

- ヘッダーを dict の `update` で重ねていたので、ライブラリが付ける `Authorization` と、利用者が渡した `authorization`（小文字）が別のものとして残り、2本送っていた
- 1本目にはプロバイダーのキーが入るので、ゲートウェイに本物のキーが届き、どちらで認証されるかもサーバー次第になる

HTTP の決まり（[RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html)）:

- 5.1: 「Field names are case-insensitive」。ヘッダーの名前は大文字と小文字を区別しない
- 5.3: 送る側は、同じ名前のヘッダーを複数送ってはいけない（MUST NOT）。例外は、カンマ区切りのリストとして定義されたヘッダーだけ
- 11.6.2: `Authorization = credentials` で、リストの形ではない。2本送ってよいヘッダーではない

## 決定

### 受け付ける場所と重ね方

- **`headers=` を4つのアダプターすべてで受け付ける。** 基底クラスの共通のオプション（`COMMON_OPTIONS`）にし、アダプターごとには書かない。`register_provider(..., headers=...)` でも渡せる
- **ライブラリが付けるヘッダーと同じ名前なら、利用者の値で置き換える**（作者の判断）
  - 名前は大文字と小文字を区別せずに比べ、同じヘッダーを2本送らない
  - 認証のヘッダー（`Authorization`、`x-api-key`、`x-goog-api-key`）も置き換えられるので、ゲートウェイが独自の認証を求めるときにも使える
  - 名前が違うときは、次の `None` で、ライブラリの認証のヘッダーを止める
  - Chat Completions 互換の `headers=` も、もともと利用者の値を後から重ねていた（大文字と小文字が同じときだけ置き換わっていた）
- **値に `None` を渡すと、ライブラリが付けるそのヘッダーを送らない**（作者の判断。2026-11-23）
  - ゲートウェイがライブラリと違う名前の認証のヘッダーを求めるとき（Anthropic・Gemini で `Authorization`、Azure の形の `api-key` など）に、プロバイダーのキーをゲートウェイへ送らないため（下の「比較で見つけた制約」）
  - requests の値の `None`、openai・anthropic の SDK の `Omit()` と同じ考え方
  - ライブラリが付けないヘッダーに `None` を渡しても、何も起きない
  - httpx が自分で付けるヘッダー（`Host`、`User-Agent`、JSON の本文に付く `Content-Type` など）は止められない

### 認証とキー

- **`headers=` にそのアダプターの認証のヘッダーがあれば、値が `None` でも、APIキーがなくても `ConfigurationError` にしない**
  - キーを持つのがゲートウェイで、利用者はゲートウェイのキーだけを持つ場合のため
  - キーがないときは、ライブラリは認証のヘッダーを付けない
- **Anthropic の `anthropic-version` と `anthropic-beta` は、`headers=` で渡すと `ConfigurationError` にする**
  - 専用のオプション（`anthropic_version=`・`beta=`）があり、`temperature=` を送る前の検査は `anthropic_version=` の版を見る（[判断](anthropic-api-version.md)）。ヘッダーで版を変えると、検査と送る版が食い違う
  - 例外のメッセージで、どちらのオプションを使うかを案内する
- `with_model()` での引き継ぎは、今までと同じ。同じプロバイダーなら引き継ぎ、別のプロバイダーへは引き継がない（認証の情報を別のサーバーへ送らないため。[判断](provider-specific-params.md)）

### 送る前の検査

送る前に分かる誤りは、クライアントを作った時点で知らせる。

- dict でないもの、名前が文字列でないもの、値が文字列でも `None` でもないものは `TypeError`
- 大文字と小文字だけが違う同じ名前を2つ渡したら `ConfigurationError`（どちらを送るか、送るか止めるかを決められない）
- `anthropic-version`・`anthropic-beta` は、値が `None` でも `ConfigurationError`
- 認証のヘッダーにキーが入っていることがあるので、メッセージにはヘッダーの名前と型だけを出し、値は出さない（例外のメッセージはログに残りやすい。[セキュリティ](../SECURITY.md)）

## 却下した案

- **重なったら `ConfigurationError` にする**: キーを渡す方法は `api_key=` の1通りになるが、ゲートウェイが認証のヘッダーの形を変えて求めるときに使えない（作者の判断で却下）
- **ライブラリのヘッダーを優先し、重なった利用者の値は捨てる**: 利用者は、渡したヘッダーが効いていないことに気づきにくい
- **`Content-Type` や認証のヘッダーだけは置き換えさせない**: どれを守るかの一覧を持つことになり、ゲートウェイの使い方を先回りして狭める。`anthropic-version` だけは、ライブラリの検査と食い違うので別に扱う
- **知っている認証のヘッダーの名前（`Authorization`、`x-api-key`、`api-key` など）が渡されたら、ライブラリの認証のヘッダーを自動で外す**: 名前の一覧を持ち続けることになる。プロバイダーのキーとゲートウェイのキーの両方を求めるゲートウェイでは、プロバイダーのキーが消えて困る
- **止めるための値に、`None` ではなく専用の印（SDK の `Omit()` のような）を公開する**: 公開 API が1つ増える。`None` は requests と同じで、型の注釈（`dict[str, str | None]`）だけで伝わる

## ほかのライブラリとの比較（2026-11-23 に確かめた）

公式の SDK を開発の環境とは別の環境に入れ、ローカルの HTTP サーバーで、実際に送られたヘッダーを受けて確かめた（openai 3.57.0、anthropic 2.12.0、google-genai 3.6.1。キーはダミー）。

| | 利用者の値が勝つか | 大文字と小文字 | 認証のヘッダーだけ（キーなし） | ライブラリのヘッダーを消す |
|---|---|---|---|---|
| openai | 勝つ（`default_headers`、さらに `extra_headers`） | 区別しない（1本だけ送る） | `OpenAIError`（キーが要る） | `Omit()` |
| anthropic | 勝つ。`anthropic-version` も置き換えられ、`extra_headers` の `anthropic-beta` が `betas=` に勝つ | 区別しない | 送れる（`X-Api-Key` か `Authorization` があればよい） | `Omit()` |
| google-genai | 勝つ（`http_options.headers`） | 区別する。`X-Goog-Api-Key` を渡すと、キー入りの `x-goog-api-key` と2本送る | `ValueError`（キーが要る） | なし |
| switchllm | 勝つ | 区別しない | 送れる（そのアダプターの認証のヘッダーのとき） | 値の `None`（比較のあとで足した） |

- 出典（各パッケージのソース）:
  - openai の `_base_client.py` の `_merge_headers`（「Merge headers case-insensitively, with later mappings taking precedence」）
  - anthropic の `_base_client.py` の `build_headers`（「later layers taking precedence whatever a name's casing」）
  - google-genai の `_api_client.py` の `patch_http_options`（`{**options_headers, **patch_options_headers}` の dict の重ね合わせ）
- anthropic 2.12.0 は、APIキーを `x-api-key` ではなく `Authorization: Bearer` で送る。そのため `default_headers` に `x-api-key` を渡しても、キー入りの `Authorization` も一緒に送る
- HTTP クライアントの httpx（0.28.1）と requests（2.34.2）も、クライアントのヘッダーをリクエストのヘッダーで、大文字と小文字を区別せずに置き換える。ただし httpx は、1つの dict の中に大文字と小文字だけが違う名前が2つあると、両方を送る（Chat Completions 互換の不具合の原因）

## 比較で見つけた制約（2026-11-23 に `None` で解決）

- ゲートウェイが、ライブラリと違う名前の認証のヘッダーを求めるとき（Anthropic・Gemini で `Authorization`、Azure の形の `api-key` など）は、同じ名前で置き換えるだけでは、ライブラリの認証のヘッダーも残る
- APIキーが環境変数にあるだけでも、プロバイダーのキーがゲートウェイに届いていた（ローカルの HTTP サーバーで確かめた）
- SDK も同じだが、openai・anthropic の SDK と requests には、ライブラリのヘッダーを消す方法（`Omit()`、値の `None`）がある
- そこで、値の `None` で、ライブラリのヘッダーを止められるようにした

```python
Client("anthropic:claude-sonnet-5-5", base_url="https://gateway.example.com",
       headers={"Authorization": "Bearer <ゲートウェイのキー>", "x-api-key": None})
```

## 影響

- 公開 API のオプション表で、`headers` がすべてのアダプターのものになる（[公開 API](../product-specs/public-api.md)）
- Chat Completions 互換で、小文字の `authorization` などを渡したときに2本送っていた不具合が直る
- オプションの照合（`tests/test_client.py`・`tests/test_docs.py`）は、アダプターごとのオプションに共通のオプションを加えて比べる
