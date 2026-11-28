# Anthropic で max_tokens を省いたら 64000 を送る

- 状態: 採用（2026-11-28、作者の判断）
- 検証: 一次情報で確認、実 API 未検証（U36、U37）
- コード: `providers/anthropic.py`（`DEFAULT_MAX_TOKENS`）
- テスト: `tests/test_anthropic.py`（`test_default_max_tokens_fits_current_models`）、`e2e/test_e2e_basic.py`（`test_max_tokens_omitted`、`test_max_tokens_above_model_limit`）
- 計画: [載せ替えの負担を減らす](../exec-plans/active/model-switch-friction.md) の 1

## 背景

- Anthropic の Messages API は `max_tokens` が必須。ほかの接続先（OpenAI Responses・Gemini・Chat Completions 互換）は省ける。switchllm は、省かれたら上限を送らず、モデルの上限まで出させる
- これまでは、省かれたら Anthropic にだけ 4096 を送っていた。同じコードでも Anthropic でだけ出力が切れる。例外にならずに結果だけが変わるので、載せ替えの負担の中でも気づきにくい
- Claude 5 系は、何も指定しなくても考える。thinking のトークンも `max_tokens` に含まれ、effort の既定は多くのモデルで `high`。4096 では、考える問いが答える前に切れやすい
- 2026-11-15 には「どの値を既定にしても、対応していないモデルがある」として、README に書くだけにしていた（[切り替えの精査](../exec-plans/completed/2026-11-15-switching-review.md) の S12）

一次情報（[Anthropic Messages](../references/anthropic-messages.md) の「出力の上限」）:

- 同期の Messages API での出力の上限
  - 128K: Claude 5 系、Opus 4.8〜4.6、Sonnet 4.6
  - 64K: Opus 4.5、Sonnet 4.5、Haiku 4.5
  - 32K: 提供を終えた Opus 4.1・Opus 4 だけ
- 出力のレート制限（OTPM）は、実際に出したトークンだけを数え、`max_tokens` は数えない。料金も、実際に出した分だけかかる
- Claude 4.5 以降は、入力と `max_tokens` の和が文脈の窓を超えても受け付ける
- 大きな `max_tokens` をストリーミングなしで送ることは勧めていない。公式 SDK は、10 分を超えそうなリクエストを送る前に止める。API の側が送る前に断るとは書かれていない

## 決定

- **`max_tokens` を省いたら、Anthropic には 64000 を送る**（作者の判断）
  - 提供中のモデルすべての上限に収まる値のうち、いちばん大きい
  - Claude 5 系では上限（128K）の半分になるが、1回の応答で 64K を超えて出すことはまれ
- `max_tokens=` を渡せば、その値を送る（0 も）。`Client(..., default_max_tokens=...)` で既定を変えられるのも、これまでと同じ
- モデルの一覧は持たない。値は1つで、モデル名を見て変えない（[実 API 検証](../exec-plans/active/real-api-verification.md) の 2026-11-17 の判断）
- 上限が 64000 より小さいモデル（提供を終えた Opus 4.1・Opus 4）では、ライブラリは先回りして弾かず、API の応答をそのまま返す。上限を超える値を API が 400 にするか、切り詰めて受け付けるかは、文書に書かれていない（U36）

## 却下した案

- **4096 のまま、手引きで「`max_tokens` は明示する」と案内する**（2026-11-15 の判断）: 基本方針の優先順位 1（文字列を変えるだけで、同じコードが同じように動く）に反する。明示しないコードは、Anthropic でだけ黙って切れ続ける
- **Models API（`GET /v1/models/{model_id}`）の `max_tokens` を聞いて使う**: ほかの社と同じ「モデルの上限」になる。ただし、`Client` ごとに通信が1回増え、聞けなかったときの扱い、同期と非同期の両方、テストと E2E の記録が増える。64000 との違いは、64K を超えて出す応答でしか表れない
- **128000 にする**: 4.5 系（上限 64K）で上限を超える。400 になるなら、4.5 系では `max_tokens` を省いたコードが動かなくなる
- **32000 にする**: 提供を終えたモデルにも収まるが、Claude 5 系で effort が高く推論が長いと、まだ切れることがある
- **API のエラーを見て、小さい値で送り直す**: エラーの文言に頼るうえ、呼び出しが2回になる

## 影響

- `max_tokens` を省いても、長い答えや考える問いが 4096 で切れなくなる。料金とレート制限は変わらない（どちらも実際に出した分だけ）
- そのぶん、ストリーミングなしの呼び出しは長くかかることがある。既定のタイムアウトでは、応答が届くまでの待ちが 120 秒を超えると `APIConnectionError` になる。これは上限を送らないほかの社と同じで、長い出力はストリーミングで受けるか `timeout=` を延ばす（[RELIABILITY.md](../RELIABILITY.md)）
- 公開 API の形は変わらない。既定の値が変わるので、CHANGELOG に書く（0.1.0 は未公開）
