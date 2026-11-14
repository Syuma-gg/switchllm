# @tool の自動変換は対応する型を固定する

- 状態: 採用（2026-11-09）
- コード: `tools.py`（`SUPPORTED_TYPES`、`_schema_for`、`tool(parameters=...)`）
- テスト: `tests/test_tools.py`

## 背景

ツール呼び出しには、関数の引数を JSON Schema で書いた定義が必要になる。`@tool` は型ヒントと docstring からこれを自動で作る便利機能で、ツールを使うアプリ（LLM を組み込むアプリではほぼ必須）の使い心地を大きく左右する。

一方、型ヒントからの変換は対応要望が際限なく増える領域（Pydantic モデル、dataclass、ネストした型…）で、軽量の方針と衝突する。以前の実装は、対応していない型を**黙って文字列扱い**にしていたため、間違った定義をモデルに送っても気付けなかった。

## 決定

- `@tool` と `run_tool_calls` は残す（使い心地を下げない）
- 自動変換する型を `SUPPORTED_TYPES` に固定する: `str` / `int` / `float` / `bool` / `list[T]`（`tuple`・`set` も可）/ `dict` / `Literal` / `Enum` / `Optional`・`Union` / `Annotated[T, "説明"]`。型ヒントのない引数は `str` として扱う
- 対応外の型は、デコレートした時点で `TypeError` にする。メッセージには引数名と対処法（`parameters=`）を入れる
- `@tool(parameters={...})` で JSON Schema を直接渡せるようにする。このときは型ヒントから推論しない。Pydantic を使う人は `parameters=Model.model_json_schema()` と書ける。ただし Pydantic の出力には `$defs` などが含まれ、Gemini の `parameters` では扱えない可能性がある（U4）

## 却下した案

- **`@tool` と `run_tool_calls` を削る**: 最も軽いが、ツールを使うアプリでの使い心地が大きく下がる
- **Pydantic や dataclass に対応する**: 依存か分岐が増え、際限がなくなる

## 影響

- 対応する型を増やしてほしいという要望には、`parameters=` を案内する
- 以前は文字列扱いで通っていた型（`Any`、dataclass など）がエラーになる。公開前なので互換性の問題はない
