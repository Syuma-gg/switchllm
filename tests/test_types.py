"""プロバイダー非依存の共通型（switchllm.types）。"""

from __future__ import annotations

from switchllm import ImagePart, Message, TextPart, ToolCall


def test_from_dict_tool_message_accepts_id_alias_and_stringifies_content():
    m = Message.from_dict({"role": "tool", "id": "call_1", "content": 42, "name": "f", "is_error": 1})
    assert (m.role, m.tool_call_id, m.content, m.name, m.is_error) == ("tool", "call_1", "42", "f", True)


def test_from_dict_assistant_with_tool_calls():
    call = ToolCall(id="c2", name="g")
    m = Message.from_dict({
        "role": "assistant",
        "content": "調べます",
        "tool_calls": [{"id": "c1", "name": "f", "arguments": {"x": 1}}, call],
    })
    assert m.text == "調べます"
    assert m.tool_calls == [ToolCall(id="c1", name="f", arguments={"x": 1}), call]
    assert m.provider_state is None  # dict から作ったものは生データを持たない（別モデル扱いで変換し直す）


def test_text_and_parts_of_list_content():
    m = Message.user([TextPart("a"), ImagePart(data="AAAA"), TextPart("b")])
    assert m.text == "ab"
    assert [p.type for p in m.parts()] == ["text", "image", "text"]
    assert Message.user("").parts() == []  # 空の文字列はパーツにしない


def test_arguments_json_prefers_parsed_arguments_and_falls_back_to_raw():
    assert ToolCall("1", "f", {"都市": "東京"}).arguments_json() == '{"都市": "東京"}'  # ASCII にエスケープしない
    assert ToolCall("1", "f", {}, raw_arguments="{broken").arguments_json() == "{broken"
    assert ToolCall("1", "f").arguments_json() == "{}"


def test_image_part_data_url_and_file_type_fallback(tmp_path):
    assert ImagePart(url="https://example.com/a.png").as_data_url() == "https://example.com/a.png"
    assert ImagePart(data="AAAA", media_type="image/gif").as_data_url() == "data:image/gif;base64,AAAA"
    jpeg = tmp_path / "photo.JPEG"
    jpeg.write_bytes(b"\xff\xd8")
    assert ImagePart.from_file(jpeg).media_type == "image/jpeg"
    unknown = tmp_path / "scan.unknownext"
    unknown.write_bytes(b"x")
    assert ImagePart.from_file(unknown).media_type == "image/png"  # 判定できなければ image/png
