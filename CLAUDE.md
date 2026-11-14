# unillm — Claude Code 向けガイド

OpenAI / Anthropic / Gemini / OpenAI互換サーバーを同じコードで呼べる Python ライブラリ。
`Client("provider:model")` の文字列（または環境変数 `UNILLM_MODEL`）だけでプロバイダーを切り替える。
経緯・未検証事項・TODO は `docs/HANDOFF.md`、利用者向け説明と設計根拠は `README.md` を参照。

## コマンド（Windows / PowerShell）

```powershell
pip install -e ".[dev]"
python -m pytest                     # 全テスト（ネットワーク不要・数秒）
python -m pytest tests/test_gemini.py -q
ruff check src tests --select F,E9,B --ignore B006   # ruff は任意
```

- **`scripts/smoke_test.py` は実 API を叩き、料金が発生する。実行前に必ずユーザーに確認すること。**
  例: `python scripts/smoke_test.py anthropic:claude-sonnet-5-5 openai:gpt-5.5 gemini:gemini-flash-latest`
- APIキーは環境変数（`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` など）。コードやファイルに書かない。

## アーキテクチャ

```
Client.chat()/stream()            src/unillm/client.py
  → ChatRequest（共通型）          providers/base.py
  → Provider.build_request()      providers/<adapter>.py  共通型 → 各社 HTTP リクエスト
  → _http.request_json / stream_sse   送信・リトライ・SSE 分解
  → Provider.parse_response() / parse_stream()  各社 JSON → Response / StreamEvent
```

- アダプターは4つ: `openai`（Responses API）/ `openai_chat`（Chat Completions 互換）/ `anthropic` / `gemini`
- モデル文字列の `provider:` 部分は `providers/__init__.py` の `PRESETS` で「アダプター + base_url + APIキー環境変数」に解決される
- 共通型は `types.py`（Message / ToolCall / Response / Usage / StreamEvent / ProviderState）

## 壊してはいけない不変条件

1. **生データ再送**: `parse_response` が返す assistant メッセージには必ず `ProviderState(self.key, self.model, raw)` を付ける。`build_request` は assistant メッセージに対して最初に `self.native_raw(m)` を確認し、あればそれを無加工で送る。これがないと Gemini の thoughtSignature、Anthropic の thinking 署名、OpenAI の reasoning アイテムが落ちてツール往復が 4xx になる。
   - 再送は「同じプロバイダーキー かつ 同じモデル」のときだけ。別モデルへは共通型から変換し直す。
   - 関連テスト: `*roundtrip*`, `test_parallel_calls_keep_signature_and_order`, `tests/test_cross_provider.py`
2. **parse_stream は最後に `done` を1回だけ yield** し、その Response は非ストリーミングと同じ内容にする。可能な限り「非ストリーミングと同じ形の dict を組み立てて `parse_response` を再利用」する（既存4アダプターはすべてこの方式）。
3. **未知の SSE イベントは無視する**（例外にしない）。プロバイダーは新イベントを追加する。
4. **共通パラメータを増やしたら4アダプターすべてで対応する**か、対応できないアダプターでは `UnsupportedFeatureError` を明示的に投げる。黙って無視しない。
5. **正規化した値の意味を変えない**:
   - `stop_reason`: `end` / `tool_use` / `max_tokens` / `content_filter` / `other`（ツール呼び出しがあれば常に `tool_use`）
   - `Usage.input_tokens` = キャッシュ読み書きを含む入力総数、`output_tokens` = 推論トークンを含む出力総数。生の内訳は `details`
   - `ToolCall.arguments` は常に dict。JSON として壊れていたら `{}` にして `raw_arguments` に原文を残す
6. **テストはネットワークを使わない**。`tests/conftest.py` の `FakeAPI`（httpx.MockTransport）で応答を積み、送られたリクエスト本体を検証する。
7. **API 仕様に関わる変更は一次情報（各社公式ドキュメント）で確認してから行う**。確認したら `docs/HANDOFF.md` の「未検証の前提」表を更新し、出典 URL を README の参考資料に足す。

## コード規約

- Python 3.10 以上。各ファイル先頭で `from __future__ import annotations`
- コメント・docstring・ドキュメントは日本語
- ランタイム依存は `httpx` のみの方針。増やす場合は理由を `docs/HANDOFF.md` に書く
- 公開 API を増やしたら `src/unillm/__init__.py` の import と `__all__` に追加

## よくある作業

- **OpenAI互換サーバーを足す**: `providers/__init__.py` の `PRESETS` に1行追加（base_url は公式ドキュメントで確認）
- **新しい形式の API を足す**: `providers/base.py` の `Provider` を継承し `build_request` / `parse_response` / `parse_stream` を実装 → `ADAPTERS` と `PRESETS` に登録 → `tests/test_<name>.py` を既存テストに倣って追加（リクエスト形状 / ツール往復 / ストリーミング / エラー）
