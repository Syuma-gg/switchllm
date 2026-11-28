"""テキストの送受信と、共通パラメータの境界値（docs/e2e-tests.md の「基本」）。"""

from __future__ import annotations

import pytest
from e2e_support import MAX_TOKENS

from switchllm import APIError, BadRequestError, DroppedParameterWarning, Message, UnsupportedFeatureError


@pytest.mark.premise("U12", "U13", "U25")
@pytest.mark.calls(1)
def test_text(client, note):
    res = client.chat("「ok」とだけ返して", max_tokens=MAX_TOKENS)
    assert res.text.strip(), f"empty text (stop_reason={res.stop_reason})"
    assert res.stop_reason == "end"
    assert res.usage.input_tokens > 0 and res.usage.output_tokens > 0
    note(f"model={res.model} text={res.text.strip()[:20]!r}")


@pytest.mark.calls(1)
def test_system_prompt(client):
    # system を入れる場所は各社で違う（instructions / system / systemInstruction / role: system）
    res = client.chat(
        "合言葉は？",
        system="合言葉は「みかん」。合言葉を聞かれたら、合言葉だけを答える。",
        max_tokens=MAX_TOKENS,
    )
    assert "みかん" in res.text, res.text[:80]


@pytest.mark.premise("U2")
@pytest.mark.calls(1)
def test_hand_written_history(client):
    # 手で作った assistant メッセージ（生データなし）を、共通型から変換して送る
    res = client.chat(
        [
            Message.user("私の名前はタロウです。覚えて。"),
            Message.assistant("わかりました、タロウさん。"),
            Message.user("私の名前は？名前だけを答えて。"),
        ],
        max_tokens=MAX_TOKENS,
    )
    assert "タロウ" in res.text, res.text[:80]


@pytest.mark.calls(1)
def test_max_tokens_lower_boundary(client, note):
    # 16 は OpenAI の max_output_tokens の下限。長く書かせて、上限で止まったと分かるか
    res = client.chat("1から200まで、数字をスペース区切りで書いて。", max_tokens=16)
    assert res.stop_reason == "max_tokens", f"stop_reason={res.stop_reason} text={res.text[:80]!r}"
    note(f"本文 {len(res.text)} 文字、output_tokens={res.usage.output_tokens}")


@pytest.mark.premise("U37")
@pytest.mark.calls(1)
def test_max_tokens_omitted(client, note):
    # max_tokens を省いても答える。ほかの社には上限を送らない。Anthropic は必須なので 64000 を、
    # ストリーミングなしで送る（公式 SDK はこの大きさなら送る前に止めるが、API が断るとは書かれていない）
    res = client.chat("「ok」とだけ返して")
    assert res.text.strip(), f"empty text (stop_reason={res.stop_reason})"
    assert res.stop_reason == "end"
    note(f"output_tokens={res.usage.output_tokens}")


@pytest.mark.only("anthropic")
@pytest.mark.premise("U36")
@pytest.mark.calls(1)
def test_max_tokens_above_model_limit(client, note):
    # どのモデルの出力の上限（128K まで）も超える値。ライブラリは先回りして弾かない。
    # API が 400 にするか、切り詰めて受け付けるかを記録する（文書に書かれていない）
    try:
        res = client.chat("「ok」とだけ返して", max_tokens=200_000)
    except BadRequestError as e:
        assert e.status_code == 400 and e.message
        note(f"API が拒否した: {e.message[:160]}")
        return
    assert res.text.strip(), f"empty text (stop_reason={res.stop_reason})"
    note(f"受け付けた: stop_reason={res.stop_reason}")


@pytest.mark.calls(1)
def test_stop_sequence(client, adapter, note):
    prompt = "1から9まで、数字をカンマ区切りで1行に書いて。ほかには何も書かないで。"
    if adapter == "openai":
        # Responses API には stop がない。黙って無視せず、送る前に知らせる
        with pytest.raises(UnsupportedFeatureError):
            client.chat(prompt, stop=["5"])
        return
    res = client.chat(prompt, stop=["5"], max_tokens=MAX_TOKENS)
    assert "4" in res.text and "5" not in res.text, res.text[:80]
    assert res.stop_reason == "end"
    note(f"text={res.text.strip()!r}")


@pytest.mark.calls(1)
def test_temperature_zero(client, adapter, note):
    # 下限の 0 を、0 だからと落とさずに送る。thinking が既定で有効なモデルは temperature の指定を
    # 拒否することがあり、そのときは API のエラーをそのまま返す（モデルごとの一覧は持たない）
    if adapter == "anthropic":
        # 既定の版（2026-10-21）は、どのモデルでも temperature を 400 にする。送らずに捨て、警告で知らせる。
        # temperature を除いた残りのリクエストは、そのまま受け付けられる
        with pytest.warns(DroppedParameterWarning, match="anthropic_version"):
            res = client.chat("「ok」とだけ返して", temperature=0, max_tokens=MAX_TOKENS)
        assert res.text.strip(), f"empty text (stop_reason={res.stop_reason})"
        note("temperature を送らずに捨てた（DroppedParameterWarning）")
        return
    try:
        res = client.chat("「ok」とだけ返して", temperature=0, max_tokens=MAX_TOKENS)
    except BadRequestError as e:
        assert e.status_code == 400 and e.message
        note(f"API が拒否した: {e.message[:160]}")
        return
    assert res.text.strip()
    note("受け付けた")


@pytest.mark.calls(1)
def test_empty_user_message(client, note):
    # 空の入力は、応答か API のエラーのどちらか。パースの失敗など、ほかの例外にならない
    try:
        res = client.chat("", max_tokens=MAX_TOKENS)
    except APIError as e:
        assert e.message
        note(f"API が拒否した（{type(e).__name__}）: {e.message[:160]}")
        return
    note(f"受け付けた: stop_reason={res.stop_reason} text={res.text[:40]!r}")
