"""usage の内訳（docs/e2e-tests.md の「usage」、docs/design-docs/usage-normalization.md）。"""

from __future__ import annotations

import pytest
from e2e_support import MAX_TOKENS, usage_problems

QUESTION = "17×23 はいくつ？答えの数字だけを書いて。"  # 推論トークンが出るように、少し考えさせる


@pytest.mark.premise("U14", "U18", "U44")
@pytest.mark.calls(2)
def test_usage_breakdown(client, adapter, note):
    res = client.chat(QUESTION, max_tokens=MAX_TOKENS)
    done = [ev.response for ev in client.stream(QUESTION, max_tokens=MAX_TOKENS) if ev.type == "done"][0]
    problems = usage_problems(adapter, res) + usage_problems(adapter, done)
    assert not problems, "; ".join(problems)
    note(f"chat: {res.usage.details}")
    note(f"stream: {done.usage.details}")
