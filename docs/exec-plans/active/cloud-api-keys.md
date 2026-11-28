# クラウドの API キーに対応する

## 目的

クラウドが提供する、API キーで呼べる4つの経路を、モデル文字列とキーの環境変数だけで呼べるプリセットにする。URL に入るリソース名やリージョンは、環境変数から組み立てる。

4つのプリセットがテストと文書つきでそろい、確かめる前提が [実 API 検証](real-api-verification.md) に入ったら完了。実 API での確認は、その計画の `--record` の1回にまとめる。

## 背景

- 2026-11-28: 作者が決めたこと
  - クラウドの API キーには対応する。クラウドの認証（SigV4・OAuth・Entra ID）には対応しない
  - 対応する経路は、下の4つすべて
  - URL のリソース名・リージョンは、環境変数から組み立てる。`base_url=` を渡せばそちらを使う
  - v0.1 の実 API の E2E の前にやる（記録を1回で済ませるため）
- [scope.md](../../product-specs/scope.md) の「やらないこと」の「Bedrock / Vertex AI / Azure の認証」のうち、API キーで呼べる経路を「やること」に移す。認証は「やらないこと」に残す

## 調べたこと（2026-11-28）

出典は `docs/references/` に書く（手順の2）。各社の文書が書いていないことは、E2E の前提にする。

| 経路 | URL | キーのヘッダー | 使うアダプター | 分かった違い |
|---|---|---|---|---|
| Azure OpenAI（v1） | `https://{resource}.openai.azure.com/openai/v1/` | `api-key`（Bearer にキーを入れる例は1つの文書にしかない） | `openai`・`openai_chat` | `model` は deployment 名。Chat Completions の推論モデルは `max_completion_tokens` だけ。推論の要約は Responses だけ |
| Claude in Microsoft Foundry | `https://{resource}.services.ai.azure.com/anthropic/v1/messages` | `api-key` か `x-api-key` | `anthropic` | `model` は deployment 名。SSE。URL の画像・PDF も受け付ける。版は Anthropic の文書が `2026-10-21`、Microsoft の文書が `2023-06-01` |
| Claude in Amazon Bedrock（mantle） | `https://bedrock-mantle.{region}.api.aws/anthropic/v1/messages` | `x-api-key` か Bearer | `anthropic` | 本文と SSE は Claude API と同じ。モデル ID に `anthropic.` が付く。URL の画像と文書を受け付けない |
| Vertex AI express mode | `https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent` | `x-goog-api-key` | `gemini` | Gemini のモデルだけ。個人の @gmail.com アカウントだけ。`?alt=sse` と、エラーの形は文書にない。`parametersJsonSchema` は文書にない |

対応しないもの:

- Vertex AI の Claude: OAuth のトークンだけ
- Bedrock の InvokeModel: ストリーミングが AWS の event stream の形式
- Claude Platform on AWS: Anthropic が運用する別の経路。API キー（Bearer）と `anthropic-workspace-id` ヘッダーで呼べる。作者に聞いた4つに入っていない

## 手順

- [x] プリセットの名前と、Vertex のキーの環境変数を作者に決めてもらう（2026-11-28）
- [x] `docs/references/` に、4つの経路の仕様と出典を書く（[クラウドの経路](../../references/cloud-endpoints.md)）
- [x] 設計文書を書く（[判断](../../design-docs/cloud-api-keys.md)）
- [x] テストを先に書き、今のコードで失敗することを確かめる（`tests/test_cloud_presets.py` の 122 件と、対応表のテスト。どれも失敗した）
- [x] 実装する
- [x] 新しいテストが、わざと壊した実装で失敗することを確かめる（17 通りすべて）
- [x] 文書を直す: README（英日）、public-api（プリセット・送れるもの・オプションの表）、scope、CHANGELOG、ARCHITECTURE、SECURITY、testing、e2e-tests、QUALITY_SCORE
- [x] E2E をクラウドのモデルでも回せるようにし、前提の U40〜U44 を `e2e_support.SCOPE` と [実 API 検証](real-api-verification.md) に足す
- [ ] 作者に、確かめる経路のアカウントとキーを用意してもらう（E2E は [実 API 検証](real-api-verification.md) でまとめて回す）

## 進捗ログ

- 2026-11-28: 4つの経路の公式の文書を読んだ（Anthropic・AWS・Microsoft Learn・Google Cloud）。上の「調べたこと」にまとめた
- 2026-11-28: 実装した。全体 1558 件、品質検査（ruff・pytest・カバレッジ 99.15%）が通った
  - `ProviderPreset` に、URL を環境変数から組み立てる `resolve_base_url` と、キーのヘッダーを替える `auth` を足した（どちらも内部のもの）
  - オプションを2つ足した: `anthropic` の `image_url`（`bedrock` は `False`）、`openai_chat` の `reasoning_summary`（`azure-chat` は `False`）
  - E2E: 記録にはクラウドの URL を決まった値（`e2e_support.REPLAY_ENV`）で残し、再生ではその値を環境変数に入れる。クラウドの経路の結果は、その経路の U 番号にだけ数える（`CLOUD_ROUTES`。`bedrock` の結果が U25 などに数えられていたのを、テストを書いて直した）
  - わざと壊した実装で確かめるとき、壊したファイルと元のファイルが同じ大きさで同じ秒に書き戻されると、Python が古い `.pyc` を使い続け、元に戻したあとのテストも失敗した。壊して回すときは `python -B` を使う

## 判断ログ

- 2026-11-28: 上の「背景」の4つを作者が決めた
- 2026-11-28: プリセットの名前は `azure`・`azure-chat`・`foundry`・`bedrock`・`vertex`、Vertex のキーは `VERTEX_API_KEY`（作者の判断。会社名を付けた名前と `GOOGLE_API_KEY` は選ばなかった）
- 2026-11-28: Azure へはキーを `api-key` ヘッダーで送る。Vertex は v1 のパスを使う。Foundry の2つの環境変数が両方あればエラーにする（理由は [判断](../../design-docs/cloud-api-keys.md)）
