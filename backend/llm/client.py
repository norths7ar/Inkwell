"""Protocol-first httpx client for streaming, completion, and model discovery."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from types import TracebackType

import httpx

from backend.llm._json import (
    JsonObject,
    JsonValue,
    as_list,
    as_object,
    get_int,
    get_str,
    parse_json_object,
)
from backend.llm.errors import (
    ErrorCategory,
    LLMError,
    classify_http_error,
    classify_transport_error,
)
from backend.llm.protocols import anthropic_messages, google_genai, openai_chat, openai_responses
from backend.llm.protocols._shared import build_auth_headers, join_endpoint_path
from backend.llm.types import (
    ContentBlock,
    DiscoveredModel,
    DoneEvent,
    EndpointConfig,
    GenerateRequest,
    GenerateResponse,
    ReasoningBlock,
    ReasoningDeltaEvent,
    StreamEvent,
    TextBlock,
    TextDeltaEvent,
    TokenUsage,
    ToolCallBlock,
    ToolCallDeltaEvent,
    UsageEvent,
    WireProtocol,
)

type _RequestBuilder = Callable[
    [EndpointConfig, GenerateRequest], tuple[str, dict[str, str], JsonObject]
]
type _StreamDecoder = Callable[[httpx.Response], AsyncIterator[StreamEvent]]

_PROTOCOL_DRIVERS: dict[WireProtocol, tuple[_RequestBuilder, _StreamDecoder]] = {
    WireProtocol.GOOGLE_GENAI: (google_genai.build_request, google_genai.decode_stream),
    WireProtocol.OPENAI_CHAT: (openai_chat.build_request, openai_chat.decode_stream),
    WireProtocol.OPENAI_RESPONSES: (openai_responses.build_request, openai_responses.decode_stream),
    WireProtocol.ANTHROPIC_MESSAGES: (
        anthropic_messages.build_request,
        anthropic_messages.decode_stream,
    ),
}


@dataclass(slots=True)
class _ToolCallAccumulator:
    call_id: str = ""
    name: str = ""
    arguments_parts: list[str] = field(default_factory=list)
    signature_parts: list[str] = field(default_factory=list)
    block_index: int = 0


class LLMClient:
    """Thin shared httpx client supporting multi-protocol streaming and discovery."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        connect_timeout: float = 10.0,
        read_timeout: float = 120.0,
    ) -> None:
        self._owns_client = client is None
        self._client = (
            client
            if client is not None
            else httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=connect_timeout,
                    read=read_timeout,
                    write=30.0,
                    pool=10.0,
                ),
                transport=transport,
            )
        )

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def http_client(self) -> httpx.AsyncClient:
        """Expose the shared httpx client for auxiliary metadata requests."""
        return self._client

    async def stream(
        self,
        endpoint: EndpointConfig,
        request: GenerateRequest,
    ) -> AsyncIterator[StreamEvent]:
        """Stream normalized events using request.protocol_override or endpoint default."""
        protocol = request.protocol_override or endpoint.default_protocol
        driver = _PROTOCOL_DRIVERS.get(protocol)
        if driver is None:
            raise LLMError(
                ErrorCategory.PROTOCOL,
                f"Wire protocol '{protocol.value}' is not implemented",
                retryable=False,
            )

        build_req, decode = driver
        url, headers, payload = build_req(endpoint, request)

        try:
            async with self._client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code >= 400:
                    raw_bytes = await response.aread()
                    body_text = raw_bytes.decode("utf-8", errors="replace")
                    classify = (
                        google_genai.classify_error
                        if protocol == WireProtocol.GOOGLE_GENAI
                        else classify_http_error
                    )
                    raise classify(response.status_code, body_text, response.headers)
                async for event in decode(response):
                    yield event
        except httpx.HTTPError as exc:
            raise classify_transport_error(exc) from exc

    async def complete(
        self,
        endpoint: EndpointConfig,
        request: GenerateRequest,
    ) -> GenerateResponse:
        """Consume a stream and coalesce ordered blocks, usage, and finish_reason."""
        blocks: list[ContentBlock] = []
        reasoning_parts: list[str] = []
        signature_parts: list[str] = []
        text_parts: list[str] = []
        text_signatures: list[str] = []
        tool_calls: dict[int, _ToolCallAccumulator] = {}
        usage = TokenUsage()
        finish_reason: str | None = None
        signature_protocol: WireProtocol | None = None

        def flush_reasoning() -> None:
            nonlocal signature_protocol
            if reasoning_parts or signature_parts:
                blocks.append(
                    ReasoningBlock(
                        text="".join(reasoning_parts),
                        signature="".join(signature_parts) if signature_parts else None,
                        signature_protocol=signature_protocol,
                    )
                )
                reasoning_parts.clear()
                signature_parts.clear()
                signature_protocol = None

        def flush_text() -> None:
            if text_parts or text_signatures:
                blocks.append(
                    TextBlock("".join(text_parts), signature="".join(text_signatures) or None)
                )
                text_parts.clear()
                text_signatures.clear()

        async for event in self.stream(endpoint, request):
            if isinstance(event, ReasoningDeltaEvent):
                flush_text()
                if event.part_start:
                    flush_reasoning()
                if event.delta:
                    reasoning_parts.append(event.delta)
                if event.signature_delta:
                    signature_parts.append(event.signature_delta)
                if event.signature_protocol is not None:
                    signature_protocol = event.signature_protocol
            elif isinstance(event, TextDeltaEvent):
                flush_reasoning()
                if event.part_start:
                    flush_text()
                if event.delta:
                    text_parts.append(event.delta)
                if event.signature_delta:
                    text_signatures.append(event.signature_delta)
            elif isinstance(event, ToolCallDeltaEvent):
                flush_reasoning()
                flush_text()
                if event.index not in tool_calls:
                    tool_calls[event.index] = _ToolCallAccumulator(block_index=len(blocks))
                    blocks.append(ToolCallBlock("", "", ""))
                acc = tool_calls[event.index]
                if event.call_id and not acc.call_id:
                    acc.call_id = event.call_id
                if event.name and not acc.name:
                    acc.name = event.name
                if event.arguments_delta:
                    acc.arguments_parts.append(event.arguments_delta)
                if event.signature_delta:
                    acc.signature_parts.append(event.signature_delta)
            elif isinstance(event, UsageEvent):
                usage = event.usage
            elif isinstance(event, DoneEvent):
                finish_reason = event.finish_reason

        flush_reasoning()
        flush_text()
        for idx in sorted(tool_calls):
            acc = tool_calls[idx]
            blocks[acc.block_index] = ToolCallBlock(
                call_id=acc.call_id,
                name=acc.name,
                arguments_json="".join(acc.arguments_parts),
                signature="".join(acc.signature_parts) or None,
            )

        return GenerateResponse(
            blocks=tuple(blocks),
            usage=usage,
            finish_reason=finish_reason,
        )

    async def list_models(self, endpoint: EndpointConfig) -> list[DiscoveredModel]:
        """Fetch available model IDs from discovery_url or <base_url>/models."""
        if endpoint.default_protocol == WireProtocol.GOOGLE_GENAI:
            return await self._list_google_models(endpoint)
        url = (
            endpoint.discovery_url.strip()
            if endpoint.discovery_url.strip()
            else join_endpoint_path(endpoint.base_url, "/models")
        )
        headers = build_auth_headers(endpoint, include_content_type=False)
        if endpoint.default_protocol == WireProtocol.ANTHROPIC_MESSAGES:
            headers.setdefault("anthropic-version", anthropic_messages.ANTHROPIC_VERSION)

        try:
            response = await self._client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise classify_transport_error(exc) from exc

        if response.status_code >= 400:
            raise classify_http_error(response.status_code, response.text, response.headers)

        try:
            payload = parse_json_object(response.text)
        except ValueError as exc:
            raise LLMError(
                ErrorCategory.PROTOCOL,
                f"Malformed JSON in models response: {exc}",
                retryable=False,
                raw_body=response.text,
            ) from exc

        items = as_list(payload.get("data")) or as_list(payload.get("models")) or []
        models: list[DiscoveredModel] = []
        seen: set[str] = set()
        for item in items:
            obj = as_object(item)
            if obj is None:
                continue
            raw_id = get_str(obj, "id") or get_str(obj, "name")
            if not raw_id:
                continue
            model_id = raw_id.removeprefix("models/").strip()
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            display_name = (
                get_str(obj, "display_name")
                or get_str(obj, "displayName")
                or (get_str(obj, "name") if get_str(obj, "id") else None)
                or model_id
            )
            owned_by = get_str(obj, "owned_by") or ""
            context_window, max_output_tokens = _extract_model_limits(obj)
            disclosed_protocol, protocol_error = _extract_disclosed_protocol(obj)
            supported, unsupported_reason = _classify_model_support(model_id, obj)
            if protocol_error:
                supported, unsupported_reason = False, protocol_error
            models.append(
                DiscoveredModel(
                    model_id=model_id,
                    display_name=display_name,
                    owned_by=owned_by,
                    context_window=context_window,
                    max_output_tokens=max_output_tokens,
                    recommended_protocol=disclosed_protocol,
                    supported=supported,
                    unsupported_reason=unsupported_reason,
                )
            )
        models.sort(key=lambda item: not item.supported)
        return models

    async def _list_google_models(self, endpoint: EndpointConfig) -> list[DiscoveredModel]:
        url = httpx.URL(
            endpoint.discovery_url.strip() or join_endpoint_path(endpoint.base_url, "/models")
        )
        headers = google_genai.auth_headers(endpoint, include_content_type=False)
        models: list[DiscoveredModel] = []
        seen_tokens: set[str] = set()
        while True:
            try:
                response = await self._client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                raise classify_transport_error(exc) from exc
            if response.status_code >= 400:
                raise google_genai.classify_error(
                    response.status_code, response.text, response.headers
                )
            try:
                payload = parse_json_object(response.text)
            except ValueError as exc:
                raise LLMError(ErrorCategory.PROTOCOL, "Malformed Gemini models response") from exc
            items = as_list(payload.get("models"))
            if items is None:
                raise LLMError(ErrorCategory.PROTOCOL, "Gemini models response is missing models")
            for item in items:
                model = as_object(item) or {}
                methods = as_list(model.get("supportedGenerationMethods")) or []
                name = get_str(model, "name")
                if name and "generateContent" in methods:
                    model_id = name.removeprefix("models/")
                    context_window, max_output_tokens = _extract_model_limits(model)
                    models.append(
                        DiscoveredModel(
                            model_id=model_id,
                            display_name=get_str(model, "displayName") or model_id,
                            owned_by="google",
                            context_window=context_window,
                            max_output_tokens=max_output_tokens,
                        )
                    )
            token = get_str(payload, "nextPageToken")
            if not token:
                return models
            if token in seen_tokens:
                raise LLMError(ErrorCategory.PROTOCOL, "Repeated Gemini model-list page token")
            seen_tokens.add(token)
            url = url.copy_set_param("pageToken", token)


