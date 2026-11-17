"""実 API の応答の記録と再生（docs/testing.md）。

- 記録（``Recorder``）: E2E テストを実 API で回したときに、HTTP のやりとりをそのまま残す。
  リクエストのヘッダー（APIキーが入る）と URL の ``key`` は残さない。応答のヘッダーは
  ``content-type`` と ``retry-after`` だけを残す。保存するときは、キーの値を ``<redacted>`` に置き換える
- 再生（``Replayer``）: 記録した応答を順に返す。ライブラリが送るリクエストが記録と違えば失敗する。
  記録は実 API が受け付けたやりとりなので、違うリクエストを送るようになったら実 API で確かめ直す
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx

RECORDINGS = Path(__file__).parent / "recordings"

#: 再生に要る応答のヘッダー。ほかは残さない（組織の ID やリクエストの ID が入るため）
KEEP_RESPONSE_HEADERS = ("content-type", "retry-after")

#: Gemini が id を返さなかった呼び出しに振る仮 ID（providers/gemini.py）。実行ごとに変わるので比べない
_SYNTHETIC_ID = re.compile(r"switchllm_[0-9a-f]{12}")

#: APIキーの形。記録に残っていないかを調べる（tests/test_recording.py）。
#: 署名や暗号化された推論（base64）の途中にたまたま現れたものは数えないよう、直前が base64 の文字でないものに限る
_SECRET_SHAPES = re.compile(
    r"(?<![A-Za-z0-9+/_\-])"
    r"(?:sk-ant-[A-Za-z0-9_\-]{20,}"  # Anthropic
    r"|sk-(?:proj-|live-)?[A-Za-z0-9_\-]{20,}"  # OpenAI・DeepSeek
    r"|AIza[0-9A-Za-z_\-]{30,}"  # Google
    r"|Bearer\s+[A-Za-z0-9._\-]{20,})"
)

#: これより短い値は置き換えない（記録のあちこちを壊すため）
_MIN_SECRET_LENGTH = 8


def _describe_request(request: httpx.Request) -> dict[str, Any]:
    body = request.content
    return {
        "method": request.method,
        "url": str(request.url.copy_remove_param("key")),
        "json": json.loads(body) if body else None,
    }


def _describe_response(status: int, headers: httpx.Headers, content: bytes) -> dict[str, Any]:
    ctype = headers.get("content-type", "")
    out: dict[str, Any] = {"status": status, "content_type": ctype}
    if headers.get("retry-after") is not None:
        out["retry_after"] = headers["retry-after"]
    text = content.decode("utf-8", errors="replace")
    if "json" in ctype:
        try:
            out["json"] = json.loads(text)
            return out
        except json.JSONDecodeError:
            pass
    out["text"] = text
    return out


class Recorder(httpx.BaseTransport):
    """本物の transport の前に置き、やりとりを ``exchanges`` に残す。

    ストリーミングの応答も、最後まで読んでから返す（記録のため。テストでは差分の届く速さは見ない）。"""

    def __init__(self, inner: httpx.BaseTransport) -> None:
        self.inner = inner
        self.exchanges: list[dict[str, Any]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.inner.handle_request(request)
        try:
            content = response.read()  # content-encoding（gzip など）を解いた本文
        finally:
            response.close()
        self.exchanges.append({
            "request": _describe_request(request),
            "response": _describe_response(response.status_code, response.headers, content),
        })
        headers = {k: v for k, v in response.headers.items() if k.lower() in KEEP_RESPONSE_HEADERS}
        return httpx.Response(response.status_code, headers=headers, content=content, request=request)

    def close(self) -> None:
        self.inner.close()


def _canonical(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1)
    return _SYNTHETIC_ID.sub("switchllm_<id>", text)


class Replayer:
    """``httpx.MockTransport`` に渡す。記録した応答を順に返し、リクエストが記録と同じかを確かめる。"""

    def __init__(self, exchanges: Iterable[dict[str, Any]]) -> None:
        self._queue = list(exchanges)

    def unused(self) -> int:
        return len(self._queue)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if not self._queue:
            raise AssertionError(
                "the library made more requests than recorded. The recording is out of date: "
                "re-record it against the real API with --models ... --record (costs money)"
            )
        exchange = self._queue.pop(0)
        expected, actual = _canonical(exchange["request"]), _canonical(_describe_request(request))
        if expected != actual:
            diff = "\n".join(difflib.unified_diff(
                expected.splitlines(), actual.splitlines(), "recorded", "sent now", lineterm="", n=2))
            raise AssertionError(
                "the request differs from the recording, so the real API has not seen it. "
                "If the change is intended, re-record against the real API with --models ... --record "
                f"(costs money).\n{diff[:4000]}"
            )
        res = exchange["response"]
        content = json.dumps(res["json"], ensure_ascii=False).encode() if "json" in res else res["text"].encode()
        headers = {"content-type": res["content_type"]}
        if "retry_after" in res:
            headers["retry-after"] = res["retry_after"]
        return httpx.Response(res["status"], headers=headers, content=content)


# --------------------------------------------------------------------------- #
# 秘密の扱い
# --------------------------------------------------------------------------- #
def scrub(text: str, secrets: Iterable[str]) -> str:
    """キーの値を ``<redacted>`` に置き換える。短い値は置き換えない。"""
    for secret in sorted({s for s in secrets if s and len(s) >= _MIN_SECRET_LENGTH}, key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    return text


def find_secrets(text: str) -> list[str]:
    """APIキーの形をした文字列を返す（記録に残っていないかの検査用）。"""
    return _SECRET_SHAPES.findall(text)


# --------------------------------------------------------------------------- #
# ファイル
# --------------------------------------------------------------------------- #
def recording_path(root: Path, module: str, test_name: str) -> Path:
    """テストの記録を置く場所。Windows でファイル名に使えない文字（: > など）は置き換える。"""
    safe = re.sub(r"[^A-Za-z0-9._\-\[\]]", "_", test_name.replace("->", "__to__"))
    return root / module / f"{safe}.json"


def save(path: Path, meta: dict[str, Any], exchanges: list[dict[str, Any]], *, secrets: Iterable[str]) -> None:
    data = {**meta, "exchanges": exchanges}
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scrub(text, secrets), encoding="utf-8", newline="\n")


def load(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    exchanges = data.pop("exchanges")
    return data, exchanges
