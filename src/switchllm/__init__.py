"""switchllm — どの LLM プロバイダーでも同じコードで呼べる薄いクライアント。

    from switchllm import Client
    client = Client("anthropic:claude-sonnet-5-5")   # ← ここだけ変える
    print(client.chat("こんにちは").text)
"""

from .client import MODEL_ENV, AsyncClient, Client, parse_model
from .errors import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    BadRequestError,
    ConfigurationError,
    DroppedParameterWarning,
    NotFoundError,
    RateLimitError,
    ServerError,
    StreamError,
    SwitchLLMError,
    UnsupportedFeatureError,
)
from .providers import register_provider
from .tools import Tool, arun_tool_calls, run_tool_calls, tool
from .types import FilePart, ImagePart, Message, Response, StreamEvent, TextPart, ToolCall, Usage

__version__ = "0.1.0"  # 版の正本。pyproject.toml はここを読む

__all__ = [
    "APIConnectionError",
    "APIError",
    "AsyncClient",
    "AuthenticationError",
    "BadRequestError",
    "Client",
    "ConfigurationError",
    "DroppedParameterWarning",
    "FilePart",
    "ImagePart",
    "MODEL_ENV",
    "Message",
    "NotFoundError",
    "RateLimitError",
    "Response",
    "ServerError",
    "StreamError",
    "StreamEvent",
    "SwitchLLMError",
    "TextPart",
    "Tool",
    "ToolCall",
    "UnsupportedFeatureError",
    "Usage",
    "arun_tool_calls",
    "parse_model",
    "register_provider",
    "run_tool_calls",
    "tool",
]
