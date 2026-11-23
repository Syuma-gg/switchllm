"""実 API の応答の記録と再生（e2e/e2e_recording.py、docs/testing.md）。

記録は E2E テストを実 API で回したときに作り、再生は既定の python -m pytest で毎回回る。
ここでは、記録に秘密が残らないこと、再生が記録どおりの応答を返し、リクエストの違いを見逃さないことを確かめる。
"""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path

import httpx
import pytest
from e2e_recording import (
    RECORDINGS,
    AsyncRecorder,
    Recorder,
    Replayer,
    find_secrets,
    load,
    recording_path,
    save,
    scrub,
)


def _serve(*responses: httpx.Response) -> httpx.MockTransport:
    queue = list(responses)
    return httpx.MockTransport(lambda request: queue.pop(0))


def test_recorder_keeps_bodies_but_not_headers_or_key_param():
    upstream = httpx.Response(200, json={"ok": True}, headers={"x-request-id": "req_1", "openai-organization": "org-1"})
    rec = Recorder(_serve(upstream))
    with httpx.Client(transport=rec) as http:
        res = http.post("https://api.example.com/v1/x?key=SECRET&alt=sse", json={"a": "東京"},
                        headers={"Authorization": "Bearer SECRET", "x-api-key": "SECRET"})
    assert res.json() == {"ok": True}  # 呼び出し側には、いつもどおりの応答を返す
    assert rec.exchanges == [{
        "request": {"method": "POST", "url": "https://api.example.com/v1/x?alt=sse", "json": {"a": "東京"}},
        "response": {"status": 200, "content_type": "application/json", "json": {"ok": True}},
    }]
    assert "SECRET" not in json.dumps(rec.exchanges) and "org-1" not in json.dumps(rec.exchanges)


def test_async_recorder_records_the_same_as_recorder():
    # AsyncClient の E2E も、同期と同じ形で記録する（秘密を残さないことも同じ）
    def upstream():
        return httpx.Response(200, json={"ok": True}, headers={"x-request-id": "req_1", "retry-after": "1"})

    def send_sync():
        rec = Recorder(_serve(upstream()))
        with httpx.Client(transport=rec) as http:
            http.post("https://api.example.com/v1/x?key=SECRET", json={"a": "東京"}, headers={"x-api-key": "SECRET"})
        return rec.exchanges

    async def send_async():
        rec = AsyncRecorder(_serve(upstream()))
        async with httpx.AsyncClient(transport=rec) as http:
            res = await http.post("https://api.example.com/v1/x?key=SECRET", json={"a": "東京"},
                                  headers={"x-api-key": "SECRET"})
            assert res.json() == {"ok": True} and res.headers["retry-after"] == "1"
        return rec.exchanges

    recorded = asyncio.run(send_async())
    assert recorded == send_sync() and len(recorded) == 1
    assert "SECRET" not in json.dumps(recorded) and "req_1" not in json.dumps(recorded)


def test_recorder_keeps_retry_after_for_replaying_retries():
    rec = Recorder(_serve(httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"retry-after": "2"})))
    with httpx.Client(transport=rec) as http:
        http.post("https://api.example.com/v1/x", json={})
    assert rec.exchanges[0]["response"]["retry_after"] == "2"


def test_recorder_decodes_compressed_streams_and_returns_plain_content():
    # 本物の API は gzip で返すことがある。記録は読める形で残し、呼び出し側にも解凍済みの本文を渡す
    sse = "data: {\"x\": 1}\n\ndata: [DONE]\n\n"
    upstream = httpx.Response(200, content=gzip.compress(sse.encode()),
                              headers={"content-encoding": "gzip", "content-type": "text/event-stream"})
    rec = Recorder(_serve(upstream))
    with httpx.Client(transport=rec) as http:
        res = http.post("https://api.example.com/v1/x", json={})
    assert res.text == sse and "content-encoding" not in res.headers
    assert rec.exchanges[0]["response"] == {"status": 200, "content_type": "text/event-stream", "text": sse}


def test_scrub_replaces_secret_values_and_ignores_short_ones():
    text = '{"error": "Incorrect API key provided: sk-abcdef1234567890"}'
    assert scrub(text, ["sk-abcdef1234567890"]) == '{"error": "Incorrect API key provided: <redacted>"}'
    # 短い値や空の値で置き換えると、記録のあちこちを壊す
    assert scrub(text, ["", "sk"]) == text


@pytest.mark.parametrize(
    "text",
    ["sk-proj-" + "a1B2" * 10, "sk-ant-api03-" + "x9" * 20, "AIza" + "S" * 35, "Bearer abcdefghijklmnopqrstuvwxyz012345"],
)
def test_find_secrets_detects_key_shapes(text):
    assert find_secrets(f'{{"m": "{text}"}}')


def test_find_secrets_ignores_the_fake_key_and_signatures():
    # E2E の無効なキーのテストが使う偽のキーと、thinking の署名（長い base64）は秘密ではない
    assert not find_secrets('{"key": "switchllm-e2e-invalid-key", "signature": "EqoBCkgIAhABGAIiQL' + "A" * 200 + '"}')
    # 署名や暗号化された推論（URL-safe base64）の途中に、たまたま sk- や AIza が現れることがある
    blob = "Zq9" + "sk-" + "b" * 30 + "AIza" + "c" * 40
    assert not find_secrets(f'{{"encrypted_content": "{blob}"}}')


