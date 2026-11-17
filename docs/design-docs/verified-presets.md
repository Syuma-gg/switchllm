# プリセットは base_url を確認したものだけ

- 状態: 採用（2026-11-09）
- 検証: 公式ドキュメントで確認（疎通は U12、[実 API 検証](../exec-plans/active/real-api-verification.md)）
- コード: `providers/__init__.py`（`PRESETS`）
- 一覧: [公開 API のプリセット表](../product-specs/public-api.md)

## 背景

プリセットは「この名前を書けば動く」という約束で、URL や認証が変わったら追従する責任を負う。以前は OpenRouter / Groq / DeepSeek / xAI / Mistral / Together を含む13個を持っていたが、互換サーバーの base_url は第三者のまとめで照合しただけで、各社の公式ドキュメントでは確認していなかった。

## 決定

- 残すのは、ネイティブ3社（`openai` / `anthropic` / `gemini`、別名の `google`）、`openai-chat`、ローカル実行の `ollama` / `lmstudio` の7個
- `ollama` / `lmstudio` の base_url は、2026-11-09 にそれぞれの公式ドキュメントで確認した（[出典](../references/openai-chat-compatible.md)）
- それ以外の互換 API は、利用者が `register_provider()` で足す
- 2026-11-17 に `deepseek` を足した（8個）。作者が DeepSeek に対応すると決め、アダプターが DeepSeek の仕様（ツールを使う会話での `reasoning_content` の送り返し）に追従することになった。追従の負担をすでに引き受けているので、プリセットで「この名前で動く」と約束する。base_url とキーの環境変数は公式文書で確認した（[出典](../references/openai-chat-compatible.md)）。疎通は[実 API 検証](../exec-plans/active/real-api-verification.md)で確かめる

## 却下した案

- **全部残す**: 確認していない URL を約束することになる
- **全部の URL を確認して残す**: 確認はできても、各社の仕様変更（認証、対応しないパラメータなど）に追従し続ける負担が残る

## 影響

- プリセットを足すときは、公式ドキュメントで base_url を確認して出典を残す（[ARCHITECTURE.md](../../ARCHITECTURE.md) の「よくある変更」）
- README で `register_provider()` の例を示す
