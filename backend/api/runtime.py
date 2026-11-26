"""Application-owned resources and run coordination."""

import asyncio
import json
import sqlite3
from dataclasses import asdict, dataclass, field

from fastapi import HTTPException
from pydantic import JsonValue, TypeAdapter

from backend.agent import events as agent_events
from backend.agent.loop import AgentRunner
from backend.agent.serialization import serialize_blocks
from backend.api.events import EventBus
from backend.api.references import EntityReference, resolve_references
from backend.config import AppConfig
from backend.crypto import create_cipher
from backend.errors import exception_error
from backend.llm._json import JsonObject
from backend.llm.client import LLMClient
from backend.llm.presets import build_endpoint_from_preset
from backend.llm.types import (
    EndpointConfig,
    GenerateRequest,
    LLMMessage,
    Role,
    TextBlock,
    WireProtocol,
)
from backend.storage.db import Database
from backend.storage.migrations import ensure_transaction
from backend.storage.models import MessageRole, ModelRecord, Provider, Run, RunStatus
from backend.storage.repos import config, sessions


@dataclass
class ActiveRun:
    run: Run
    steer: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    task: asyncio.Task[None] | None = None
    auto_title: tuple[str, str] | None = None
    extra_snippets: tuple[str, ...] = ()


class ApiRuntime:
    def __init__(self, settings: AppConfig, llm: LLMClient, events: EventBus) -> None:
        self.config = settings
        self.db = Database(settings.db_path)
        self.llm = llm
        self.events = events
        self.cipher = create_cipher(settings)
        self.conn: sqlite3.Connection
        self.runner = AgentRunner(self.db, llm)
        self.active: dict[str, ActiveRun] = {}
        self.background_tasks: set[asyncio.Task[None]] = set()
        self.maintenance_sessions: set[str] = set()

    def open(self) -> None:
        self.config.ensure_data_dirs()
        self.db.initialize()
        self.conn = self.db.connect()
        # Waiting questions survive restarts; interrupted upstream streams cannot.
        for row in self.conn.execute("SELECT id FROM runs WHERE status = 'running'").fetchall():
            sessions.finish_run(self.conn, str(row[0]), status=RunStatus.CANCELLED)

    async def close(self) -> None:
        self.events.close()
        tasks = [
            *[entry.task for entry in self.active.values() if entry.task is not None],
            *self.background_tasks,
        ]
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))
        for entry in self.active.values():
            sessions.finish_run(self.conn, entry.run.id, status=RunStatus.CANCELLED)
        self.active.clear()
        self.background_tasks.clear()
        await self.llm.aclose()
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.close()

    def endpoint(self, provider: Provider) -> EndpointConfig:
        headers = TypeAdapter(dict[str, str]).validate_json(
            self.cipher.decrypt(provider.headers_encrypted) or "{}"
        )
        return build_endpoint_from_preset(
            provider.preset or "custom",
            api_key=self.cipher.decrypt(provider.api_key_encrypted),
            base_url=provider.base_url or None,
            default_protocol=WireProtocol(provider.default_protocol.value),
            discovery_url=provider.discovery_url,
            headers=tuple(headers.items()),
        )

    def ensure_book_idle(self, book_id: str) -> None:
        if any(
            session.id in self.maintenance_sessions
            for session in sessions.list_sessions(self.conn, book_id)
        ):
            raise HTTPException(409, "Book has a session maintenance operation")
        if self.conn.execute(
            "SELECT 1 FROM runs WHERE book_id=? AND status IN ('running','waiting_user')",
            (book_id,),
        ).fetchone():
            raise HTTPException(409, "Book has an active or waiting run")

    def ensure_session_idle(self, session_id: str) -> None:
        if session_id in self.maintenance_sessions:
            raise HTTPException(409, "Session has a maintenance operation")
        if any(item.run.session_id == session_id for item in self.active.values()) or any(
            run.status in (RunStatus.RUNNING, RunStatus.WAITING_USER)
            for run in sessions.list_runs(self.conn, session_id)
        ):
            raise HTTPException(409, "Session already has an active or waiting run")

    def resolve_model(self, record_id: str | None) -> tuple[ModelRecord, EndpointConfig]:
        record_id = record_id or config.get_setting(self.conn, "default_model_id")
        model = config.get_model(self.conn, record_id) if record_id else None
        if model is None or not model.enabled:
            raise HTTPException(422, "Select an enabled model")
        provider = config.get_provider(self.conn, model.provider_id)
        if provider is None or not provider.enabled:
            raise HTTPException(422, "Model provider is disabled or missing")
        return model, self.endpoint(provider)

    def _fallback_session_title(self, text: str) -> str:
        for raw_line in text.splitlines():
            collapsed = " ".join(raw_line.split()).strip("\"'“”‘’「」『』 ")
            if collapsed:
                return collapsed[:20]
        return "新对话"

    def _resolve_background_model(self) -> tuple[ModelRecord, EndpointConfig] | None:
        for setting_key in ("background_model_id", "default_model_id"):
            record_id = config.get_setting(self.conn, setting_key)
            if not record_id:
                continue
            model = config.get_model(self.conn, record_id)
            if model is None or not model.enabled:
                continue
            provider = config.get_provider(self.conn, model.provider_id)
            if provider is None or not provider.enabled:
                continue
            return model, self.endpoint(provider)
        return None

    async def _generate_session_title(
        self, session_id: str, book_id: str, expected_title: str, user_text: str
    ) -> None:
        try:
            resolved = self._resolve_background_model()
            if resolved is None:
                return
            model, endpoint = resolved
            extra_body: JsonObject | None = (
                {"thinking": {"type": "disabled"}}
                if "deepseek" in endpoint.base_url.lower()
                else None
            )
            response = await self.llm.complete(
                endpoint,
                GenerateRequest(
                    model=model.model_id,
                    messages=(
                        LLMMessage.text(
                            Role.USER,
                            f"请为下面这段用户提问起一个 6~12 个字的简洁中文对话标题，直接输出标题文本，不要引号、标点或任何解释：\n\n「{user_text[:600]}」",
                        ),
                    ),
                    protocol_override=WireProtocol(model.protocol_override.value)
                    if model.protocol_override
                    else None,
                    max_output_tokens=1024,
                    extra_body=extra_body,
                ),
            )
            raw = "".join(block.text for block in response.blocks if isinstance(block, TextBlock))
            first_line = next(
                (line for line in raw.splitlines() if line.strip()),
                "",
            )
            generated = (
                " ".join(first_line.split())
                .strip("\"'“”‘’「」 ")
                .rstrip("。，、：:；;！!？?.")[:24]
                .strip()
            )
            if not generated:
                return
            current = sessions.get_session(self.conn, session_id)
            if current is None or current.title != expected_title:
                return
            sessions.update_session(self.conn, session_id, title=generated)
            self.events.publish("content_changed", {"book_id": book_id, "session_id": session_id})
        except Exception:
            return

    def start(
        self,
        session_id: str,
        text: str,
        record_id: str | None,
        *,
        answer_to: str | None = None,
        reasoning_effort: str | None = None,
        references: list[EntityReference] | None = None,
    ) -> Run:
        session = sessions.get_session(self.conn, session_id)
        if session is None:
            raise LookupError("Session not found")
        model, endpoint = self.resolve_model(record_id)
        if answer_to is None:
            self.ensure_session_idle(session_id)
        else:
            waiting = sessions.get_run(self.conn, answer_to)
            if waiting is None or waiting.status != RunStatus.WAITING_USER:
                raise HTTPException(409, "Run is not waiting for an answer")
            if answer_to in self.active:
                raise HTTPException(409, "Run is still finishing; retry the answer")
            if waiting.session_id != session_id:
                raise HTTPException(409, "Run belongs to another session")
            if reasoning_effort is None:
                reasoning_effort = waiting.reasoning_effort
            if references is None:
                references = TypeAdapter(list[EntityReference]).validate_json(
                    waiting.references_json
                )
        references = references or []
        snippets = resolve_references(self.conn, session, references)
        fallback_title: str | None = None
        if answer_to is None and not session.title.strip():
            fallback_title = self._fallback_session_title(text)
        with ensure_transaction(self.conn):
            if fallback_title is not None:
                sessions.update_session(self.conn, session_id, title=fallback_title)
            if answer_to is not None:
                sessions.finish_run(self.conn, answer_to, status=RunStatus.COMPLETED)
            run = sessions.create_run(
                self.conn,
                session_id=session_id,
                book_id=session.book_id,
                model_id=model.id,
                reasoning_effort=reasoning_effort,
                references_json=TypeAdapter(list[EntityReference]).dump_json(references).decode(),
            )
            sessions.append_message(
                self.conn,
                session_id=session_id,
                book_id=session.book_id,
                run_id=run.id,
                role=MessageRole.USER,
                blocks_json=serialize_blocks((TextBlock(text),)),
            )
        active = ActiveRun(
            run,
            auto_title=(fallback_title, text) if fallback_title is not None else None,
            extra_snippets=snippets,
        )
        self.active[run.id] = active
        active.task = asyncio.create_task(self._execute(active, model, endpoint))
        return run

    def _emit(self, event: agent_events.AgentEvent, run: Run) -> None:
        names = {
            agent_events.RunStartedEvent: "run_started",
            agent_events.TextDeltaEvent: "text_delta",
            agent_events.ReasoningDeltaEvent: "reasoning_delta",
            agent_events.ReasoningCompletedEvent: "reasoning_completed",
            agent_events.AttemptResetEvent: "attempt_reset",
            agent_events.ToolStartedEvent: "tool_start",
            agent_events.ToolFinishedEvent: "tool_result",
            agent_events.PendingChangeCreatedEvent: "pending_change_created",
            agent_events.WaitingUserEvent: "waiting_user",
            agent_events.RunFinishedEvent: "run_done",
        }
        payload = TypeAdapter(dict[str, JsonValue]).validate_json(json.dumps(asdict(event)))
        payload.update(book_id=run.book_id, session_id=run.session_id, run_id=run.id)
        if isinstance(event, (agent_events.RunFinishedEvent, agent_events.WaitingUserEvent)):
            current = sessions.get_run(self.conn, run.id)
            payload["reasoning_duration_ms"] = current.reasoning_duration_ms if current else 0
        self.events.publish(names[type(event)], payload)
        if isinstance(
            event, (agent_events.PendingChangeCreatedEvent, agent_events.RunFinishedEvent)
        ):
            self.events.publish(
                "content_changed",
                {"book_id": run.book_id, "session_id": run.session_id, "run_id": run.id},
            )

    async def _execute(
        self, active: ActiveRun, model: ModelRecord, endpoint: EndpointConfig
    ) -> None:
        run = active.run
        try:
            await self.runner.run_turn(
                session_id=run.session_id,
                endpoint=endpoint,
                model_id=model.model_id,
                run_id=run.id,
                steer_queue=active.steer,
                protocol_override=WireProtocol(model.protocol_override.value)
                if model.protocol_override
                else None,
                max_output_tokens=model.max_output_tokens,
                reasoning_effort=run.reasoning_effort,
                extra_snippets=active.extra_snippets,
                sink=lambda event: self._emit(event, run),
            )
        except asyncio.CancelledError:
            current = sessions.get_run(self.conn, run.id)
            if current is not None and current.status == RunStatus.RUNNING:
                sessions.finish_run(self.conn, run.id, status=RunStatus.CANCELLED)
            self._emit(agent_events.RunFinishedEvent(run.id, RunStatus.CANCELLED), run)
        except Exception as exc:
            error = exception_error(exc)
            sessions.finish_run(
                self.conn,
                run.id,
                status=RunStatus.FAILED,
                error=error,
            )
            self._emit(
                agent_events.RunFinishedEvent(run.id, RunStatus.FAILED, error.message, error=error),
                run,
            )
        finally:
            self.active.pop(run.id, None)
            if active.auto_title is not None:
                expected_title, user_text = active.auto_title
                title_task = asyncio.create_task(
                    self._generate_session_title(
                        run.session_id, run.book_id, expected_title, user_text
                    )
                )
                self.background_tasks.add(title_task)
                title_task.add_done_callback(self.background_tasks.discard)

    async def cancel(self, run_id: str) -> Run:
        run = sessions.get_run(self.conn, run_id)
        if run is None:
            raise LookupError("Run not found")
        active = self.active.get(run_id)
        if active is not None and active.task is not None:
            if not active.task.cancelling():
                active.task.cancel()
            await asyncio.shield(asyncio.gather(active.task, return_exceptions=True))
            self.active.pop(run_id, None)
        current = sessions.get_run(self.conn, run_id)
        if current is not None and current.status in (RunStatus.RUNNING, RunStatus.WAITING_USER):
            current = sessions.finish_run(self.conn, run_id, status=RunStatus.CANCELLED)
            self._emit(agent_events.RunFinishedEvent(run_id, RunStatus.CANCELLED), run)
        return current or run
