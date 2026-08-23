"""FastAPI router for the read-only monitoring plane."""

import json
from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from quantora_trade.dashboard.models import (
    DashboardEventPage,
    DashboardSnapshot,
    PaperReport,
    TradePage,
)
from quantora_trade.dashboard.service import DashboardService

ReadAuthorizer = Callable[[str | None], str]


def _deny_unconfigured(_: str | None) -> str:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="insufficient authorization scope",
    )


def create_dashboard_router(
    service: DashboardService,
    *,
    authorize_read: ReadAuthorizer = _deny_unconfigured,
) -> APIRouter:
    """Create a router containing queries only; no execution controls are exposed."""
    router = APIRouter(tags=["monitoring"])

    def require_system_read(
        authorization: Annotated[str | None, Header()] = None,
    ) -> str:
        try:
            return authorize_read(authorization)
        except PermissionError as error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient authorization scope",
            ) from error

    SystemRead = Annotated[str, Depends(require_system_read)]

    @router.get("/dashboard", response_model=DashboardSnapshot)
    def dashboard(_: SystemRead) -> DashboardSnapshot:
        return service.snapshot()

    @router.get("/events", response_model=DashboardEventPage)
    def events(
        _: SystemRead,
        cursor: Annotated[int | None, Query(ge=0)] = None,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> DashboardEventPage:
        header_cursor: int | None = None
        if last_event_id is not None:
            try:
                header_cursor = int(last_event_id)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Last-Event-ID must be a non-negative integer",
                ) from exc
            if header_cursor < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Last-Event-ID must be a non-negative integer",
                )
        if cursor is not None and header_cursor is not None and cursor != header_cursor:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="cursor and Last-Event-ID disagree",
            )
        return service.events(
            after_event_id=header_cursor if header_cursor is not None else cursor or 0,
            limit=limit,
        )

    @router.get("/events/stream")
    def event_stream(
        _: SystemRead,
        cursor: Annotated[int | None, Query(ge=0)] = None,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> StreamingResponse:
        """Return one bounded SSE batch; clients resume with Last-Event-ID.

        The bounded response avoids tying a request worker to an infinite polling loop.
        Reconnecting clients receive a heartbeat when no new event is available.
        """
        header_cursor = _parse_last_event_id(last_event_id)
        if cursor is not None and header_cursor is not None and cursor != header_cursor:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="cursor and Last-Event-ID disagree",
            )
        page = service.events(
            after_event_id=header_cursor if header_cursor is not None else cursor or 0,
            limit=limit,
        )

        def body() -> Iterator[str]:
            if not page.events:
                yield ": heartbeat\n\n"
                return
            for item in page.events:
                payload = json.dumps(item.model_dump(mode="json"), separators=(",", ":"))
                yield f"id: {item.event_id}\nevent: {item.kind.value}\ndata: {payload}\n\n"

        return StreamingResponse(
            body(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/trades", response_model=TradePage)
    def trades(
        _: SystemRead,
        cursor: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> TradePage:
        return service.trades(after_event_id=cursor, limit=limit)

    @router.get("/report", response_model=PaperReport)
    def report(_: SystemRead) -> PaperReport:
        return service.report()

    return router


def _parse_last_event_id(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        result = int(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Last-Event-ID must be a non-negative integer",
        ) from exc
    if result < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Last-Event-ID must be a non-negative integer",
        )
    return result
