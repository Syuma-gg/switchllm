"""headers=（docs/e2e-tests.md の「ヘッダー」）。

ゲートウェイを経由するときのように、APIキーを ``headers=`` で渡し、知らないヘッダーも一緒に送る。
同じ名前のヘッダーを2本送らないことは tests/ のモックで確かめる（docs/design-docs/custom-headers.md）。
"""

from __future__ import annotations

import pytest
from e2e_support import MAX_TOKENS


@pytest.mark.needs_key
@pytest.mark.calls(1)
def test_api_key_in_headers(make_client, model):
    # 各社の認証のヘッダーの名前と形（docs/references/ の「認証」）を、headers= で書いても受け付ける。
    # ライブラリが付ける認証のヘッダーは、大文字と小文字が違っても置き換わり、2本にならない
    provider = make_client(model).provider
    value = f"{provider.auth_scheme} {provider.api_key}" if provider.auth_scheme else provider.api_key
    headers = {provider.auth_header.upper(): value, "X-Switchllm-E2E": "headers"}
    res = make_client(model, headers=headers).chat("「ok」とだけ返して", max_tokens=MAX_TOKENS)
    assert res.text.strip(), f"empty text (stop_reason={res.stop_reason})"
