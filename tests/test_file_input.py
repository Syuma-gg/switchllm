"""ファイル（PDF など）の入力。FilePart を各社の形で送り、受け付けないと分かっている種類は送る前に知らせる
（docs/design-docs/file-input.md）。"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from switchllm import FilePart, ImagePart, Message, TextPart, UnsupportedFeatureError

PDF = base64.b64encode(b"%PDF-1.4\n% switchllm\n").decode("ascii")
TEXT = base64.b64encode("見出し\n本文".encode()).decode("ascii")
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

OK: dict[str, dict[str, Any]] = {
    "openai:gpt-5.5": {"status": "completed", "output": [], "usage": {}},
    "openai-chat:gpt-x": {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
    "anthropic:claude-sonnet-5-5": {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {}},
    "gemini:gemini-flash-latest": {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]},
}
MODELS = list(OK)


def _pdf(filename: str | None = "report.pdf") -> FilePart:
    return FilePart(PDF, "application/pdf", filename=filename)


def _send(api, model: str, part: FilePart, **options: Any) -> dict[str, Any]:
    api.reply_json(OK[model])
    api.client(model, **options).chat([Message.user([part, TextPart("要約して")])])
    return api.body()


def _rejected(api, model: str, part: Any, error: type[Exception], match: str, **options: Any) -> None:
    with pytest.raises(error, match=match):
        api.client(model, **options).chat([Message.user([part, TextPart("要約して")])])
    assert api.requests == []


# --------------------------------------------------------------------------- #
# 各社の形
# --------------------------------------------------------------------------- #
def test_responses_sends_files_as_input_file(api):
    body = _send(api, "openai:gpt-5.5", _pdf())
    assert body["input"][0]["content"] == [
        {"type": "input_file", "file_data": f"data:application/pdf;base64,{PDF}", "filename": "report.pdf"},
        {"type": "input_text", "text": "要約して"},
    ]


def test_responses_sends_other_types_without_checking_them(api):
    # 受け付ける種類の一覧は長く、拡張子で書かれているので、確かめずに送る（判断は API に任せる）
    body = _send(api, "openai:gpt-5.5", FilePart(PDF, DOCX))
    assert body["input"][0]["content"][0] == {"type": "input_file", "file_data": f"data:{DOCX};base64,{PDF}"}


def test_chat_completions_sends_pdf_as_a_file_part(api):
    body = _send(api, "openai-chat:gpt-x", _pdf())
    assert body["messages"][0]["content"] == [
        {"type": "file", "file": {"file_data": f"data:application/pdf;base64,{PDF}", "filename": "report.pdf"}},
        {"type": "text", "text": "要約して"},
    ]
    body = _send(api, "openai-chat:gpt-x", _pdf(filename=None))
    assert body["messages"][0]["content"][0] == {"type": "file", "file": {"file_data": f"data:application/pdf;base64,{PDF}"}}


def test_chat_completions_accepts_only_pdf(api):
    # OpenAI の文書: 「Chat Completions accepts only PDF files as `file` content parts」
    _rejected(api, "openai-chat:gpt-x", FilePart(TEXT, "text/plain"), UnsupportedFeatureError, "only PDF")


@pytest.mark.parametrize("model", ["deepseek:deepseek-flash", "ollama:llama3", "lmstudio:local-model"])
def test_presets_without_file_input_reject_files_before_sending(api, model):
    # DeepSeek は file_id でしか受け付けず形も違い、Ollama・LM Studio の文書にはファイルの記述がない
    _rejected(api, model, _pdf(), UnsupportedFeatureError, "file input")


def test_file_input_false_rejects_files_on_any_compatible_server(api):
    _rejected(api, "openai-chat:gpt-x", _pdf(), UnsupportedFeatureError, "file input", file_input=False)


def test_anthropic_sends_pdf_as_a_document(api):
    body = _send(api, "anthropic:claude-sonnet-5-5", _pdf())
    assert body["messages"][0]["content"] == [
        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": PDF}},
        {"type": "text", "text": "要約して"},
    ]  # filename を title に入れない


@pytest.mark.parametrize("media_type", ["text/plain", "text/markdown", "text/csv", "application/json", "application/xml"])
def test_anthropic_sends_text_files_as_text_documents(api, media_type):
    body = _send(api, "anthropic:claude-sonnet-5-5", FilePart(TEXT, media_type, filename="notes"))
    assert body["messages"][0]["content"][0] == {
        "type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "見出し\n本文"},
    }


def test_anthropic_rejects_binary_documents_before_sending(api):
    # Anthropic の文書: 「Binary formats such as .xlsx or .docx are not supported in document blocks」
    _rejected(api, "anthropic:claude-sonnet-5-5", FilePart(PDF, DOCX), UnsupportedFeatureError, "PDF and plain text")


def test_anthropic_text_must_be_utf8_base64(api):
    not_utf8 = base64.b64encode(b"\xff\xfe\x00").decode("ascii")
    _rejected(api, "anthropic:claude-sonnet-5-5", FilePart(not_utf8, "text/plain"), UnsupportedFeatureError, "UTF-8")
    _rejected(api, "anthropic:claude-sonnet-5-5", FilePart("見出し", "text/plain"), ValueError, "base64")


def test_gemini_sends_files_as_inline_data(api):
    body = _send(api, "gemini:gemini-flash-latest", _pdf())
    assert body["contents"][0]["parts"] == [{"inlineData": {"mimeType": "application/pdf", "data": PDF}}, {"text": "要約して"}]
    body = _send(api, "gemini:gemini-flash-latest", FilePart(PDF, DOCX))  # 種類は確かめずに送る
    assert body["contents"][0]["parts"][0] == {"inlineData": {"mimeType": DOCX, "data": PDF}}


# --------------------------------------------------------------------------- #
# どのアダプターでも送る前に知らせること
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("media_type, match", [
    ("image/png", "use ImagePart"),
    ("audio/mpeg", "audio and video"),
    ("video/mp4", "audio and video"),
])
def test_images_audio_and_video_are_not_sent_as_files(api, model, media_type, match):
    _rejected(api, model, FilePart(PDF, media_type), UnsupportedFeatureError, match)


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("part", [
    FilePart(None, "application/pdf"),
    FilePart("", "application/pdf"),
    FilePart(PDF, None),
    FilePart(PDF, ""),
], ids=["data-none", "data-empty", "media-type-none", "media-type-empty"])
def test_files_without_data_or_media_type_are_rejected(api, model, part):
    # 中身のないファイルを送って、API のエラーで初めて気づくことにしない
    _rejected(api, model, part, TypeError, "FilePart")


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("role", ["system", "assistant"])
@pytest.mark.parametrize("part", [_pdf(), ImagePart(data="iVBORw0KGgo=")], ids=["file", "image"])
def test_files_and_images_are_only_for_user_messages(api, model, role, part):
    # 以前は、どのアダプターも user 以外のメッセージの画像を黙って落としていた
    with pytest.raises(TypeError, match="only user messages"):
        api.client(model).chat([Message(role=role, content=[TextPart("t"), part]), Message.user("hi")])
    assert api.requests == []


# --------------------------------------------------------------------------- #
# FilePart.from_file
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name, media_type", [
    ("report.PDF", "application/pdf"),
    ("data.csv", "text/csv"),  # Windows の mimetypes は application/vnd.ms-excel を返すことがある
    ("notes.md", "text/markdown"),
    ("notes.txt", "text/plain"),
    ("spec.docx", DOCX),
    ("sheet.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("unknown.zzqx", "application/octet-stream"),
])
def test_from_file_reads_name_media_type_and_base64(tmp_path, name, media_type):
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4 bytes")
    part = FilePart.from_file(path)
    assert (part.media_type, part.filename) == (media_type, name)
    assert base64.b64decode(part.data) == b"%PDF-1.4 bytes"


def test_from_file_media_type_can_be_given(tmp_path):
    path = tmp_path / "export.dat"
    path.write_bytes(b"a,b\n1,2\n")
    assert FilePart.from_file(path, media_type="text/csv").media_type == "text/csv"
