# プロバイダー固有の指定は、各社の分を並べて渡す

- 状態: 採用（2026-11-15）。`Client(...)` のオプションにも同じ形を広げた（2026-11-28、下の「`Client(...)` のオプション」）
- 検証: —（各社の API の形式には関わらない）
- コード: `client.py`（`_pick_extra`、`_pick_options`、`Client.with_model`）、`providers/base.py`（`Provider.option_names`、`check_options`）

## 背景

`extra=` は、各社の形式のパラメータをリクエスト本体にそのままマージする逃げ道で、構造化出力や推論の強さなど共通化しないと決めたもの（[スコープ](../product-specs/scope.md)）はここで渡す。ただし中身はプロバイダーごとに違うので、`extra=` を使うコードは、モデル文字列を変えると呼び出し側も直す必要があった。[基本方針の優先順位](core-beliefs.md) の 1（切り替えを楽にすることは必須）に反する。

`Client(..., **options)` のプロバイダー別のオプションにも、切り替えで困る点があった。

- 知らない名前を黙って無視するので、打ち間違いや、別のアダプター用のオプションに気づけない
- `with_model()` は、別のプロバイダーにもオプションをそのまま引き継いでいた。`headers=` に認証情報を入れていると、別のサーバーへ届く
- `with_model()` は、同じプロバイダーの別モデルでも api_key / base_url を引き継がないので、`api_key=` で渡していると切り替えで落ちる

## 決定

- `chat()` / `stream()` に `extra_by_provider=` を足す。`{"anthropic": {...}, "openai": {...}}` の形で各社の分を並べ、モデル文字列の `provider:` 部分と同じ名前の分だけを `extra=` に深くマージする（同じキーはプロバイダーの分が優先）
  - 名前は大文字小文字を区別しない。登録されていない名前は `ConfigurationError`、値が dict でなければ `TypeError`（打ち間違いを黙って無視しない）
  - 書いていないプロバイダーには何も足さない
- 意味の共通化（推論の強さを `low` / `medium` / `high` にそろえる、など）はしない。指定方法がモデルの世代ごとに変わり、4アダプターで追い続けるのは重い（方針 1「薄く保つ」）。**2026-11-18 に見直した**: 推論の強さと構造化出力は共通化する（[計画](../exec-plans/completed/2026-11-23-model-layer-features.md)）。構造化出力は 2026-11-20 に `response_schema=` として（[判断](structured-output.md)）、推論の強さは 2026-11-21 に `reasoning_effort=` として足した（[判断](reasoning-effort.md)）。`extra=` / `extra_by_provider=` は、共通化しないものの逃げ道として残す
- アダプターごとに、受け付けるオプション名を `Provider.option_names` に決め、それ以外は `ConfigurationError` にする。`register_provider()` も登録の時点で検査する。一覧は [public-api.md](../product-specs/public-api.md) にあり、ソースが読むオプションとの一致は `tests/test_client.py`、文書との一致は `tests/test_docs.py` が検査する
- `with_model()` は、同じプロバイダーなら api_key / base_url / オプションを引き継ぎ、別のプロバイダーへは timeout / max_retries / retry_backoff だけを引き継ぐ（2026-11-28 から `options_by_provider=` も。下の節）

### `Client(...)` のオプション（2026-11-28）

背景: 知らないオプション名を `ConfigurationError` にしたので、`Client(..., default_max_tokens=...)` のような接続先ごとのオプションを付けたまま設定で載せ替えると、`Client` を作るところで落ちた。呼び出しの引数には `extra_by_provider=` があるが、`Client(...)` のオプションには、接続先ごとに書き分ける仕組みがなかった（[載せ替えの負担を減らす](../exec-plans/active/model-switch-friction.md) の 3）。

- **`Client(...)` と `AsyncClient(...)` に `options_by_provider=` を足す**（作者の判断）
  - 形は `extra_by_provider=` と同じで、キーはモデル文字列の `provider:` 部分（大文字小文字を区別しない）
  - 選んだ接続先の分だけを `**options` に深くマージし、同じ名前はこちらが優先する
  - 書いていない接続先には何も足さない
- **選ばれなかった接続先の分も、`Client` を作るときに検査する**（作者の判断）
  - 知らない接続先の名前は `ConfigurationError`、値が dict でなければ `TypeError`
  - オプション名と `headers` の中身は、その接続先のアダプターで `check_options` する。打ち間違いに、その接続先へ載せ替えてから気づかないようにするため
- **`with_model()` は、別の接続先へも `options_by_provider=` を引き継ぐ**（作者の判断）
  - 接続先ごとに分けて書いてあり、切り替え先の分しか使わないので、元の接続先の `headers=` や設定は漏れない
  - 並べて比べるときや、会話の途中で切り替えるときに、渡し直さなくてよい
- `**options` に、選んだ接続先が知らないオプションを渡したときの `ConfigurationError` には、`options_by_provider=` で書き分けられることを書く

## 却下した案

| 案 | 却下した理由 |
|---|---|
| 構造化出力を共通パラメータにする | 4社とも JSON Schema での出力に対応しているが、一次情報の確認と4アダプターの実装が要る。作者の判断で、[スコープ](../product-specs/scope.md) の「やらないこと」のままにした。2026-11-18 に見直し、足すことにした（[計画](../exec-plans/completed/2026-11-23-model-layer-features.md)） |
| `extra=` のキーがプロバイダー名なら振り分ける | `extra` は本体にそのままマージするので、本体のキーとプロバイダー名の区別がつかない |
| キーをアダプター名（`openai_chat` など）にする | 利用者はアダプター名を目にしない。モデル文字列に書く名前の方が迷わない |
| `with_model()` で何も引き継がない | 同じ社の別モデルへの切り替え（よくある使い方）で、毎回キーを渡し直すことになる |
| `**options` で、ほかのアダプターのオプション名も受け付け、選んだ接続先が読まないものは使わない（2026-11-28） | 入れ子がなく書きやすいが、同じアダプターを使う接続先（`openai-chat`・`deepseek`・`ollama` など）の間で書き分けられない。どの接続先向けかが、書いたコードから分からない |
| `options_by_provider=` の、選んだ接続先の分だけを検査する（2026-11-28） | 打ち間違いに、その接続先へ載せ替えてから初めて気づく |

## 影響

- `google:` と `gemini:` は別の名前として扱う。`google:` で指定するなら `"google"` のキーに書く
- 今まで黙って無視していたオプションが `ConfigurationError` になる。まだ公開していないので、壊れる利用者のコードはない
- [切り替えの精査](../exec-plans/completed/2026-11-15-switching-review.md) の S5・S7・S8・S13
