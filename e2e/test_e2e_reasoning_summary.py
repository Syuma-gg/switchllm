"""推論の要約（docs/e2e-tests.md の「推論の要約」）。

各社の「要約を返す」指定を送り、返った要約が ``Response.thinking`` と ``thinking`` イベントに入ることを確かめる。
Chat Completions 互換のサーバーには何も足さない。OpenAI 本家の Chat Completions は送る前に止める。
"""

from __future__ import annotations

import pytest
from e2e_support import REASONING_MAX_TOKENS, describe_reasoning, requires_key, tool_roundtrip

from switchllm import UnsupportedFeatureError, parse_model
from switchllm.providers import PRESETS

# 考えないと答えにくい問い（答えは 63）。答えの文言は確かめない
PROMPT = "1 から 1000 までの整数のうち、各桁の数字の和が 10 になるものはいくつありますか。数だけを答えて。"


def _openai_chat_completions(model: str) -> bool:
    """OpenAI のモデルの Chat Completions（本家の ``openai-chat`` と Azure の ``azure-chat``）。推論の本文を返さない。"""
    key = parse_model(model)[0]
    return key == "openai-chat" or PRESETS[key].options.get("reasoning_summary", True) is False


@pytest.mark.premise("U32", "U33", "U34", "U35")
@pytest.mark.calls(1)
def test_reasoning_summary_stream(client, model, note):
    if _openai_chat_completions(model):
        # 本物の OpenAI の Chat Completions は推論の本文を返さない。黙って None にせず、送る前に止める
        with pytest.raises(UnsupportedFeatureError, match="'openai:'"):
            list(client.stream(PROMPT, reasoning_summary=True))
        note("送る前に UnsupportedFeatureError（呼ばない）")
        return
    events = list(client.stream(PROMPT, reasoning_summary=True, max_tokens=REASONING_MAX_TOKENS))
    assert [e.type for e in events].count("done") == 1 and events[-1].type == "done", [e.type for e in events]
    res = events[-1].response
    assert res.text.strip(), f"empty text (stop_reason={res.stop_reason})"
    streamed = "".join(e.text for e in events if e.type == "thinking")
    if requires_key(model):
        # クラウドの API は、頼んだ要約（DeepSeek は推論の本文）を返すはず。返らなければ送る形の誤りを疑う
        assert res.thinking, f"no reasoning summary ({describe_reasoning(res)}, stop_reason={res.stop_reason})"
        assert streamed.strip(), "no thinking events in the stream"
    # ローカルのサーバーは、返すか・どのフィールドかが文書にない（U35）。返ったかを記録する
    note(
        f"要約 {len(res.thinking or '')}文字、thinking イベント {len(streamed)}文字、"
        f"{describe_reasoning(res)}、output_tokens={res.usage.output_tokens}"
    )


@pytest.mark.only("openai", "anthropic", "gemini")  # 互換サーバーには何も足さないので、送る形は変わらない
@pytest.mark.premise("U32", "U33", "U34")
@pytest.mark.calls(2)
def test_reasoning_summary_with_tools(client, note):
    # 要約を頼んだまま、ツールの往復で推論情報（要約の入った thinking ブロック・reasoning アイテム・署名）を
    # 送り返す。Anthropic は display を変えても signature が同じで、手を加えずに送り返す
    first = tool_roundtrip(client, reasoning_summary=True)
    note(f"最初の応答の要約 {len(first.thinking or '')}文字、{describe_reasoning(first)}")
