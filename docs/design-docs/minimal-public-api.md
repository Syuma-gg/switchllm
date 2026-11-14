# 公開 API を最小にする

- 状態: 採用（2026-11-09）
- 一覧: [公開 API](../product-specs/public-api.md)
- 検査: `tests/test_docs.py`（一覧と `unillm.__all__` の一致）

## 背景

公開した名前は、変えると利用者のコードを壊す。特にアダプターの基底クラス `Provider` を公開すると、`build_request` の引数を1つ変えるだけで破壊的変更になり、内部の設計を直せなくなる。

## 決定

- 公開 API は `unillm` 直下の `__all__` にある名前だけ。`unillm.providers` などのサブモジュールは内部実装
- 独自のアダプターを外から登録する `register_adapter()` は削除した。新しい形式の API は、このリポジトリにアダプターとして足す
- OpenAI 互換サーバーの追加は、公開 API の `register_provider()` で引き続きできる

## 却下した案

- **`register_adapter()` と `Provider` を公開したままにする**: 拡張性は高いが、内部の設計を変えるたびに破壊的変更になる

## 影響

- 内部の設計（`Provider` のメソッド、`ChatRequest`）は自由に変えられる
- 独自形式の API を自分で足したい利用者には対応しない
