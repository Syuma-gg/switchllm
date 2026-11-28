"""プロバイダー共通の例外階層。

アプリ側は ``except RateLimitError`` のように書けば、どのプロバイダーでも同じ扱いになる。
"""

from __future__ import annotations

from typing import Any


class SwitchLLMError(Exception):
    """このライブラリが投げる例外の基底クラス。"""


class ConfigurationError(SwitchLLMError):
    """モデル指定の誤り、APIキー未設定など、リクエスト前に分かる設定ミス。"""


class UnsupportedFeatureError(SwitchLLMError):
    """選んだプロバイダーが対応していない機能を指定した。"""


class DroppedParameterWarning(UserWarning):
    """指定を、選んだプロバイダーに送らずに捨てた（例外ではなく警告）。

    捨てるのは、送るとどのモデルでも API が拒否し、捨てても応答の正しさが変わらない指定だけ
    （今は Anthropic の ``temperature``。docs/design-docs/dropped-parameter-warning.md）。
    ``warnings.filterwarnings("error", category=DroppedParameterWarning)`` で、送る前に止める例外にできる。
    """


class APIConnectionError(SwitchLLMError):
    """ネットワーク到達不可・タイムアウトなど。"""


class APIError(SwitchLLMError):
    """プロバイダーがエラーを返した。

    - ``message``: プロバイダーが返したエラーメッセージそのもの（``str(e)`` は前後にプロバイダー名と HTTP ステータスを付ける）
    - ``body``: エラーの本文の全体（JSON なら解釈したもの、そうでなければ文字列）
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: int | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(f"[{provider}] {message}" + (f" (HTTP {status_code})" if status_code else ""))
        self.message = message
        self.provider = provider
        self.status_code = status_code
        self.body = body


class BadRequestError(APIError):
    """400 / 413 / 422: リクエスト内容の誤り。"""


class AuthenticationError(APIError):
    """401 / 403: APIキーの誤り、権限不足。"""


class NotFoundError(APIError):
    """404: モデル名の誤りなど。"""


class RateLimitError(APIError):
    """429: レート制限。"""


class ServerError(APIError):
    """5xx（Anthropic の 529 overloaded を含む）。"""


class StreamError(APIError):
    """ストリーミング途中でプロバイダーからエラーイベントが届いた。"""


def error_for_status(status: int) -> type[APIError]:
    if status in (401, 403):
        return AuthenticationError
    if status == 404:
        return NotFoundError
    if status == 429:
        return RateLimitError
    if status >= 500:
        return ServerError
    return BadRequestError