_AUDIO_MODEL_RE = re.compile(
    r"^(?:tts-|whisper-|gpt-live-transcribe(?:-|$)"
    r"|(?:gpt-\d[^/]*|gpt)-(?:audio|realtime|transcribe|tts)(?:-|$)"
    r"|mimo-(?:v\d+(?:\.\d+)*-)?(?:asr|tts)(?:-|$))",
    re.IGNORECASE,
)
_NON_CHAT_MODEL_RE = re.compile(
    r"^(?:(?:chatgpt|gpt)-image(?:-|$)|gpt-live(?:-|$)|dall-e-"
    r"|text-embedding-|(?:omni|text)-moderation-)",
    re.IGNORECASE,
)
_GPT_LIVE_RE = re.compile(r"^gpt-live-(?!transcribe)", re.IGNORECASE)


def _positive(value: JsonValue | None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _extract_model_limits(obj: JsonObject) -> tuple[int | None, int | None]:
    """Extract context_window and max_output_tokens from common provider listing formats."""
    limit_obj = as_object(obj.get("limit"))
    top_provider = as_object(obj.get("top_provider"))
    context_window = (
        _positive(limit_obj.get("context") if limit_obj else None)
        or _positive(obj.get("context_window"))
        or _positive(obj.get("contextWindow"))
        or _positive(obj.get("context_length"))
        or _positive(obj.get("inputTokenLimit"))
        or (get_int(top_provider, "context_length") if top_provider else None)
    )
    max_output = (
        _positive(limit_obj.get("output") if limit_obj else None)
        or _positive(obj.get("max_output_tokens"))
        or _positive(obj.get("maxTokens"))
        or _positive(obj.get("outputTokenLimit"))
        or _positive(top_provider.get("max_completion_tokens") if top_provider else None)
    )
    return (
        context_window if context_window and context_window > 0 else None,
        max_output if max_output and max_output > 0 else None,
    )


def _extract_disclosed_protocol(obj: JsonObject) -> tuple[WireProtocol | None, str]:
    """Honor an explicit wire protocol disclosed directly by the provider's /models response."""
    raw = (get_str(obj, "protocol") or get_str(obj, "api") or "").strip().lower()
    if not raw:
        return None, ""
    mapping = {
        "openai-chat": WireProtocol.OPENAI_CHAT,
        "openai-completions": WireProtocol.OPENAI_CHAT,
        "openai-responses": WireProtocol.OPENAI_RESPONSES,
        "anthropic-messages": WireProtocol.ANTHROPIC_MESSAGES,
        "google-genai": WireProtocol.GOOGLE_GENAI,
        "google-generative-ai": WireProtocol.GOOGLE_GENAI,
    }
    protocol = mapping.get(raw)
    if protocol is None:
        return None, f"服务端声明了不支持的协议：{raw[:200]}"
    return protocol, ""


def _classify_model_support(model_id: str, obj: JsonObject) -> tuple[bool, str]:
    raw_type = (get_str(obj, "type") or "").strip().lower()
    methods = as_list(obj.get("supportedGenerationMethods"))
    is_audio = raw_type in ("asr", "tts", "speech", "audio") or bool(
        _AUDIO_MODEL_RE.search(model_id)
    )
    unsupported = (
        (bool(raw_type) and raw_type not in ("chat", "text", "language", "model"))
        or is_audio
        or bool(_NON_CHAT_MODEL_RE.search(model_id))
        or (methods is not None and "generateContent" not in methods)
    )
    if not unsupported:
        return True, ""
    if _GPT_LIVE_RE.search(model_id):
        return False, "此型号的实时接口尚未适配"
    return False, "非对话模型，不支持接入"
