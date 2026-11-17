# 実行計画

複数ステップにまたがる作業は、計画を文書にしてリポジトリに置く。会話が途切れても、次の担当者（人でもエージェントでも）が続きから進められるようにするため。

1回のコミットで終わる小さな変更には、計画は要らない。

## 書き方

`exec-plans/active/<短い名前>.md` に、次の見出しで書く。

- **目的**: 何が終わったら完了か
- **手順**: チェックボックス付きの一覧（`- [ ]` / `- [x]`）
- **進捗ログ**: 日付つきで、やったことと分かったこと
- **判断ログ**: 日付つきで、決めたことと理由

終わったら `exec-plans/completed/<YYYY-MM-DD>-<短い名前>.md` に移し、下の一覧を更新する。途中で見つけた負債は [tech-debt-tracker.md](exec-plans/tech-debt-tracker.md) に書く。

## 進行中

| 計画 | 目的 |
|---|---|
| [実 API 検証](exec-plans/active/real-api-verification.md) | 未検証の前提を、実際の API で確かめる |
| [v0.1 公開](exec-plans/active/v0.1-release.md) | PyPI に最初の版を公開する |
| [モデルの層の機能を足す](exec-plans/active/model-layer-features.md) | 履歴の JSON 保存・復元、構造化出力、推論の強さ、非同期のクライアント、`headers=`、PDF・ファイルの入力 |

## 完了

| 計画 | 完了日 |
|---|---|
| [軽量化とドキュメントの構造化](exec-plans/completed/2026-11-09-lightweight-and-docs.md) | 2026-11-09 |
| [切り替えの精査とテストの追加](exec-plans/completed/2026-11-15-switching-review.md) | 2026-11-15 |
