# クラウドの経路（API キーで呼ぶもの）

各社のモデルを、クラウドが API キーで呼べるようにしている経路。どの経路も、送る本文は既存のアダプターと同じ形式なので、URL・キーのヘッダー・送る前に止めるものだけを変える（[判断](../design-docs/cloud-api-keys.md)）。クラウドの認証（SigV4・OAuth・Entra ID）には対応しない（[scope](../product-specs/scope.md)）。

各形式の細かい対応は、アダプターの文書（[OpenAI Responses](openai-responses.md)・[Chat Completions 互換](openai-chat-compatible.md)・[Anthropic Messages](anthropic-messages.md)・[Gemini generateContent](gemini-generate-content.md)）が正で、ここにはクラウドで違うところだけを書く。文書に書いていないことは、[実 API 検証](../exec-plans/active/real-api-verification.md) の U40〜U44 で確かめる。

## 一覧

| プリセット | 経路 | アダプター | URL | キーのヘッダー | キーの環境変数 | URL を組み立てる環境変数 |
|---|---|---|---|---|---|---|
| `azure` | Azure OpenAI（v1）の Responses API | `openai` | `https://{resource}.openai.azure.com/openai/v1/responses` | `api-key` | `AZURE_OPENAI_API_KEY` | `AZURE_OPENAI_ENDPOINT` |
| `azure-chat` | Azure OpenAI（v1）の Chat Completions | `openai_chat` | `https://{resource}.openai.azure.com/openai/v1/chat/completions` | `api-key` | `AZURE_OPENAI_API_KEY` | `AZURE_OPENAI_ENDPOINT` |
| `foundry` | Claude in Microsoft Foundry | `anthropic` | `https://{resource}.services.ai.azure.com/anthropic/v1/messages` | `x-api-key` | `ANTHROPIC_FOUNDRY_API_KEY` | `ANTHROPIC_FOUNDRY_RESOURCE` か `ANTHROPIC_FOUNDRY_BASE_URL` |
| `bedrock` | Claude in Amazon Bedrock（bedrock-mantle の Messages API） | `anthropic` | `https://bedrock-mantle.{region}.api.aws/anthropic/v1/messages` | `x-api-key` | `AWS_BEARER_TOKEN_BEDROCK` | `AWS_REGION`、なければ `AWS_DEFAULT_REGION` |
| `vertex` | Vertex AI express mode（Gemini） | `gemini` | `https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent` | `x-goog-api-key` | `VERTEX_API_KEY` | なし（URL は固定） |

- 環境変数の名前は、各社の公式 SDK が読む名前に合わせた。`VERTEX_API_KEY` だけは、Google の文書が名前を決めていないので、作者が決めた（`gemini` のプリセットが読む `GOOGLE_API_KEY` と分けるため）
- `base_url=` を渡せば、URL を組み立てる環境変数は読まない

## Azure OpenAI（`azure`・`azure-chat`）

- URL: v1 の API は `https://{resource}.openai.azure.com/openai/v1/`。`https://{resource}.services.ai.azure.com/openai/v1/` も受け付ける。日付の付いた `api-version` は要らない
- `AZURE_OPENAI_ENDPOINT` は、文書の例ではパスのない `https://{resource}.openai.azure.com`。switchllm はこれに `/openai/v1` を足す。`/openai/v1` まで書いた値もそのまま使う
- キー: 生の HTTP の例はどれも `api-key` ヘッダー。`Authorization: Bearer <キー>` の例は Foundry Models の文書に1つあるだけで、ほかの文書の Bearer は Entra ID のトークンなので、`api-key` で送る
- `model` には deployment 名を書く。モデル名ではない
- Responses（`azure`）
  - `store: false` でも、reasoning アイテムに暗号化した推論が既定で付く。output アイテムをそのまま次の input に戻す（OpenAI と同じ）
  - 推論の要約（`reasoning.summary`）、構造化出力（`text.format`）、URL の画像（`input_image`）、base64 の PDF（`input_file`）を受け付ける
  - `reasoning.effort` は `none`〜`max`。受け付ける段階はモデルによる
