# プロバイダー固有の指定は、各社の分を並べて渡す

- 状態: 採用（2026-11-15）
- 検証: —（各社の API の形式には関わらない）
- コード: `client.py`（`_pick_extra`、`Client.with_model`）、`providers/base.py`（`Provider.option_names`、`check_options`）

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
- 意味の共通化（推論の強さを `low` / `medium` / `high` にそろえる、など）はしない。指定方法がモデルの世代ごとに変わり、4アダプターで追い続けるのは重い（方針 1「薄く保つ」）。**2026-11-18 に見直した**: 推論の強さと構造化出力は共通化する（[計画](../exec-plans/active/model-layer-features.md)）。`extra=` / `extra_by_provider=` は、共通化しないものの逃げ道として残す
- アダプターごとに、受け付けるオプション名を `Provider.option_names` に決め、それ以外は `ConfigurationError` にする。`register_provider()` も登録の時点で検査する。一覧は [public-api.md](../product-specs/public-api.md) にあり、ソースが読むオプションとの一致は `tests/test_client.py`、文書との一致は `tests/test_docs.py` が検査する
- `with_model()` は、同じプロバイダーなら api_key / base_url / オプションを引き継ぎ、別のプロバイダーへは timeout / max_retries / retry_backoff だけを引き継ぐ

## 却下した案

| 案 | 却下した理由 |
|---|---|
| 構造化出力を共通パラメータにする | 4社とも JSON Schema での出力に対応しているが、一次情報の確認と4アダプターの実装が要る。作者の判断で、[スコープ](../product-specs/scope.md) の「やらないこと」のままにした。2026-11-18 に見直し、足すことにした（[計画](../exec-plans/active/model-layer-features.md)） |
| `extra=` のキーがプロバイダー名なら振り分ける | `extra` は本体にそのままマージするので、本体のキーとプロバイダー名の区別がつかない |
| キーをアダプター名（`openai_chat` など）にする | 利用者はアダプター名を目にしない。モデル文字列に書く名前の方が迷わない |
| `with_model()` で何も引き継がない | 同じ社の別モデルへの切り替え（よくある使い方）で、毎回キーを渡し直すことになる |

## 影響

- `google:` と `gemini:` は別の名前として扱う。`google:` で指定するなら `"google"` のキーに書く
- 今まで黙って無視していたオプションが `ConfigurationError` になる。まだ公開していないので、壊れる利用者のコードはない
- [切り替えの精査](../exec-plans/completed/2026-11-15-switching-review.md) の S5・S7・S8・S13
