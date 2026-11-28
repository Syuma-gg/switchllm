"""画像入力（docs/e2e-tests.md の「画像」）。"""

from __future__ import annotations

import base64

import pytest
from e2e_support import MAX_TOKENS, image_url_supported, solid_png

from switchllm import ImagePart, Message, TextPart, UnsupportedFeatureError


@pytest.mark.vision
@pytest.mark.calls(1)
def test_image_base64(client, note):
    image = ImagePart(data=base64.b64encode(solid_png((255, 0, 0))).decode(), media_type="image/png")
    res = client.chat([Message.user([TextPart("この画像は何色？色の名前を一語で答えて。"), image])], max_tokens=MAX_TOKENS)
    assert "赤" in res.text or "red" in res.text.lower(), res.text[:80]
    note(f"text={res.text.strip()[:20]!r}")


# このリポジトリの e2e/assets/red.png（solid_png((255, 0, 0)) と同じもの）。main に push してあること
IMAGE_URL = "https://raw.githubusercontent.com/Syuma-gg/switchllm/main/e2e/assets/red.png"


@pytest.mark.vision
@pytest.mark.premise("U39")
@pytest.mark.calls(1)
def test_image_url(client, model, note):
    # 公開の HTTPS の URL を渡し、接続先の API に取りに行かせる（ライブラリは取りに行かない）。
    # Gemini は fileData.fileUri で送る（U39）。URL を受け付けない接続先（Ollama）は、送る前に知らせる（料金なし）
    message = Message.user([TextPart("この画像は何色？色の名前を一語で答えて。"), ImagePart(url=IMAGE_URL)])
    if not image_url_supported(model):
        with pytest.raises(UnsupportedFeatureError, match="from_file"):
            client.chat([message])
        note("送る前に UnsupportedFeatureError（呼ばない）")
        return
    res = client.chat([message], max_tokens=MAX_TOKENS)
    assert "赤" in res.text or "red" in res.text.lower(), res.text[:80]
    note(f"text={res.text.strip()[:20]!r}")