- Chat Completions（`azure-chat`）
  - 推論モデルは `max_completion_tokens` だけを受け付け、`max_tokens` を受け付けない。そのため、プリセットの `max_tokens_field` を `max_completion_tokens` にした
  - 推論の要約は Responses の例しかない。Chat Completions は推論の本文を返さないので、`reasoning_summary=True` は送る前に止める（OpenAI 本家の Chat Completions と同じ。プリセットのオプション `reasoning_summary: False`）
  - 構造化出力（`response_format` の `json_schema`）を受け付ける。GPT-5.6 以降は、ツールと `reasoning_effort`（`none` 以外）を一緒に送ると断る（文書のエラーの文言。API のエラーをそのまま返す）
- 文書に書いていないこと（U40・U41）: エラーの本文の形、`stream_options.include_usage`、Chat Completions の `stop`・画像の URL・PDF

## Claude in Microsoft Foundry（`foundry`）

- URL: `https://{resource}.services.ai.azure.com/anthropic/v1/*`。SDK の base URL は `https://{resource}.services.ai.azure.com/anthropic/`
- 環境変数: SDK は `ANTHROPIC_FOUNDRY_RESOURCE` と `ANTHROPIC_FOUNDRY_BASE_URL` を読み、2つは「mutually exclusive」。両方あれば switchllm は `ConfigurationError` にする
- キー: `api-key` か `x-api-key`。switchllm は Anthropic と同じ `x-api-key` で送る。Claude Mythos のモデルは Entra ID だけで、API キーでは呼べない
- 本文は Claude API と同じ。`model` は deployment 名（既定ではモデル ID と同じ。例: `claude-sonnet-5-5`）
- ストリーミングは SSE（Microsoft Learn）
- 画像と PDF は base64 も URL も受け付ける（Azure で動く deployment。Microsoft Learn）
- 受け付けないもの: Files API、コード実行、新しい版の web 検索・web fetch、Agent Skills、programmatic tool calling、Message Batches、Models API。switchllm はどれも使わない
- 版: Anthropic の文書の例は `anthropic-version: 2026-10-21`、Microsoft Learn の例は `2023-06-01`。どの版を受け付けるかは書いていない（U42）
- 文書に書いていないこと（U42）: エラーの本文の形、テキストのファイル（`text` の source）、`thinking.display`

## Claude in Amazon Bedrock（`bedrock`）

- URL: `https://bedrock-mantle.{region}.api.aws/anthropic/v1/messages`。リージョンは、SDK と同じく `AWS_REGION`、なければ `AWS_DEFAULT_REGION` から読む
- キー（Amazon Bedrock API key）: `x-api-key` か `Authorization: Bearer`。switchllm は Anthropic と同じ `x-api-key` で送る
  - 短期のキーは最長 12 時間で切れ、作ったリージョンでしか使えない。長期のキーは期限まで同じ文字列で使える
  - キーを作り直す（token generator を呼ぶ）のは認証の側の仕事なので、switchllm はしない。長く動くアプリは、作り直したキーを `api_key=` で渡す
- 本文と SSE は Claude API と同じ（「the same request body shape as the Claude API」）。`model` は本文に入れ、`anthropic.` が付く（例: `anthropic.claude-sonnet-5-5`）
- `anthropic-beta` ヘッダーを受け付ける（Claude Platform on AWS の比較表）
- 受け付けないもの: **URL の画像と文書**、Files API、サーバーのツール（コード実行・web 検索・web fetch・advisor）、Agent Skills、MCP connector、programmatic tool calling、Message Batches、Models API。URL の画像は、プリセットのオプション `image_url: False` で送る前に止める
- 版: 例は `anthropic-version: 2026-10-21`。受け付ける版の一覧はない
- 旧来の `bedrock-runtime`（InvokeModel・Converse）は、モデルを URL に入れ、本文に `anthropic_version: "bedrock-2023-05-31"` を入れ、ストリーミングが AWS の event stream なので、対応しない
- 文書に書いていないこと（U43）: エラーの本文の形、base64 の PDF とテキストのファイル、effort・adaptive thinking・`thinking.display`、同じキーの別のモデルへ thinking ブロックを送り返せるか

## Vertex AI express mode（`vertex`）

- URL: `https://aiplatform.googleapis.com/v1/{model}:generateContent`（`{model}` は `publishers/google/models/{モデル ID}`）。プロジェクトとリージョンが入らない。ストリーミングは `:streamGenerateContent`
  - `v1beta1` のパスもある。v1 は GA なので v1 にした。関数呼び出しの `id` は v1beta1 の Gemini 3 以降だけなので、v1 では返らず、switchllm が仮 ID を振る（U6 と同じ扱い）
