"""画像入力（docs/e2e-tests.md の「画像」）。"""

from __future__ import annotations

import base64

import pytest
from e2e_support import MAX_TOKENS, solid_png

from switchllm import ImagePart, Message, TextPart, UnsupportedFeatureError


@pytest.mark.vision
@pytest.mark.calls(1)
def test_image_base64(client, note):
    image = ImagePart(data=base64.b64encode(solid_png((255, 0, 0))).decode(), media_type="image/png")
    res = client.chat([Message.user([TextPart("この画像は何色？色の名前を一語で答えて。"), image])], max_tokens=MAX_TOKENS)
    assert "赤" in res.text or "red" in res.text.lower(), res.text[:80]
    note(f"text={res.text.strip()[:20]!r}")


@pytest.mark.only("gemini")
@pytest.mark.calls(0)
def test_image_url_is_rejected_before_sending(client):
    # generateContent は任意の URL の画像を受け付けないので、送る前に知らせる（料金なし）
    image = ImagePart(url="https://example.com/cat.png")
    with pytest.raises(UnsupportedFeatureError):
        client.chat([Message.user([TextPart("何が写っている？"), image])])
