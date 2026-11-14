# 主要3社はネイティブ API を使う

- 状態: 採用
- 検証: 一次情報で確認
- コード: `providers/openai_responses.py`、`providers/anthropic.py`、`providers/gemini.py`

## 背景

Anthropic も Gemini も OpenAI SDK 互換の入口を持つので、Chat Completions のアダプター1つで全社を呼ぶこともできる。

ただし Anthropic は互換レイヤーを「テスト・比較用で、本番向けの長期的な解決策ではない」と位置づけ、プロンプトキャッシュ非対応・thinking の中身が返らないなどの制限を挙げている。Google も、OpenAI のライブラリを既に使っていないなら Gemini API を直接呼ぶよう勧めている。

## 決定

主要3社は、それぞれのネイティブ API のアダプターを持つ。

## 却下した案

- **互換エンドポイントに一本化する**: 実装は最小になるが、thinking・署名・キャッシュなどのネイティブ機能を落とす

## 影響

- アダプターごとに形式の差を吸収するコードが要る
- Gemini は generateContent を使う。Google は generateContent を「レガシー」とし、新規開発には Interactions API を勧めている（U7、[負債](../exec-plans/tech-debt-tracker.md)）

## 出典

- [Anthropic: OpenAI SDK compatibility](https://docs.anthropic.com/en/api/openai-sdk)
- [Google: OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)
