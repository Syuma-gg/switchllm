"""API が返したエラーの受け渡し（docs/e2e-tests.md の「エラー」）。失敗するリクエストは多くの場合課金されない。"""

from __future__ import annotations

import pytest
from e2e_support import MAX_TOKENS

from switchllm import AuthenticationError, BadRequestError, NotFoundError, UnsupportedFeatureError, parse_model


@pytest.mark.needs_key
@pytest.mark.calls(1)
def test_invalid_api_key(make_client, model, note):
    # API の文言と本文をそのまま持つ（RELIABILITY.md）。Gemini は無効なキーを 400 で返すことがあるので、
    # どちらの例外だったかを残す
    with make_client(model, api_key="switchllm-e2e-invalid-key", max_retries=0) as bad:
        with pytest.raises((AuthenticationError, BadRequestError)) as info:
            bad.chat("hi", max_tokens=16)
    e = info.value
    assert e.message and e.body is not None
    note(f"{type(e).__name__}（HTTP {e.status_code}）: {e.message[:160]}")


@pytest.mark.calls(1)
def test_unknown_model(make_client, model, note):
    key, _ = parse_model(model)
    with make_client(f"{key}:switchllm-e2e-no-such-model", max_retries=0) as missing:
        with pytest.raises((NotFoundError, BadRequestError)) as info:
            missing.chat("hi", max_tokens=16)
    e = info.value
    assert e.message
    note(f"{type(e).__name__}（HTTP {e.status_code}）: {e.message[:160]}")


@pytest.mark.calls(1)
def test_temperature_above_range(client, adapter, note):
    # 5 はどの API の上限（OpenAI・Gemini は 2、Anthropic は 1）も超える。
    # API のエラーをそのまま返すか、黙って無視される（DeepSeek の思考モードは temperature を無視する）
    if adapter == "anthropic":
        # 既定の版（2026-10-21）では temperature 自体を送れない（test_temperature_zero と同じ）
        with pytest.raises(UnsupportedFeatureError, match="anthropic_version"):
            client.chat("「ok」とだけ返して", temperature=5, max_tokens=MAX_TOKENS)
        note("送る前に UnsupportedFeatureError（呼ばない）")
        return
    try:
        res = client.chat("「ok」とだけ返して", temperature=5, max_tokens=MAX_TOKENS)
    except BadRequestError as e:
        assert e.status_code == 400 and e.message
        note(f"API が拒否した: {e.message[:160]}")
        return
    assert res.text.strip()
    note("受け付けた（無視されたとみられる）")
