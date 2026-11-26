"""FastAPI composition and application lifespan."""

import hmac
import os
import sqlite3
from collections.abc import AsyncIterator, Callable, MutableMapping
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api import content, sessions, settings, transfers
from backend.api.errors import validation_details
from backend.api.events import EventBus
from backend.api.runtime import ApiRuntime
from backend.config import AppConfig, load_config
from backend.errors import exception_error
from backend.llm.client import LLMClient
from backend.llm.errors import LLMError


class FrontendFiles(StaticFiles):
    async def get_response(self, path: str, scope: MutableMapping[str, object]) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            root = path.replace("\\", "/").lstrip("/").split("/", 1)[0]
            if exc.status_code != 404 or root in ("api", "health") or Path(path).suffix:
                raise
            return await super().get_response("index.html", scope)


def create_app(
    config: AppConfig | None = None,
    *,
    llm: LLMClient | None = None,
    events: EventBus | None = None,
    static_dir: Path | None = None,
    shutdown: Callable[[], None] | None = None,
) -> FastAPI:
    runtime = ApiRuntime(config or load_config(), llm or LLMClient(), events or EventBus())

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        runtime.open()
        try:
            yield
        finally:
            await runtime.close()

    app = FastAPI(title="Inkwell", version="0.1.0", lifespan=lifespan)

    async def invalid_request(_request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, (RequestValidationError, ValidationError)):
            return JSONResponse({"detail": validation_details(exc)}, status_code=422)
        return JSONResponse({"detail": "Invalid request"}, status_code=422)

    async def missing(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": "Resource not found"}, status_code=404)

    async def conflict(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": "Operation conflicts with current data"}, status_code=409)

    async def upstream(_request: Request, exc: Exception) -> JSONResponse:
        error = exception_error(exc)
        return JSONResponse({"detail": error.message, "error": asdict(error)}, status_code=502)

    app.add_exception_handler(RequestValidationError, invalid_request)
    app.add_exception_handler(ValidationError, invalid_request)
    app.add_exception_handler(LookupError, missing)
    app.add_exception_handler(ValueError, conflict)
    app.add_exception_handler(sqlite3.IntegrityError, conflict)
    app.add_exception_handler(LLMError, upstream)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy", "service": "inkwell"}

    @app.post("/health/shutdown", include_in_schema=False)
    async def request_shutdown(
        x_inkwell_shutdown_token: str = Header(default=""),
    ) -> dict[str, str]:
        token = os.getenv("INKWELL_SHUTDOWN_TOKEN", "")
        if (
            shutdown is None
            or not token
            or not hmac.compare_digest(token, x_inkwell_shutdown_token)
        ):
            raise HTTPException(403, "Forbidden")
        runtime.events.close()
        shutdown()
        return {"status": "stopping"}

    @app.get("/api/events")
    async def stream(last_event_id: str | None = Header(default=None)) -> StreamingResponse:
        try:
            cursor = int(last_event_id) if last_event_id is not None else None
        except ValueError as exc:
            raise HTTPException(400, "Invalid Last-Event-ID") from exc
        return StreamingResponse(
            runtime.events.stream(cursor),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    for module in (transfers, content, settings, sessions):
        app.include_router(module.create_router(runtime), prefix="/api")
    dist = (
        static_dir
        if static_dir is not None
        else Path(__file__).resolve().parents[2] / "frontend" / "dist"
    )
    if dist.is_dir():
        app.mount("/", FrontendFiles(directory=dist, html=True), name="frontend")
    return app
