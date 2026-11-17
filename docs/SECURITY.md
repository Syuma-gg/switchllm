# セキュリティ

## APIキー

- 環境変数（`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` など）か `Client(api_key=...)` で渡す。コードやリポジトリ内のファイルには書かない。`.env` は `.gitignore` 済み
- キーは HTTP ヘッダー（`Authorization` / `x-api-key` / `x-goog-api-key`）にだけ載せる。URL のクエリには載せない
- `Client.with_model()` は、別のプロバイダーへ切り替えるときは `api_key`・`base_url`・プロバイダー別のオプション（`headers=` など）を引き継がない。別のサーバーにキーや認証ヘッダーを誤って送らないため。同じプロバイダーの別モデルへは、送り先が変わらないので引き継ぐ（[判断](design-docs/provider-specific-params.md)）
- ライブラリはログを出さず、外部への送信（テレメトリ）もしない

## ツールの実行

- `run_tool_calls` / `Tool.execute` は、**モデルが決めた引数で利用者の関数を実行する**。ファイル操作・コマンド実行・外部への送信などを行う関数では、引数を検証してから使うこと
- 関数が例外を出すと、`例外の型: メッセージ` の文字列がそのままモデルに返る。メッセージに秘密情報が含まれていると、それがプロバイダーに送られる

## 例外に含まれる情報

- `APIError.body` には、プロバイダーが返したエラー本文がそのまま入る。ログに出すときは中身に注意する

## 開発時

- `Message.from_dict()` で復元した生データ（`provider_state`）は、そのままプロバイダーに送られる。読み込むのは自分で保存した履歴だけにする（利用者が書き換えられる場所から読み込まない）
- `e2e/` の E2E テストは、`--models` を渡すと実際の API を呼ぶので、料金が発生する。キーは環境変数から読み、テストの出力やファイルには残さない（無効なキーのテストは、固定の偽のキーを使う）
- `--record` で残す記録（`e2e/recordings/`）には、リクエストのヘッダーと URL の `key` を入れず、キーの環境変数の値を `<redacted>` に置き換える。`tests/test_recording.py` が、記録にキーの形をした文字列がないことを毎回確かめる。記録はリポジトリに入れるので、コミットする前に差分を見る（[テストの方針](testing.md)）
- テストはネットワークを使わない（`tests/conftest.py` の `FakeAPI`）

## 脆弱性の報告

脆弱性は公開の Issue に書かず、GitHub の Private vulnerability reporting で知らせる。

- リポジトリの「Security」タブの「Report a vulnerability」から送る（https://github.com/Syuma-gg/switchllm/security/advisories/new ）。報告は作者にしか見えない
- 書いてほしいこと: 影響を受ける版、再現の手順、想定される影響
- 修正するのは最新の版だけ。0.x の間は、古い版にさかのぼって修正しない
