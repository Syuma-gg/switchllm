"""E2E の結果を、どの前提（U 番号）に数えるか（e2e/e2e_support.py、docs/e2e-tests.md）。

実 API で回した結果は、実 API 検証の各前提の「結果」に書き写すので、別の接続先の結果を数えないこと。
"""

from __future__ import annotations

from e2e_support import SCOPE, relevant, switch_premises


def test_cloud_routes_count_only_for_premises_that_name_them():
    # bedrock や foundry は anthropic アダプターを使うが、Anthropic 本家の前提（U25 の既定の版など）には数えない
    assert relevant(("U25", "U43", "U38"), "bedrock:anthropic.claude-sonnet-5-5") == ("U43", "U38")
    assert relevant(("U25", "U42"), "foundry:claude-sonnet-5-5") == ("U42",)
    assert relevant(("U13", "U1", "U40"), "azure:my-gpt") == ("U40",)
    assert relevant(("U10", "U41"), "azure-chat:my-gpt") == ("U41",)
    assert relevant(("U13", "U34", "U44"), "vertex:gemini-2.5-flash") == ("U44",)


def test_vendor_presets_keep_counting_by_provider_and_adapter():
    assert relevant(("U25", "U43"), "anthropic:claude-sonnet-5-5") == ("U25",)
    assert relevant(("U10", "U41"), "deepseek:deepseek-flash") == ("U10",)  # U10 は openai_chat アダプターの前提


def test_error_hints_premise_counts_for_every_route():
    # U38（断られたときの案内）は、どの接続先でも確かめる
    for model in ("azure:my-gpt", "azure-chat:my-gpt", "foundry:c", "bedrock:anthropic.c", "vertex:gemini-2.5-flash"):
        assert relevant(("U38",), model) == ("U38",)


def test_cloud_premises_are_scoped_to_their_route():
    assert {u: SCOPE[u] for u in ("U40", "U41", "U42", "U43", "U44")} == {
        "U40": frozenset({"azure"}),
        "U41": frozenset({"azure-chat"}),
        "U42": frozenset({"foundry"}),
        "U43": frozenset({"bedrock"}),
        "U44": frozenset({"vertex"}),
    }


def test_switch_premises_for_cloud_routes():
    # 同じ経路の2つのモデルで thinking ブロックを送り返すのは、その経路の前提
    assert switch_premises("bedrock:anthropic.claude-sonnet-5-5", "bedrock:anthropic.claude-haiku-5-5") == ("U43",)
    assert switch_premises("foundry:claude-sonnet-5-5", "foundry:claude-haiku-5-5") == ("U42",)
    assert switch_premises("anthropic:claude-sonnet-5-5", "anthropic:claude-haiku-5-5") == ("U8",)
    # 別のプロバイダーの履歴にダミーの署名を付けて送るのは、Vertex では U44、Gemini API では U5
    assert switch_premises("vertex:gemini-2.5-flash", "openai:gpt-5.5") == ("U44",)
    assert switch_premises("openai:gpt-5.5", "gemini:gemini-flash-latest") == ("U5",)
    assert switch_premises("vertex:gemini-2.5-flash", "gemini:gemini-flash-latest") == ("U44", "U5")
    # 別の経路どうしなら、thinking ブロックを送らないので Anthropic の前提には数えない
    assert switch_premises("bedrock:anthropic.claude-sonnet-5-5", "anthropic:claude-sonnet-5-5") == ()