- キー: `x-goog-api-key` ヘッダー。ヘッダーを付けられないときだけ `?key=`
- 使える人: 個人の @gmail.com アカウント。Workspace のアカウントは、通常の Google Cloud の API キーか ADC を使う（プロジェクトを含む URL。API キーで呼べるかは文書にないので対応しない）
- 使えるモデル: Gemini だけ（表にあるのは `gemini-2.5-flash`・`gemini-2.5-pro`・`gemini-2.5-flash-lite`・`gemini-3-flash-preview`・`gemini-3.1-pro-preview` など）。表とクイックスタートの例（`gemini-3.5-flash`）が食い違う。プレビューの扱い
- 本文: Gemini API と同じ名前のフィールドを持つ
  - `generationConfig`: `maxOutputTokens`・`stopSequences`（5個まで）・`temperature`（3.6 Flash 以降は無視）・`responseMimeType`・`responseJsonSchema`（どちらも非推奨とされ、`responseFormat` を勧める）・`thinkingConfig`（`thinkingLevel`・`includeThoughts`）
  - `thinkingLevel` の値は `LOW`・`MEDIUM`・`HIGH`・`MINIMAL`・`XHIGH`（大文字）。switchllm は Gemini API と同じく小文字で送る
  - `parts`: `inlineData`・`fileData`（`gs://`、公開の HTTPS の URL（15 MiB まで）、YouTube）・`thought`・`thoughtSignature`・`functionCall`・`functionResponse`
  - 関数の宣言は `parameters`（OpenAPI のサブセット）。`parametersJsonSchema` は文書にない
  - `toolConfig.functionCallingConfig.mode` は `AUTO`・`NONE`・`ANY`。`allowedFunctionNames` は `ANY` のときだけ
  - `systemInstruction` は text だけ
- 応答: `candidates[].content.parts`・`finishReason`・`usageMetadata`（`promptTokenCount`・`candidatesTokenCount`・`totalTokenCount`。`thoughtsTokenCount` は SDK の例にだけある）
- 推論情報: 前の応答の thought signature を次のリクエストに戻す（Gemini API と同じ）。ダミーの署名 `skip_thought_signature_validator` は文書にない
- 文書に書いていないこと（U44）: `?alt=sse`（SSE）で返すか、エラーの本文の形、小文字の `thinkingLevel`、express mode で HTTPS の URL の画像を取りに行くか、`parametersJsonSchema`、ダミーの署名、`thoughtsTokenCount`

## 対応しない経路

| 経路 | 理由 |
|---|---|
| Vertex AI の Claude | 認証が Google Cloud のアクセストークン（`gcloud auth print-access-token`）だけ。API キーの記述がない |
| Bedrock の InvokeModel・Converse | モデルが URL に入り、本文の版の書き方が違い、ストリーミングが AWS の event stream の形式 |
| Bedrock の Chat Completions・Responses（OpenAI 互換） | Claude を受け付けない（Sonnet 4.6 のモデルカード）。gpt-oss などは対応できるが、2026-11-28 に作者が選んだ4つに入っていない |
| Claude Platform on AWS | Anthropic が運用する経路で、API キー（Bearer）と `anthropic-workspace-id` ヘッダーで呼べる。作者が選んだ4つに入っていない |

## 出典

すべて 2026-11-28 に確かめた。

### Anthropic

