# クラウドの API キーで呼べる経路をプリセットにする

- 状態: 採用（2026-11-28）
- 検証: 一次情報で確認、実 API 未検証（[実 API 検証](../exec-plans/active/real-api-verification.md) の U40〜U44）
- コード: `providers/__init__.py`（`PRESETS` と URL の組み立て）、`providers/base.py`（キーのヘッダー）
- 仕様と出典: [クラウドの経路](../references/cloud-endpoints.md)
- 計画: [クラウドの API キー](../exec-plans/active/cloud-api-keys.md)

## 背景

- Azure・AWS・Google Cloud は、各社のモデルを自分のクラウドで提供している。会社によっては、各社に直接ではなく、クラウドとの契約で使うことが決まっている
- これまでは [scope](../product-specs/scope.md) で「Bedrock / Vertex AI / Azure の認証」を「やらないこと」にしていた。どのクラウドも、認証を SigV4・OAuth・Entra ID で行うと考えていたため
- 2026-11-28 に調べ直すと、4つの経路が API キーで呼べ、送る本文も既存のアダプターと同じ形式だった（[クラウドの経路](../references/cloud-endpoints.md)）
- `register_provider(adapter="anthropic", base_url=...)` を使えば、利用者が自分で登録することもできた。ただし、URL にリソース名やリージョンが入るのでモデルの文字列だけでは切り替えられない。Azure の `api-key` ヘッダーも送れず、経路ごとの制限（Bedrock は URL の画像を受け付けない）も、実際に呼ぶまで分からない
- 作者の判断（2026-11-28）: クラウドの API キーには対応する。認証には対応しない。4つの経路すべてに対応し、URL は環境変数から組み立てる

## 決定

### プリセット

名前は作者が選んだ（`openai` と `openai-chat` の並べ方に合わせる）。

| プリセット | アダプター | キーの環境変数 | URL |
|---|---|---|---|
| `azure` | `openai`（Responses） | `AZURE_OPENAI_API_KEY` | `AZURE_OPENAI_ENDPOINT` + `/openai/v1` |
| `azure-chat` | `openai_chat` | `AZURE_OPENAI_API_KEY` | 同上 |
| `foundry` | `anthropic` | `ANTHROPIC_FOUNDRY_API_KEY` | `https://{ANTHROPIC_FOUNDRY_RESOURCE}.services.ai.azure.com/anthropic`、または `ANTHROPIC_FOUNDRY_BASE_URL` |
| `bedrock` | `anthropic` | `AWS_BEARER_TOKEN_BEDROCK` | `https://bedrock-mantle.{AWS_REGION}.api.aws/anthropic`（`AWS_REGION` がなければ `AWS_DEFAULT_REGION`） |
| `vertex` | `gemini` | `VERTEX_API_KEY` | `https://aiplatform.googleapis.com/v1/publishers/google`（固定） |

- 環境変数の名前は、各社の公式 SDK と同じにする。SDK から移る人が、設定を変えずに使えるようにするため
- `VERTEX_API_KEY` は作者が決めた。Google の文書は名前を決めていない。`gemini` のプリセットが読む `GOOGLE_API_KEY` を使うと、AI Studio のキーを Vertex に送ってしまうことがある
- Vertex は v1 のパスを使う。v1 は GA で、express mode の文書が最初に示すパス。v1beta1 にしかない関数呼び出しの `id` は、なくても switchllm が仮 ID を振る（U6 と同じ）

### URL は Client を作るときに組み立てる

- `base_url=` を渡せばそれを使い、環境変数は読まない。渡さなければ、プリセットが環境変数から組み立てる
- 環境変数がないときは、`Client` を作るときに `ConfigurationError` にする。キーがないときと同じで、API を呼ぶ前に分かる
- リソース名とリージョンは、ホスト名の1つのラベル（英数字とハイフン）であることを確かめる。キーは組み立てた URL のホストへ送るので、打ち間違いや `.` を含む値で、別のホストへキーを送らないため
- `AZURE_OPENAI_ENDPOINT` は `https://` で始まり、パスがないか `/openai/v1` であることを確かめる。古い形の URL（`/openai/deployments/...`）に `/openai/v1` を足すと、どこにも届かない URL になるため
- `ANTHROPIC_FOUNDRY_RESOURCE` と `ANTHROPIC_FOUNDRY_BASE_URL` が両方あれば `ConfigurationError` にする。SDK の文書が2つを「mutually exclusive」とし、どちらを優先するかを書いていないため
- 組み立てる仕組み（`ProviderPreset` の欄）は内部のもので、`register_provider()` には足さない（[公開 API を最小にする](minimal-public-api.md)）

