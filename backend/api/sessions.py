"""Conversation history and asynchronous run controls."""

from dataclasses import replace

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.api.runtime import ApiRuntime
from backend.errors import safe_error
from backend.storage.models import Message, Run, Session, SessionMode, Visibility
from backend.storage.repos import content, sessions
from backend.storage.repos.content import UNSET


class SessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = ""
    mode: SessionMode = SessionMode.DISCUSS
    visibility_scope: Visibility = Visibility.LOCAL


class TextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)


class RunInput(TextInput):
    model_id: str | None = None


def public_run(run: Run) -> Run:
    if run.error is not None:
        error = safe_error(
            run.error.category,
            retryable=run.error.retryable,
            status_code=run.error.status_code,
            retry_after_seconds=run.error.retry_after_seconds,
        )
        return replace(run, error=error, error_message=error.message)
    if run.error_message or run.status.value == "failed":
        error = safe_error("unknown")
        return replace(run, error=error, error_message=error.message)
    return run


def create_router(runtime: ApiRuntime) -> APIRouter:
    router = APIRouter()

    def require_session(session_id: str) -> Session:
        session = sessions.get_session(runtime.conn, session_id)
        if session is None:
            raise LookupError("Session not found")
        return session

    def require_run(run_id: str) -> Run:
        run = sessions.get_run(runtime.conn, run_id)
        if run is None:
            raise LookupError("Run not found")
        return run

    @router.get("/books/{book_id}/sessions")
    async def list_sessions(book_id: str) -> list[Session]:
        if content.get_book(runtime.conn, book_id) is None:
            raise LookupError("Book not found")
        return sessions.list_sessions(runtime.conn, book_id)

    @router.post("/books/{book_id}/sessions", status_code=201)
    async def create_session(book_id: str, body: SessionInput) -> Session:
        if content.get_book(runtime.conn, book_id) is None:
            raise LookupError("Book not found")
        session = sessions.create_session(
            runtime.conn,
            book_id=book_id,
            title=body.title,
            mode=body.mode,
            visibility_scope=body.visibility_scope,
        )
        runtime.events.publish("content_changed", {"book_id": book_id, "session_id": session.id})
        return session

    @router.get("/sessions/{session_id}")
    async def get_session(session_id: str) -> Session:
        return require_session(session_id)

    @router.patch("/sessions/{session_id}")
    async def update_session(session_id: str, body: SessionInput) -> Session:
        existing = require_session(session_id)
        if (
            "visibility_scope" in body.model_fields_set
            and existing.visibility_scope == Visibility.GLOBAL
            and body.visibility_scope == Visibility.LOCAL
        ):
            raise HTTPException(
                status_code=409,
                detail="Session visibility scope cannot be downgraded from global to local",
            )
        runtime.ensure_session_idle(session_id)
        session = sessions.update_session(
            runtime.conn,
            session_id,
            title=body.title if "title" in body.model_fields_set else UNSET,
            mode=body.mode if "mode" in body.model_fields_set else UNSET,
            visibility_scope=body.visibility_scope
            if "visibility_scope" in body.model_fields_set
            else UNSET,
        )
        runtime.events.publish(
            "content_changed",
            {
                "book_id": session.book_id,
                "session_id": session.id,
                "resource": "session_deleted",
            },
        )
        return session

    @router.delete("/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str) -> Response:
        session = require_session(session_id)
        runtime.ensure_session_idle(session_id)
        sessions.delete_session(runtime.conn, session_id)
        runtime.events.publish(
            "content_changed",
            {
                "book_id": session.book_id,
                "session_id": session.id,
                "resource": "session_deleted",
            },
        )
        return Response(status_code=204)

    @router.get("/sessions/{session_id}/messages")
    async def messages(session_id: str) -> list[Message]:
        require_session(session_id)
        return sessions.list_messages(runtime.conn, session_id)

    @router.get("/sessions/{session_id}/runs")
    async def runs(session_id: str) -> list[Run]:
        require_session(session_id)
        return [public_run(run) for run in sessions.list_runs(runtime.conn, session_id)]

    @router.post("/sessions/{session_id}/runs", status_code=202)
    async def start(session_id: str, body: RunInput) -> Run:
        return runtime.start(session_id, body.text, body.model_id)

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str) -> Run:
        return public_run(require_run(run_id))

    @router.post("/runs/{run_id}/cancel")
    async def cancel(run_id: str) -> Run:
        return public_run(await runtime.cancel(run_id))

    @router.post("/runs/{run_id}/steer", status_code=202)
    async def steer(run_id: str, body: TextInput) -> dict[str, str]:
        require_run(run_id)
        active = runtime.active.get(run_id)
        if active is None or active.task is None or active.task.done():
            raise HTTPException(409, "Run is not active")
        active.steer.put_nowait(body.text)
        return {"run_id": run_id, "status": "queued"}

    @router.post("/runs/{run_id}/answer", status_code=202)
    async def answer(run_id: str, body: RunInput) -> Run:
        waiting = require_run(run_id)
        return runtime.start(
            waiting.session_id, body.text, body.model_id or waiting.model_id, answer_to=run_id
        )

    return router
