"""Pure-Python async Agent Loop with single-stream persistence and event fan-out."""

from __future__ import annotations

import asyncio
import json
from contextlib import closing
from dataclasses import dataclass, field

from backend.agent.context import build_context
from backend.agent.events import (
    AttemptResetEvent,
    EventSink,
    PendingChangeCreatedEvent,
    RunFinishedEvent,
    RunStartedEvent,
    ToolFinishedEvent,
    ToolStartedEvent,
    WaitingUserEvent,
    emit_event,
)
from backend.agent.events import (
    ReasoningDeltaEvent as AgentReasoningDeltaEvent,
)
from backend.agent.events import (
    TextDeltaEvent as AgentTextDeltaEvent,
)
from backend.agent.serialization import serialize_blocks
from backend.agent.tools import (
    BoundTool,
    ToolContext,
    ToolExecutionResult,
    ToolFailure,
    tools_for_mode,
)
from backend.errors import ErrorInfo, exception_error, safe_error
from backend.llm.client import LLMClient
from backend.llm.errors import LLMError
from backend.llm.types import (
    ContentBlock,
    DoneEvent,
    EndpointConfig,
    GenerateRequest,
    GenerateResponse,
    ReasoningBlock,
    ReasoningDeltaEvent,
    TextBlock,
    TextDeltaEvent,
    TokenUsage,
    ToolCallBlock,
    ToolCallDeltaEvent,
    ToolResultBlock,
    UsageEvent,
    WireProtocol,
)
from backend.storage.db import Database
from backend.storage.models import (
    Book,
    MessageRole,
    Run,
    RunStatus,
    Session,
)
from backend.storage.repos.content import get_book
from backend.storage.repos.sessions import (
    append_message,
    create_run,
    finish_run,
    get_run,
    get_session,
    set_run_waiting_user,
)


class _TurnCancelledError(Exception):
    """Internal signal used when cancel_event triggers mid-stream."""


@dataclass(slots=True)
class _ToolAcc:
    call_id: str = ""
    name: str = ""
    arguments_parts: list[str] = field(default_factory=list)