- [Claude in Microsoft Foundry](https://platform.claude.com/docs/en/build-with-claude/claude-in-microsoft-foundry): URL `https://{resource}.services.ai.azure.com/anthropic/v1/*`、「Use either the `api-key` or `x-api-key` header」、`ANTHROPIC_FOUNDRY_API_KEY`・`ANTHROPIC_FOUNDRY_RESOURCE`・`ANTHROPIC_FOUNDRY_BASE_URL`（resource と base URL は mutually exclusive）、deployment 名が `model` になる、Azure で動く deployment が受け付けない機能（Files API など）、Mythos は Entra ID だけ
- [Claude in Amazon Bedrock](https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock): URL `https://bedrock-mantle.{region}.api.aws/anthropic/v1/messages`、「uses standard SSE streaming and the same request body shape as the Claude API」、トークンは `x-api-key` か `Authorization: Bearer`、`AWS_BEARER_TOKEN_BEDROCK`、リージョンは `AWS_REGION` か `AWS_DEFAULT_REGION`、受け付けない機能に「URL sources for images and documents」、モデル ID（`anthropic.claude-opus-5-5` など）、リージョンの一覧
- [Claude on Amazon Bedrock (legacy)](https://platform.claude.com/docs/en/build-with-claude/claude-on-amazon-bedrock-legacy): InvokeModel・Converse は AWS の event stream、本文に `"anthropic_version": "bedrock-2023-05-31"`
- [Claude Models on Google Cloud (Google managed)](https://platform.claude.com/docs/en/build-with-claude/claude-models-on-google-cloud/google-managed): `rawPredict`、本文に `anthropic_version`、認証は `gcloud auth print-access-token`
- [Claude Platform on AWS](https://platform.claude.com/docs/en/build-with-claude/claude-platform-on-aws): `https://aws-external-anthropic.{region}.api.aws`、`anthropic-workspace-id` ヘッダー、`ANTHROPIC_AWS_API_KEY`、Bedrock が `anthropic-beta` ヘッダーで beta を受け付けるという比較表

### Microsoft

- [Azure OpenAI v1 API](https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle): 「`base_url` accepts both `https://YOUR-RESOURCE-NAME.openai.azure.com/openai/v1/` and `https://YOUR-RESOURCE-NAME.services.ai.azure.com/openai/v1/` formats」、「`api-version` is no longer a required parameter with the v1 GA API」、curl の例は `-H "api-key: $AZURE_OPENAI_API_KEY"`
- [Endpoints for Microsoft Foundry Models](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/endpoints): 「pass the deployment name in the `model` field」、Bearer にキーを入れる curl の例
- [Use the Azure OpenAI Responses API](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/responses): `AZURE_OPENAI_ENDPOINT`（例はパスなし）・`AZURE_OPENAI_API_KEY`、「Stateless responses return encrypted content by default」
- [Reasoning models](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/reasoning): 推論モデルは Chat Completions で `max_completion_tokens` だけ、`reasoning.summary` は Responses の例だけ、effort の段階、GPT-5.6 のツールと `reasoning_effort` のエラーの文言
- [Structured outputs](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/structured-outputs): Chat Completions は `response_format`、Responses は `text.format`
- [Claude in Foundry Models](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/claude-models): SSE、「Hosted on Azure deployments only accept base64 encoded or URL-based images」、PDF は base64 か URL、effort と thinking の対応表
- [Use Claude models in Foundry](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-claude): REST の例は `anthropic-version: 2023-06-01`

### AWS

- [Use an Amazon Bedrock API key](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys-use.html): `AWS_BEARER_TOKEN_BEDROCK`、`Authorization: Bearer`、API キーで呼べない操作の一覧
- [Amazon Bedrock API keys](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html): 短期のキーは最長 12 時間で、作ったリージョンだけ。長期のキーは期限まで
- [Chat Completions API](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-chat-completions.html): `bedrock-runtime` と `bedrock-mantle` の OpenAI 互換の URL
- [Claude Sonnet 4.6 のモデルカード](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html): Chat Completions は対応しない

### Google

- [Express mode overview](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/start/express-mode/overview): `https://aiplatform.googleapis.com/v1/publishers/google/models/MODEL_ID:generateContent`、「Pass your API key in the `x-goog-api-key` HTTP header」、@gmail.com のアカウント、使えるモデルの表と「Only the models listed in the preceding table are supported in express mode」
- [Express mode API reference](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/start/express-mode/vertex-ai-express-mode-api-reference): v1 と v1beta1 の generateContent・streamGenerateContent、エンドポイントに projects・locations が入らない
- [Express mode generateContent](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/reference/express-mode/rest/v1/publishers.models/generateContent): 本文のフィールド、`systemInstruction` は「only text should be used in parts」
- [Inference reference](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/model-reference/inference): Part・FileData（`gs://`、公開の HTTP(S) の URL は 15 MiB まで）・`thoughtSignature`、`finishReason`、`usageMetadata`
- [GenerationConfig](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/reference/rest/v1/GenerationConfig): `responseMimeType`・`responseJsonSchema`（非推奨、`responseFormat` を勧める）、`thinkingLevel` の値、`stopSequences` は5個まで
- [Function calling reference](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/models/function-calling): FunctionDeclaration は `parameters`、mode は `AUTO`・`NONE`・`ANY`、`id` は v1beta1 の Gemini 3 以降
- [Thinking](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/thinking.md.txt): thought signature を次のリクエストに戻す
- [Get a Google Cloud API key](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/start/api-keys): express mode のキーと、通常の Google Cloud の API キー
