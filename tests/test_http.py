"""HTTP の送信・リトライ・エラーの解釈（docs/RELIABILITY.md）。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from switchllm import (
    APIConnectionError,
    APIError,
    AsyncClient,
    AuthenticationError,
    BadRequestError,
    Client,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from switchllm._http import MAX_RETRY_AFTER, RetryPolicy

ANTHROPIC_OK = {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {}}
ANTHROPIC_STREAM = [
    ("message_start", {"type": "message_start", "message": {"content": [], "usage": {}}}),
    ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "途中"}}),
]


def _client(api, **kw):
    client = api.client("anthropic:claude-sonnet-5-5", **kw)
    slept: list[float] = []
    client.retry.sleep = slept.append  # 待ち時間を記録し、実際には待たない
    return client, slept


# --------------------------------------------------------------------------- #
# 接続エラー
# --------------------------------------------------------------------------- #
def test_connection_error_is_retried_then_succeeds(api):
    api.fail(httpx.ConnectError("refused"))
    api.reply_json(ANTHROPIC_OK)
    client, slept = _client(api)
    assert client.chat("hi").text == "ok"
    assert len(api.requests) == 2 and len(slept) == 1


def test_connection_error_after_retries_becomes_api_connection_error(api):
    for _ in range(3):
        api.fail(httpx.ConnectError("refused"))
    client, _ = _client(api, max_retries=2)
    with pytest.raises(APIConnectionError, match=r"\[anthropic\].*ConnectError") as e:
        client.chat("hi")
    assert isinstance(e.value.__cause__, httpx.ConnectError)  # 元の例外を辿れる
    assert len(api.requests) == 3


@pytest.mark.parametrize(
    "exc",
    [httpx.ConnectTimeout("connect timed out"), httpx.PoolTimeout("no connection"), httpx.ReadError("reset")],
    ids=["connect", "pool", "read-error"],
)
def test_transport_errors_other_than_read_timeout_are_still_retried(api, exc):
    api.fail(exc)
    api.reply_json(ANTHROPIC_OK)
    client, slept = _client(api)
    assert client.chat("hi").text == "ok"
    assert len(api.requests) == 2 and len(slept) == 1


def test_read_timeout_is_not_retried(api):
    # 受信の待ちを超えたのは、API がまだ生成している最中のことが多い。送り直すと同じ生成を重ねて頼み、
    # 待ちも料金も重なるので、リトライせずに知らせる（docs/design-docs/read-timeout.md）
    api.fail(httpx.ReadTimeout("timed out"))
    client, slept = _client(api, max_retries=2)
    with pytest.raises(APIConnectionError) as e:
        client.chat("hi")
    assert len(api.requests) == 1 and slept == []
    assert isinstance(e.value.__cause__, httpx.ReadTimeout)
    text = str(e.value)
    assert text.startswith("[anthropic]") and "ReadTimeout" in text
    assert "not retried" in text.lower() and "stream()" in text and "timeout=" in text


def test_stream_read_timeout_before_first_event_is_not_retried(api):
    api.fail(httpx.ReadTimeout("timed out"))
    client, slept = _client(api, max_retries=2)
    with pytest.raises(APIConnectionError, match="(?i)not retried.*timeout="):
        list(client.stream("hi"))
    assert len(api.requests) == 1 and slept == []


def test_default_timeout_waits_600_seconds_for_a_response():
    # 推論の長い呼び出しが切れないよう、公式の OpenAI・Anthropic の SDK と同じ 10 分にする。接続は 10 秒のまま
    client = Client("anthropic:claude-sonnet-5-5", api_key="k")
    try:
        timeout = client._http.timeout
        assert (timeout.read, timeout.write, timeout.pool, timeout.connect) == (600.0, 600.0, 600.0, 10.0)
    finally:
        client.close()
    aclient = AsyncClient("anthropic:claude-sonnet-5-5", api_key="k")
    try:
        assert aclient._http.timeout.read == 600.0 and aclient._http.timeout.connect == 10.0
    finally:
        asyncio.run(aclient.aclose())


def test_timeout_argument_still_changes_the_wait():
    client = Client("anthropic:claude-sonnet-5-5", api_key="k", timeout=30)
    try:
        assert client._http.timeout.read == 30 and client._http.timeout.connect == 10.0
    finally:
        client.close()


def test_stream_connection_error_before_first_event_is_retried(api):
    api.fail(httpx.ConnectError("refused"))
    api.reply_sse([*ANTHROPIC_STREAM, ("message_stop", {"type": "message_stop"})])
    client, _ = _client(api)
    events = list(client.stream("hi"))
    assert events[-1].response.text == "途中" and len(api.requests) == 2


def test_stream_disconnect_after_first_event_is_not_retried(api):
    # 途中まで受け取ってから送り直すと、同じ本文が2回届くので、そのまま失敗させる
    api.reply_sse_then_disconnect(ANTHROPIC_STREAM)
    client, _ = _client(api)
    received = []
    with pytest.raises(APIConnectionError, match="ReadError"):
        for ev in client.stream("hi"):
            received.append(ev.text)
    assert received == ["途中"] and len(api.requests) == 1


# --------------------------------------------------------------------------- #
# HTTP のエラー
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("status", "cls", "retried"),
    [
        (400, BadRequestError, False),
        (403, AuthenticationError, False),
        (404, NotFoundError, False),
        (413, BadRequestError, False),
        (422, BadRequestError, False),
        (408, BadRequestError, True),
        (429, RateLimitError, True),
        (529, ServerError, True),  # Anthropic の overloaded
    ],
)
def test_status_maps_to_exception_and_retry(api, status, cls, retried):
    for _ in range(2):
        api.reply_json({"error": {"message": "nope"}}, status=status)
    client, _ = _client(api, max_retries=1)
    with pytest.raises(cls, match="nope") as e:
        client.chat("hi")
    assert e.value.status_code == status and e.value.body == {"error": {"message": "nope"}}
    assert len(api.requests) == (2 if retried else 1)


def test_api_error_message_is_passed_through_unchanged(api):
    # Anthropic が強制ツール呼び出しを拒否したときの応答（docs/references/anthropic-messages.md の出典の形）
    body = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": 'tool_choice: type "tool" and "any" are not supported for this model.',
        },
        "request_id": "req_011CSHoEeqs5C35K2UUqR7Fy",
    }
    api.reply_json(body, status=400)
    client, _ = _client(api)
    with pytest.raises(BadRequestError) as e:
        client.chat("hi")
    assert e.value.message == 'tool_choice: type "tool" and "any" are not supported for this model.'
    assert str(e.value) == f"[anthropic] {e.value.message} (HTTP 400)"
    assert e.value.body == body  # エラーの種類や request_id も含めて、本文の全体が残る


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"error": "plain string"}, "plain string"),  # error が文字列
        ({"message": "top level"}, "top level"),  # message が直下
        ([{"error": {"message": "in a list"}}], "in a list"),  # 配列で包まれている
    ],
)
def test_error_message_is_taken_from_body(api, body, message):
    api.reply_json(body, status=400)
    client, _ = _client(api)
    with pytest.raises(BadRequestError) as e:
        client.chat("hi")
    assert e.value.message == message and e.value.body == body


@pytest.mark.parametrize("content", [b'{"detail": "not here"}', b"[]"])
def test_unknown_error_shape_uses_body_text_as_is(api, content):
    # 理由句（Bad Request）だけにせず、API が返した本文を見せる。JSON の書き方は httpx の版で変わるので、本文はバイト列で積む
    api.reply_raw(content, status=400, headers={"content-type": "application/json"})
    client, _ = _client(api)
    with pytest.raises(BadRequestError) as e:
        client.chat("hi")
    assert e.value.message == content.decode()


def test_error_message_from_text_body_is_truncated(api):
    api.reply_raw(("x" * 600).encode(), status=400)
    client, _ = _client(api)
    with pytest.raises(BadRequestError) as e:
        client.chat("hi")
    assert "x" * 500 in str(e.value) and "x" * 501 not in str(e.value)
    assert e.value.body == "x" * 600  # body には全文が残る


def test_empty_error_body_uses_reason_phrase(api):
    api.reply_raw(b"", status=404)
    client, _ = _client(api)
    with pytest.raises(NotFoundError, match=r"Not Found \(HTTP 404\)"):
        client.chat("hi")


def test_stream_non_retryable_error_is_raised_with_body_message(api):
    api.reply_json({"error": {"message": "bad model"}}, status=400)
    client, _ = _client(api)
    with pytest.raises(BadRequestError, match="bad model"):
        list(client.stream("hi"))
    assert len(api.requests) == 1


@pytest.mark.parametrize("content", [b"<html>proxy error</html>", b"[1, 2]"])
def test_success_status_without_json_object_is_api_error(api, content):
    # プロキシが 200 で HTML を返したときなど。JSONDecodeError ではなく、このライブラリの例外にする
    api.reply_raw(content, status=200)
    client, _ = _client(api)
    with pytest.raises(APIError, match="expected a JSON object") as e:
        client.chat("hi")
    assert e.value.status_code == 200 and e.value.body == content.decode()


# --------------------------------------------------------------------------- #
# 待ち時間
# --------------------------------------------------------------------------- #
def _response(retry_after: str | None) -> httpx.Response:
    return httpx.Response(429, headers={"retry-after": retry_after} if retry_after is not None else {})


@pytest.mark.parametrize(
    ("value", "expected"),
    [("2.5", 2.5), ("0", 0.0), ("120", MAX_RETRY_AFTER), ("-1", 0.0)],
)
def test_retry_after_seconds_are_clamped(value, expected):
    assert RetryPolicy(backoff=1.0).delay(0, _response(value)) == expected


@pytest.mark.parametrize("value", [None, "", "nan", "inf", "Wed, 21 Oct 2026 07:28:00 GMT"])
def test_unreadable_retry_after_falls_back_to_backoff(value):
    # 秒数として読めないもの（HTTP-date 形式など）は、通常のバックオフ（1秒 × 2^0 ± 25%）になる
    assert 0.75 <= RetryPolicy(backoff=1.0).delay(0, _response(value)) <= 1.25


def test_backoff_doubles_with_jitter_and_is_capped():
    policy = RetryPolicy(backoff=0.5)
    for attempt, base in [(0, 0.5), (1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (10, 8.0)]:  # 上限は 8 秒
        assert base * 0.75 <= policy.delay(attempt, None) <= base * 1.25
    assert RetryPolicy(backoff=0).delay(3, None) == 0.0


def test_negative_retry_after_does_not_crash_the_retry(api):
    # 以前は time.sleep(-1) が ValueError を投げていた
    api.reply_json({"error": {"message": "slow"}}, status=429, headers={"retry-after": "-1"})
    api.reply_json(ANTHROPIC_OK)
    client, slept = _client(api)
    assert client.chat("hi").text == "ok"
    assert slept == [0.0]
