"""ファイルの入力（docs/e2e-tests.md の「ファイル」）。

受け付けないと分かっている接続先（DeepSeek・Ollama・LM Studio のプリセット、Chat Completions 互換の
PDF 以外）では、送る前に UnsupportedFeatureError になることを確かめる（呼ばない）。
"""

from __future__ import annotations

import base64

import pytest
from e2e_support import MAX_TOKENS, adapter_of, file_input_supported, text_pdf

from switchllm import FilePart, Message, TextPart, UnsupportedFeatureError

CODE = "SWLM-7319"
WORD = "みずいろ"


def _ask(client, part: FilePart, question: str):
    # Anthropic の文書は、PDF を文字より先に置くよう勧める
    return client.chat([Message.user([part, TextPart(question)])], max_tokens=MAX_TOKENS)


@pytest.mark.premise("U28", "U29", "U30", "U31")
@pytest.mark.calls(1)
def test_pdf_file(client, model, note):
    part = FilePart(base64.b64encode(text_pdf(f"Code: {CODE}")).decode("ascii"), "application/pdf", filename="code.pdf")
    question = "この PDF に書かれているコードを、そのまま答えて。"
    if not file_input_supported(model):
        with pytest.raises(UnsupportedFeatureError):
            _ask(client, part, question)
        return
    res = _ask(client, part, question)
    assert CODE in res.text.replace(" ", ""), res.text[:80]
    note(f"text={res.text.strip()[:30]!r}")


@pytest.mark.premise("U28", "U30", "U31")
@pytest.mark.calls(1)
def test_text_file(client, model, note):
    part = FilePart(base64.b64encode(f"合言葉は「{WORD}」です。\n".encode()).decode("ascii"), "text/plain", filename="notes.txt")
    question = "このファイルに書かれた合言葉を答えて。"
    if not file_input_supported(model) or adapter_of(model) == "openai_chat":
        # Chat Completions 互換は PDF だけ（OpenAI の文書）
        with pytest.raises(UnsupportedFeatureError):
            _ask(client, part, question)
        return
    res = _ask(client, part, question)
    assert WORD in res.text, res.text[:80]
    note(f"text={res.text.strip()[:30]!r}")