### キーのヘッダー

- アダプターごとの既定（`Authorization: Bearer`・`x-api-key`・`x-goog-api-key`）を、プリセットが替えられるようにする。`azure` と `azure-chat` は `api-key` に、キーをそのまま入れて送る。Microsoft の生の HTTP の例が、どれもこのヘッダーを使うため
- `foundry` と `bedrock` は `x-api-key`、`vertex` は `x-goog-api-key` で、アダプターの既定のまま。どれも文書が受け付けると書いている
- `headers=` で認証のヘッダーを置き換える仕組み（[判断](custom-headers.md)）は、替えたヘッダーの名前で働く。`azure` で `headers={"api-key": ...}` を渡せば、キーの環境変数はなくてよい

### 経路ごとに送る前に止めるもの

プリセットのオプションで決める。どれも、互換サーバーを `register_provider()` で足すときにも使える。

- `bedrock`: URL の画像を送る前に `UnsupportedFeatureError` にする。文書が「URL sources for images and documents」を受け付けないと書くため。`anthropic` アダプターにオプション `image_url` を足す（`openai_chat` と同じ名前と意味）
- `azure-chat`: `max_tokens_field` を `max_completion_tokens` にする（推論モデルが `max_tokens` を受け付けない）。`reasoning_summary=True` は送る前に止める（Chat Completions は推論の本文を返さない）。`openai_chat` アダプターにオプション `reasoning_summary` を足す
  - OpenAI 本家の Chat Completions は、これまでどおり接続先の URL で決める（`openai-chat:` に別の `base_url` を渡したときに止めないため。[判断](reasoning-summary.md)）

### 生データの再送

- 再送はプロバイダーキーごとなので（[判断](provider-state-resend.md)）、`anthropic`・`foundry`・`bedrock` の間では送り返さない。会話の途中で経路を替えると、共通の型から作り直して送る（thinking ブロックは落ちる）
- 同じ経路の中では、アダプターの決まりのまま。`bedrock` の別のモデルへも、`anthropic` と同じく thinking ブロックを送り返す。受け付けるかは U43 で確かめる

### やらないこと

- キーを作り直すこと（Bedrock の短期のキーは 12 時間で切れる）。認証の側の仕事なので、作り直したキーを `api_key=` で渡してもらう
- SigV4・OAuth・Entra ID の認証

## 却下した案

- **README で `register_provider()` の書き方を案内するだけにする**: 背景のとおり、モデルの文字列だけで切り替えられず、Azure の `api-key` ヘッダーも `headers=` で認証のヘッダーを止める書き方が要る
- **`base_url=` を必ず渡してもらう**: 作者の判断で却下。モデルを替えるたびに `base_url` も替えることになる
- **Azure に `Authorization: Bearer <キー>` で送る**: 既存のアダプターのまま送れるが、キーを Bearer に入れる例は1つの文書にしかない
- **Azure のプリセットを1つにして、Responses と Chat Completions を自動で選ぶ**: deployment 名からはモデルが分からない。`openai` と `openai-chat` と同じく、利用者が選ぶ
- **`register_provider()` に `auth_header=` や URL の組み立てを足す**: 公開 API が増える。要望が出てから考える
- **Vertex で v1beta1 を使う**: 関数呼び出しの `id` が返るが、ベータのパスになる
- **Foundry の2つの環境変数が両方あれば、どちらかを優先する**: SDK の文書に決まりがなく、黙って選ぶと別のリソースへ送ることがある

## 影響

- [scope](../product-specs/scope.md): API キーで呼べるクラウドの経路を「やること」に移し、認証は「やらないこと」に残す
- [公開 API](../product-specs/public-api.md): プリセットの表、送れるものの表、オプションの表（`anthropic` の `image_url`、`openai_chat` の `reasoning_summary`）
- [SECURITY.md](../SECURITY.md): 環境変数から組み立てた URL へキーを送ること、その前に値を確かめること
- E2E: URL にリソース名やリージョンが入る。記録には決まった値に置き換えて残し、再生ではその値を環境変数に入れる（[E2E テスト](../e2e-tests.md)）
