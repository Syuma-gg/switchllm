# SDK を使わず httpx で直接呼ぶ

- 状態: 採用
- コード: `_http.py`、各アダプター
- 検査: `tests/test_architecture.py`（ランタイム依存が httpx だけであること）

## 背景

各社の SDK を使えば仕様変更への追従は SDK 任せにできる。その代わり依存が増え、形式の差がコードから見えなくなる。

## 決定

- 各社の HTTP API を httpx で直接呼ぶ。ランタイム依存は httpx だけ
- テストは `httpx.MockTransport` を使い、ネットワークなしで行う

## 却下した案

- **各社の SDK を使う**（any-llm の方針）: 保守は楽になるが、依存が増える
- **依存ゼロにする（標準ライブラリの urllib）**（2026-11-09 に検討）: 送信処理とテストの仕組みを書き直すことになり、非同期化も難しくなる。httpx は openai や anthropic の SDK も使っている一般的な依存なので、利用者の負担は小さい

## 影響

- API 仕様の変更は自分で追う。確認した内容は [references](../references/index.md) に残す
