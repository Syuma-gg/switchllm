# セキュリティ

## APIキー

- 環境変数（`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` など）か `Client(api_key=...)` で渡す。コードやリポジトリ内のファイルには書かない。`.env` は `.gitignore` 済み
- キーは HTTP ヘッダー（`Authorization` / `x-api-key` / `x-goog-api-key`）にだけ載せる。URL のクエリには載せない
- `Client.with_model()` は `api_key` と `base_url` を引き継がない。別のプロバイダーにキーを誤って送らないため
- ライブラリはログを出さず、外部への送信（テレメトリ）もしない

## ツールの実行

- `run_tool_calls` / `Tool.execute` は、**モデルが決めた引数で利用者の関数を実行する**。ファイル操作・コマンド実行・外部への送信などを行う関数では、引数を検証してから使うこと
- 関数が例外を出すと、`例外の型: メッセージ` の文字列がそのままモデルに返る。メッセージに秘密情報が含まれていると、それがプロバイダーに送られる

## 例外に含まれる情報

- `APIError.body` には、プロバイダーが返したエラー本文がそのまま入る。ログに出すときは中身に注意する

## 開発時

- `scripts/smoke_test.py` は実際の API を呼ぶので、料金が発生する
- テストはネットワークを使わない（`tests/conftest.py` の `FakeAPI`）

## 脆弱性の報告

公開するときに窓口を決めて、ここに書く（[v0.1 公開](exec-plans/active/v0.1-release.md)）。