def _exchange(body: dict, reply: dict) -> dict:
    return {
        "request": {"method": "POST", "url": "https://api.example.com/v1/x", "json": body},
        "response": {"status": 200, "content_type": "application/json", "json": reply},
    }


def test_replayer_returns_recorded_responses_in_order():
    rep = Replayer([_exchange({"n": 1}, {"r": 1}), _exchange({"n": 2}, {"r": 2})])
    with httpx.Client(transport=httpx.MockTransport(rep)) as http:
        assert http.post("https://api.example.com/v1/x", json={"n": 1}).json() == {"r": 1}
        assert http.post("https://api.example.com/v1/x", json={"n": 2}).json() == {"r": 2}
    assert rep.unused() == 0


def test_replayer_fails_when_the_library_sends_a_different_request():
    # 記録した時と違うリクエストを送るようになったら、実 API で確かめ直す必要がある
    rep = Replayer([_exchange({"model": "m", "temperature": 0}, {"r": 1})])
    with httpx.Client(transport=httpx.MockTransport(rep)) as http:
        with pytest.raises(AssertionError, match="differs from the recording") as e:
            http.post("https://api.example.com/v1/x", json={"model": "m"})
    assert "temperature" in str(e.value)  # どこが違うかを見せる


def test_replayer_fails_on_requests_beyond_the_recording_and_counts_unused_ones():
    rep = Replayer([_exchange({"n": 1}, {"r": 1}), _exchange({"n": 2}, {"r": 2})])
    with httpx.Client(transport=httpx.MockTransport(rep)) as http:
        http.post("https://api.example.com/v1/x", json={"n": 1})
    assert rep.unused() == 1
    rep2 = Replayer([])
    with httpx.Client(transport=httpx.MockTransport(rep2)) as http:
        with pytest.raises(AssertionError, match="more requests than recorded"):
            http.post("https://api.example.com/v1/x", json={})


def test_replayer_treats_synthetic_gemini_ids_as_equal():
    # Gemini が id を返さなかった呼び出しには、ランダムな仮 ID（switchllm_ + 12桁）を振る。実行ごとに変わる
    rep = Replayer([_exchange({"call_id": "switchllm_0123456789ab"}, {"r": 1})])
    with httpx.Client(transport=httpx.MockTransport(rep)) as http:
        assert http.post("https://api.example.com/v1/x", json={"call_id": "switchllm_ffffffffffff"}).json() == {"r": 1}


def test_recorded_exchanges_replay_the_same_bytes(tmp_path):
    sse = "event: message_stop\ndata: {\"type\": \"message_stop\"}\n\n"
    rec = Recorder(_serve(httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})))
    with httpx.Client(transport=rec) as http:
        http.post("https://api.example.com/v1/x", json={"stream": True})
    path = tmp_path / "t.json"
    save(path, {"nodeid": "e2e/test_x.py::test_y[m]", "models": ["anthropic:m"]}, rec.exchanges, secrets=[])
    meta, exchanges = load(path)
    assert meta["models"] == ["anthropic:m"]
    with httpx.Client(transport=httpx.MockTransport(Replayer(exchanges))) as http:
        assert http.post("https://api.example.com/v1/x", json={"stream": True}).text == sse


def test_save_scrubs_secrets(tmp_path):
    rec = Recorder(_serve(httpx.Response(401, json={"error": {"message": "bad key sk-live-0123456789abcdef"}})))
    with httpx.Client(transport=rec) as http:
        http.post("https://api.example.com/v1/x", json={})
    path = tmp_path / "t.json"
    save(path, {"nodeid": "n", "models": []}, rec.exchanges, secrets=["sk-live-0123456789abcdef"])
    assert "sk-live" not in path.read_text(encoding="utf-8")


def test_recording_path_is_safe_on_windows():
    p = recording_path(Path("rec"), "test_e2e_switch", "test_switch_and_back[anthropic:claude-sonnet-5-5->openai:gpt-5.5]")
    assert p.parent == Path("rec") / "test_e2e_switch" and p.suffix == ".json"
    assert not any(c in p.name for c in '<>:"/\\|?*')
    other = recording_path(Path("rec"), "test_e2e_switch", "test_switch_and_back[openai:gpt-5.5->anthropic:claude-sonnet-5-5]")
    assert p != other  # 向きの違う組を取り違えない


def test_committed_recordings_have_no_secrets_or_request_headers():
    # リポジトリに入れた記録に、キーの形をした文字列やリクエストのヘッダーがないこと（docs/SECURITY.md）
    problems = []
    for path in sorted(RECORDINGS.rglob("*.json")):
        text = path.read_text(encoding="utf-8")
        problems += [f"{path.name}: {s[:12]}..." for s in find_secrets(text)]
        _, exchanges = load(path)
        problems += [f"{path.name}: request headers" for ex in exchanges if "headers" in ex["request"]]
    assert not problems, problems
