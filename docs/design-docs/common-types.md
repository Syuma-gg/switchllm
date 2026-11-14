# 出力は OpenAI 形式ではなく独自の共通型

- 状態: 採用
- コード: `types.py`

## 背景

LiteLLM や any-llm は、入出力を OpenAI の形式に寄せている。利用者には馴染みがあるが、OpenAI の形式には署名や推論アイテムのようなプロバイダー固有の情報を入れる場所がない。

## 決定

`Message` / `ToolCall` / `Response` / `Usage` / `StreamEvent` を独自に定義し、プロバイダー固有の生データは `ProviderState` として持たせる（[生データの再送](provider-state-resend.md)）。正規化した値の意味は [公開 API](../product-specs/public-api.md) で定義する。

## 却下した案

- **OpenAI の形式に寄せる**: 既存ライブラリと同じになり、署名を保持できない

## 影響

- 利用者は unillm の型を覚える必要がある。そのぶん型の数は最小に保つ