class AgentRunner:
    """Explicit async ReAct runner replacing heavy graph and checkpointer frameworks."""

    def __init__(
        self,
        db: Database,
        llm_client: LLMClient,
        *,
        max_iterations: int = 12,
        max_llm_retries: int = 2,
        max_tool_concurrency: int = 8,
    ) -> None:
        self.db = db
        self.llm_client = llm_client
        self.max_iterations = max_iterations
        self.max_llm_retries = max_llm_retries
        self.max_tool_concurrency = max_tool_concurrency

    async def _stream_once(
        self,
        endpoint: EndpointConfig,
        request: GenerateRequest,
        *,
        run_id: str,
        sink: EventSink | None,
        cancel_event: asyncio.Event | None,
    ) -> GenerateResponse:
        blocks: list[ContentBlock] = []
        reasoning_parts: list[str] = []
        signature_parts: list[str] = []
        text_parts: list[str] = []
        tool_calls: dict[int, _ToolAcc] = {}
        usage = TokenUsage()
        finish_reason: str | None = None

        def flush_reasoning() -> None:
            if reasoning_parts or signature_parts:
                blocks.append(
                    ReasoningBlock(
                        text="".join(reasoning_parts),
                        signature="".join(signature_parts) if signature_parts else None,
                    )
                )
                reasoning_parts.clear()
                signature_parts.clear()

        def flush_text() -> None:
            if text_parts:
                blocks.append(TextBlock("".join(text_parts)))
                text_parts.clear()

        async for event in self.llm_client.stream(endpoint, request):
            if cancel_event is not None and cancel_event.is_set():
                raise _TurnCancelledError

            if isinstance(event, ReasoningDeltaEvent):
                flush_text()
                if event.delta:
                    reasoning_parts.append(event.delta)
                    await emit_event(sink, AgentReasoningDeltaEvent(run_id, event.delta))
                if event.signature_delta:
                    signature_parts.append(event.signature_delta)
            elif isinstance(event, TextDeltaEvent):
                flush_reasoning()
                if event.delta:
                    text_parts.append(event.delta)
                    await emit_event(sink, AgentTextDeltaEvent(run_id, event.delta))
            elif isinstance(event, ToolCallDeltaEvent):
                flush_reasoning()
                flush_text()
                acc = tool_calls.setdefault(event.index, _ToolAcc())
                if event.call_id and not acc.call_id:
                    acc.call_id = event.call_id
                if event.name and not acc.name:
                    acc.name = event.name
                if event.arguments_delta:
                    acc.arguments_parts.append(event.arguments_delta)
            elif isinstance(event, UsageEvent):
                usage = event.usage
            elif isinstance(event, DoneEvent):
                finish_reason = event.finish_reason

        flush_reasoning()
        flush_text()
        for idx in sorted(tool_calls):
            acc = tool_calls[idx]
            blocks.append(
                ToolCallBlock(
                    call_id=acc.call_id,
                    name=acc.name,
                    arguments_json="".join(acc.arguments_parts),
                )
            )

        return GenerateResponse(
            blocks=tuple(blocks),
            usage=usage,
            finish_reason=finish_reason,
        )

    async def _sleep_cancellable(
        self,
        seconds: float,
        cancel_event: asyncio.Event | None,
    ) -> None:
        if cancel_event is None:
            await asyncio.sleep(seconds)
            return
        if cancel_event.is_set():
            raise _TurnCancelledError
        try:
            await asyncio.wait_for(cancel_event.wait(), timeout=seconds)
        except TimeoutError:
            return
        raise _TurnCancelledError

    async def _stream_with_retry(
        self,
        endpoint: EndpointConfig,
        request: GenerateRequest,
        *,
        run_id: str,
        sink: EventSink | None,
        cancel_event: asyncio.Event | None,
    ) -> GenerateResponse:
        for attempt in range(self.max_llm_retries + 1):
            try:
                return await self._stream_once(
                    endpoint,
                    request,
                    run_id=run_id,
                    sink=sink,
                    cancel_event=cancel_event,
                )
            except LLMError as exc:
                if not exc.retryable or attempt >= self.max_llm_retries:
                    raise
                delay = (
                    exc.retry_after_seconds
                    if exc.retry_after_seconds is not None
                    else float(2**attempt)
                )
                delay = min(max(delay, 0.1), 30.0)
                error = exception_error(exc)
                await emit_event(sink, AttemptResetEvent(run_id, attempt + 1, error.message, error))
                await self._sleep_cancellable(delay, cancel_event)
        raise RuntimeError("Unreachable retry exhaustion")

    def _drain_steer_queue(
        self,
        steer_queue: asyncio.Queue[str] | None,
        *,
        session_id: str,
        book_id: str,
        run_id: str,
    ) -> bool:
        if steer_queue is None or steer_queue.empty():
            return False
        items: list[str] = []
        while not steer_queue.empty():
            try:
                text = steer_queue.get_nowait().strip()
            except asyncio.QueueEmpty:
                break
            if text:
                items.append(text)
        if not items:
            return False
        with self.db.transaction() as conn:
            for text in items:
                append_message(
                    conn,
                    session_id=session_id,
                    book_id=book_id,
                    run_id=run_id,
                    role=MessageRole.USER,
                    blocks_json=serialize_blocks((TextBlock(text),)),
                )
        return True

    async def _execute_tools(
        self,
        tool_calls: list[ToolCallBlock],
        *,
        tool_map: dict[str, BoundTool],
        tool_ctx: ToolContext,
        sink: EventSink | None,
    ) -> list[ToolExecutionResult]:
        for tc in tool_calls:
            await emit_event(
                sink,
                ToolStartedEvent(
                    run_id=tool_ctx.run_id,
                    call_id=tc.call_id,
                    name=tc.name,
                    arguments_json=tc.arguments_json,
                ),
            )

        results: list[ToolExecutionResult | None] = [None] * len(tool_calls)
        semaphore = asyncio.Semaphore(self.max_tool_concurrency)
        read_indices: list[int] = []
        seq_indices: list[int] = []

        for idx, tc in enumerate(tool_calls):
            tool = tool_map.get(tc.name)
            if tool is None:
                results[idx] = ToolExecutionResult(
                    content=ToolFailure("unknown_tool", f"Unknown tool: {tc.name}").to_json(),
                    is_error=True,
                )
            elif tool.read_only:
                read_indices.append(idx)
            else:
                seq_indices.append(idx)

        async def run_read(idx: int) -> None:
            tc = tool_calls[idx]
            tool = tool_map[tc.name]
            async with semaphore:
                results[idx] = await asyncio.to_thread(tool.execute, tool_ctx, tc.arguments_json)

        if read_indices:
            await asyncio.gather(*(run_read(idx) for idx in read_indices))

        for idx in seq_indices:
            tc = tool_calls[idx]
            tool = tool_map[tc.name]
            results[idx] = await asyncio.to_thread(tool.execute, tool_ctx, tc.arguments_json)

        final_results: list[ToolExecutionResult] = []
        for idx, tc in enumerate(tool_calls):
            res = results[idx]
            if res is None:
                res = ToolExecutionResult(
                    content=ToolFailure("execution_error", "Missing tool result").to_json(),
                    is_error=True,
                )
            if res.created_change is not None:
                await emit_event(
                    sink,
                    PendingChangeCreatedEvent(tool_ctx.run_id, res.created_change),
                )
            await emit_event(
                sink,
                ToolFinishedEvent(
                    run_id=tool_ctx.run_id,
                    call_id=tc.call_id,
                    name=tc.name,
                    content=res.content,
                    is_error=res.is_error,
                ),
            )
            final_results.append(res)
        return final_results

    async def _execute_and_record_tools(
        self,
        tool_calls: list[ToolCallBlock],
        *,
        tool_map: dict[str, BoundTool],
        tool_ctx: ToolContext,
        sink: EventSink | None,
    ) -> list[ToolExecutionResult]:
        results = await self._execute_tools(
            tool_calls,
            tool_map=tool_map,
            tool_ctx=tool_ctx,
            sink=sink,
        )
        blocks = tuple(
            ToolResultBlock(
                call_id=call.call_id,
                name=call.name,
                content=result.content,
                is_error=result.is_error,
            )
            for call, result in zip(tool_calls, results, strict=True)
        )
        with self.db.transaction() as conn:
            append_message(
                conn,
                session_id=tool_ctx.session_id,
                book_id=tool_ctx.book_id,
                run_id=tool_ctx.run_id,
                role=MessageRole.TOOL,
                blocks_json=serialize_blocks(blocks),
            )
        return results

    def _finish_db_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        usage: TokenUsage,
        error_message: str | None = None,
        error: ErrorInfo | None = None,
    ) -> Run:
        with self.db.transaction() as conn:
            return finish_run(
                conn,
                run_id,
                status=status,
                error_message=error_message,
                error=error,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
            )

    def _load_session_and_book(self, session_id: str) -> tuple[Session, Book]:
        with closing(self.db.connect()) as conn:
            session = get_session(conn, session_id)
            if session is None:
                raise LookupError(f"Session not found: {session_id}")
            book = get_book(conn, session.book_id)
            if book is None:
                raise LookupError(f"Book not found: {session.book_id}")
            return session, book

    async def run_turn(
        self,
        *,
        session_id: str,
        endpoint: EndpointConfig,
        model_id: str,
        user_text: str | None = None,
        extra_snippets: tuple[str, ...] = (),
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        reasoning_effort: str | None = None,
        protocol_override: WireProtocol | None = None,
        sink: EventSink | None = None,
        cancel_event: asyncio.Event | None = None,
        steer_queue: asyncio.Queue[str] | None = None,
        run_id: str | None = None,
    ) -> Run:
        """Run one complete user-initiated agent loop until stop, suspend, cancel, or error."""
        session, book = self._load_session_and_book(session_id)

        with self.db.transaction() as conn:
            if user_text is not None:
                append_message(
                    conn,
                    session_id=session.id,
                    book_id=book.id,
                    role=MessageRole.USER,
                    blocks_json=serialize_blocks((TextBlock(user_text),)),
                )
            prepared = get_run(conn, run_id) if run_id is not None else None
            if prepared is not None:
                if prepared.session_id != session.id or prepared.status != RunStatus.RUNNING:
                    raise ValueError("Prepared run does not belong to this active session")
                run = prepared
            else:
                run = create_run(
                    conn,
                    session_id=session.id,
                    book_id=book.id,
                    model_id=model_id,
                    run_id=run_id,
                )

        await emit_event(sink, RunStartedEvent(run.id, session.id, book.id))

        total_in = 0
        total_out = 0
        total_cr = 0
        total_cw = 0

        def total_usage() -> TokenUsage:
            return TokenUsage(
                input_tokens=total_in,
                output_tokens=total_out,
                cache_read_tokens=total_cr,
                cache_write_tokens=total_cw,
            )

        tools = tools_for_mode(session.mode)
        tool_specs = tuple(tool.to_spec() for tool in tools)
        tool_map = {tool.name: tool for tool in tools}
        tool_ctx = ToolContext(
            db=self.db,
            book_id=book.id,
            session_id=session.id,
            run_id=run.id,
            visibility_scope=session.visibility_scope,
        )

        try:
            for _iteration in range(self.max_iterations):
                if cancel_event is not None and cancel_event.is_set():
                    raise _TurnCancelledError

                self._drain_steer_queue(
                    steer_queue,
                    session_id=session.id,
                    book_id=book.id,
                    run_id=run.id,
                )

                with closing(self.db.connect()) as conn:
                    messages = build_context(
                        conn,
                        session=session,
                        book=book,
                        extra_snippets=extra_snippets,
                    )

                request = GenerateRequest(
                    model=model_id,
                    messages=messages,
                    tools=tool_specs,
                    protocol_override=protocol_override,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    reasoning_effort=reasoning_effort,
                )
                response = await self._stream_with_retry(
                    endpoint,
                    request,
                    run_id=run.id,
                    sink=sink,
                    cancel_event=cancel_event,
                )

                total_in += response.usage.input_tokens
                total_out += response.usage.output_tokens
                total_cr += response.usage.cache_read_tokens
                total_cw += response.usage.cache_write_tokens

                with self.db.transaction() as conn:
                    append_message(
                        conn,
                        session_id=session.id,
                        book_id=book.id,
                        run_id=run.id,
                        role=MessageRole.ASSISTANT,
                        blocks_json=serialize_blocks(response.blocks),
                    )

                tool_calls = [b for b in response.blocks if isinstance(b, ToolCallBlock)]
                if not tool_calls:
                    if self._drain_steer_queue(
                        steer_queue,
                        session_id=session.id,
                        book_id=book.id,
                        run_id=run.id,
                    ):
                        continue
                    usage_now = total_usage()
                    done_run = self._finish_db_run(
                        run.id,
                        status=RunStatus.COMPLETED,
                        usage=usage_now,
                    )
                    await emit_event(
                        sink,
                        RunFinishedEvent(run.id, RunStatus.COMPLETED, usage=usage_now),
                    )
                    return done_run

                tool_task = asyncio.create_task(
                    self._execute_and_record_tools(
                        tool_calls,
                        tool_map=tool_map,
                        tool_ctx=tool_ctx,
                        sink=sink,
                    )
                )
                try:
                    tool_results = await asyncio.shield(tool_task)
                except asyncio.CancelledError:
                    # SQLite worker threads cannot be interrupted. Finish their bounded
                    # transactions and paired tool results before unlocking the session.
                    await tool_task
                    raise

                suspended = next(
                    (
                        res.suspend_question
                        for res in tool_results
                        if res.suspend_question is not None
                    ),
                    None,
                )
                if suspended is not None:
                    question, options = suspended
                    question_json = json.dumps(
                        {"question": question, "options": list(options)},
                        ensure_ascii=False,
                    )
                    with self.db.transaction() as conn:
                        waiting_run = set_run_waiting_user(
                            conn,
                            run.id,
                            pending_question_json=question_json,
                        )
                    await emit_event(sink, WaitingUserEvent(run.id, question, options))
                    return waiting_run

            error = safe_error("iteration_limit")
            err_msg = error.message
            usage_now = total_usage()
            failed_run = self._finish_db_run(
                run.id,
                status=RunStatus.FAILED,
                usage=usage_now,
                error_message=err_msg,
                error=error,
            )
            await emit_event(
                sink,
                RunFinishedEvent(
                    run.id,
                    RunStatus.FAILED,
                    error_message=err_msg,
                    error=error,
                    usage=usage_now,
                ),
            )
            return failed_run
        except _TurnCancelledError:
            usage_now = total_usage()
            cancelled_run = self._finish_db_run(
                run.id,
                status=RunStatus.CANCELLED,
                usage=usage_now,
            )
            await emit_event(
                sink,
                RunFinishedEvent(run.id, RunStatus.CANCELLED, usage=usage_now),
            )
            return cancelled_run
        except asyncio.CancelledError:
            self._finish_db_run(
                run.id,
                status=RunStatus.CANCELLED,
                usage=total_usage(),
            )
            raise
        except Exception as exc:
            error = exception_error(exc)
            err_msg = error.message
            usage_now = total_usage()
            failed_run = self._finish_db_run(
                run.id,
                status=RunStatus.FAILED,
                usage=usage_now,
                error_message=err_msg,
                error=error,
            )
            await emit_event(
                sink,
                RunFinishedEvent(
                    run.id,
                    RunStatus.FAILED,
                    error_message=err_msg,
                    error=error,
                    usage=usage_now,
                ),
            )
            return failed_run

    async def resume_after_user_answer(
        self,
        *,
        run_id: str,
        answer_text: str,
        endpoint: EndpointConfig,
        model_id: str,
        extra_snippets: tuple[str, ...] = (),
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        reasoning_effort: str | None = None,
        protocol_override: WireProtocol | None = None,
        sink: EventSink | None = None,
        cancel_event: asyncio.Event | None = None,
        steer_queue: asyncio.Queue[str] | None = None,
    ) -> Run:
        """Close a waiting_user run and continue the session with the author's answer."""
        with self.db.transaction() as conn:
            waiting = get_run(conn, run_id)
            if waiting is None:
                raise LookupError(f"Run not found: {run_id}")
            if waiting.status != RunStatus.WAITING_USER:
                raise ValueError(
                    f"Run {run_id} is not waiting for user input ({waiting.status.value})"
                )
            finish_run(
                conn,
                waiting.id,
                status=RunStatus.COMPLETED,
                input_tokens=waiting.input_tokens,
                output_tokens=waiting.output_tokens,
                cache_read_tokens=waiting.cache_read_tokens,
                cache_write_tokens=waiting.cache_write_tokens,
            )

        return await self.run_turn(
            session_id=waiting.session_id,
            endpoint=endpoint,
            model_id=model_id,
            user_text=answer_text,
            extra_snippets=extra_snippets,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            protocol_override=protocol_override,
            sink=sink,
            cancel_event=cancel_event,
            steer_queue=steer_queue,
        )
