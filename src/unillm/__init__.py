"""unillm — どの LLM プロバイダーでも同じコードで呼べる薄いクライアント。

    from unillm import Client
    client = Client("anthropic:claude-sonnet-5-5")   # ← ここだけ変える
    print(client.chat("こんにちは").text)
"""

from .client import MODEL_ENV, Client, parse_model
from .errors import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    BadRequestError,
    ConfigurationError,
    NotFoundError,
    RateLimitError,
    ServerError,
    StreamError,
    UniLLMError,
    UnsupportedFeatureError,
)
from .providers import register_provider
from .tools import Tool, run_tool_calls, tool
from .types import ImagePart, Message, Response, StreamEvent, TextPart, ToolCall, Usage

__version__ = "0.1.0"

__all__ = [
    "APIConnectionError",
    "APIError",
    "AuthenticationError",
    "BadRequestError",
    "Client",
    "ConfigurationError",
    "ImagePart",
    "MODEL_ENV",
    "Message",
    "NotFoundError",
    "RateLimitError",
    "Response",
    "ServerError",
    "StreamError",
    "StreamEvent",
    "TextPart",
    "Tool",
    "ToolCall",
    "UniLLMError",
    "UnsupportedFeatureError",
    "Usage",
    "parse_model",
    "register_provider",
    "run_tool_calls",
    "tool",
]
