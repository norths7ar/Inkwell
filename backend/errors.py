"""Safe, stable error information shared by persistence and public interfaces."""

from dataclasses import dataclass
from typing import Literal

from backend.llm.errors import LLMError

type ErrorCode = Literal[
    "auth",
    "quota",
    "rate_limit",
    "context_overflow",
    "invalid_request",
    "server",
    "timeout",
    "network",
    "protocol",
    "cancelled",
    "iteration_limit",
    "internal",
    "unknown",
]

_MESSAGES: dict[ErrorCode, str] = {
    "auth": "鉴权失败，请检查提供商凭据和访问权限。",
    "quota": "提供商额度不足，请检查账户余额或配额。",
    "rate_limit": "请求受到限流，请稍后重试。",
    "context_overflow": "上下文超过模型限制，请缩短输入或选择上下文更大的模型。",
    "invalid_request": "提供商拒绝了请求，请检查模型与请求参数。",
    "server": "模型服务暂时不可用，请稍后重试。",
    "timeout": "模型请求超时，请稍后重试。",
    "network": "无法连接模型服务，请检查网络与提供商地址。",
    "protocol": "模型响应不符合预期协议，请检查模型协议配置。",
    "cancelled": "运行已取消。",
    "iteration_limit": "已达到本次运行的工具迭代上限，请缩小任务后继续。",
    "internal": "运行遇到内部错误，请重试或检查本地服务。",
    "unknown": "运行失败，旧记录未保存具体错误分类。",
}


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    category: ErrorCode
    message: str
    retryable: bool = False
    status_code: int | None = None
    retry_after_seconds: float | None = None


def safe_error(
    category: ErrorCode = "internal",
    *,
    retryable: bool = False,
    status_code: int | None = None,
    retry_after_seconds: float | None = None,
) -> ErrorInfo:
    return ErrorInfo(category, _MESSAGES[category], retryable, status_code, retry_after_seconds)


def exception_error(exc: Exception) -> ErrorInfo:
    if not isinstance(exc, LLMError):
        return safe_error()
    # Match known codes, never expose upstream messages or raw response bodies.
    for category in _MESSAGES:
        if category == exc.category.value:
            return safe_error(
                category,
                retryable=exc.retryable,
                status_code=exc.status_code,
                retry_after_seconds=exc.retry_after_seconds,
            )
    return safe_error()
