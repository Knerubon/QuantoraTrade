"""FastAPI application factory for the non-execution control/read plane."""

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Annotated, Protocol, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, Response, status

from quantora_trade import __version__
from quantora_trade.api.schemas import (
    HealthResponse,
    ResolvedSymbolSpecification,
    RiskPolicyValidationRequest,
    RiskPolicyValidationResponse,
    ServiceStatus,
    SystemCommandRequest,
    SystemCommandResponse,
)
from quantora_trade.dashboard.router import create_dashboard_router
from quantora_trade.dashboard.service import DashboardService
from quantora_trade.domain.enums import TradingMode

StatusProvider = Callable[[], ServiceStatus]


class Authorizer(Protocol):
    """Token verification boundary; production may later implement OIDC offline."""

    def authorize(self, bearer_token: str, required_scope: str) -> str: ...


class CommandRecord(Protocol):
    @property
    def id(self) -> UUID: ...

    @property
    def request_id(self) -> str: ...

    @property
    def action(self) -> str: ...

    @property
    def mode(self) -> str: ...

    @property
    def payload(self) -> Mapping[str, object]: ...

    @property
    def actor(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def created_at(self) -> datetime: ...

    @property
    def updated_at(self) -> datetime: ...


class CommandEnqueueResult(Protocol):
    @property
    def command(self) -> CommandRecord: ...

    @property
    def created(self) -> bool: ...


class CommandRepository(Protocol):
    """Minimum queue interface required by the HTTP control plane."""

    def enqueue(
        self,
        *,
        request_id: str,
        idempotency_key: str,
        request_hash: str,
        action: str,
        mode: str,
        payload: Mapping[str, object],
        actor: str,
    ) -> CommandEnqueueResult: ...

    def get(self, command_id: UUID) -> CommandRecord | None: ...


class SymbolPreflightPort(Protocol):
    """Resolve persisted, broker-authoritative specifications for a PAPER start."""

    def resolve(self, symbols: Sequence[str]) -> Sequence[ResolvedSymbolSpecification]: ...


class DenyAllAuthorizer:
    def authorize(self, bearer_token: str, required_scope: str) -> str:
        raise PermissionError("authorization is not configured")


def default_status_provider() -> ServiceStatus:
    """Return a deterministic fail-closed snapshot until adapters are wired."""

    return ServiceStatus(
        version=__version__,
        environment="development",
        mode=TradingMode.PAPER,
        ready=False,
        database_ready=False,
        broker_connected=False,
        kill_switch_active=True,
    )


def create_app(
    status_provider: StatusProvider = default_status_provider,
    *,
    command_repository: CommandRepository | None = None,
    authorizer: Authorizer | None = None,
    dashboard_service: DashboardService | None = None,
    symbol_preflight: SymbolPreflightPort | None = None,
) -> FastAPI:
    """Build the API with explicit dependencies and no execution services."""

    app = FastAPI(title="QuantoraTrade Control API", version=__version__)

    @app.middleware("http")
    async def contract_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Propagate correlation IDs and advertise the stable major API version."""
        request_id = request.headers.get("X-Request-ID") or f"req_{uuid4().hex}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-API-Version"] = "1"
        return response

    def snapshot() -> ServiceStatus:
        return status_provider()

    StatusSnapshot = Annotated[ServiceStatus, Depends(snapshot)]

    def actor_for(authorization: str | None, scope: str) -> str:
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="bearer authorization is required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = authorization.removeprefix("Bearer ")
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token"
            )
        try:
            return (authorizer or DenyAllAuthorizer()).authorize(token, scope)
        except PermissionError as error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient authorization scope",
            ) from error

    if dashboard_service is not None:
        app.include_router(
            create_dashboard_router(
                dashboard_service,
                authorize_read=lambda authorization: actor_for(authorization, "system:read"),
            )
        )

    def enqueue_command(
        action: str,
        request: SystemCommandRequest,
        request_id: str,
        idempotency_key: str,
        authorization: str | None,
    ) -> SystemCommandResponse:
        if request.mode is not TradingMode.PAPER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="paper mode is required"
            )
        actor = actor_for(authorization, "system:operate")
        if command_repository is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="command queue is not configured",
            )
        payload = request.model_dump(mode="json")
        if action == "start":
            if symbol_preflight is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="authoritative symbol preflight is not configured",
                )
            try:
                resolved = tuple(symbol_preflight.resolve(request.symbols))
            except (LookupError, ValueError) as error:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"symbol preflight failed: {error}",
                ) from error
            _validate_symbol_preflight(request.symbols, resolved)
            payload["symbol_specifications"] = [
                item.model_dump(mode="json", exclude={"active", "stale"}) for item in resolved
            ]
        canonical = json.dumps(
            {"action": action, "payload": payload}, sort_keys=True, separators=(",", ":")
        )
        request_hash = hashlib.sha256(canonical.encode()).hexdigest()
        try:
            result = command_repository.enqueue(
                request_id=request_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                action=action,
                mode=request.mode.value,
                payload=payload,
                actor=actor,
            )
        except ValueError as error:
            if "idempotency" in str(error).lower():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=str(error)
                ) from error
            raise
        command = result.command
        return command_response(command, replayed=not bool(result.created))

    @app.get("/health/live", response_model=HealthResponse)
    def health_live() -> HealthResponse:
        return HealthResponse(status="alive")

    @app.get("/health/ready", response_model=HealthResponse)
    def health_ready(current: StatusSnapshot) -> HealthResponse:
        if not current.ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="service is not ready",
            )
        return HealthResponse(status="ready")

    @app.get("/status", response_model=ServiceStatus)
    def service_status(
        current: StatusSnapshot,
        authorization: Annotated[str | None, Header()] = None,
    ) -> ServiceStatus:
        actor_for(authorization, "system:read")
        return current

    @app.post("/config/risk/validate", response_model=RiskPolicyValidationResponse)
    def validate_risk_policy(
        request: RiskPolicyValidationRequest,
    ) -> RiskPolicyValidationResponse:
        if request.requested_mode is not TradingMode.PAPER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="paper mode is required",
            )
        if request.activate:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="configuration activation is not available on the validation endpoint",
            )
        settings = request.policy.to_settings()
        missing = settings.missing_limits()
        return RiskPolicyValidationResponse(
            activation_ready=not missing,
            missing_limits=missing,
            requested_mode=request.requested_mode,
            policy=request.policy,
        )

    HeaderValue = Annotated[str, Header(min_length=1, max_length=255)]
    AuthorizationValue = Annotated[str | None, Header()]

    @app.post(
        "/system/start", response_model=SystemCommandResponse, status_code=status.HTTP_202_ACCEPTED
    )
    def start_system(
        request: SystemCommandRequest,
        x_request_id: HeaderValue,
        idempotency_key: HeaderValue,
        authorization: AuthorizationValue = None,
    ) -> SystemCommandResponse:
        return enqueue_command("start", request, x_request_id, idempotency_key, authorization)

    @app.post(
        "/system/stop", response_model=SystemCommandResponse, status_code=status.HTTP_202_ACCEPTED
    )
    def stop_system(
        request: SystemCommandRequest,
        x_request_id: HeaderValue,
        idempotency_key: HeaderValue,
        authorization: AuthorizationValue = None,
    ) -> SystemCommandResponse:
        return enqueue_command("stop", request, x_request_id, idempotency_key, authorization)

    @app.get("/system/commands/{command_id}", response_model=SystemCommandResponse)
    def command_status(
        command_id: UUID,
        authorization: AuthorizationValue = None,
    ) -> SystemCommandResponse:
        actor_for(authorization, "system:read")
        if command_repository is None:
            raise HTTPException(status_code=503, detail="command queue is not configured")
        command = command_repository.get(command_id)
        if command is None:
            raise HTTPException(status_code=404, detail="command not found")
        return command_response(command, replayed=False)

    @app.get("/api/v1/status", response_model=ServiceStatus)
    def versioned_service_status(
        current: StatusSnapshot,
        authorization: AuthorizationValue = None,
    ) -> ServiceStatus:
        actor_for(authorization, "system:read")
        return current

    # Keep the original unversioned routes as compatibility aliases while the
    # documented contract lives under /api/v1.  Both point at identical handlers.
    versioned = APIRouter()
    versioned.routes.extend(
        tuple(
            route
            for route in app.router.routes[4:]
            if getattr(route, "path", None) != "/status"
            and not str(getattr(route, "path", "")).startswith("/api/v1/")
        )
    )
    app.include_router(versioned, prefix="/api/v1")

    return app


def command_response(command: CommandRecord, *, replayed: bool) -> SystemCommandResponse:
    """Map repository data without exposing the idempotency key or request hash."""

    payload = command.payload
    return SystemCommandResponse(
        id=command.id,
        request_id=command.request_id,
        action=command.action,
        mode=TradingMode(command.mode),
        symbols=tuple(str(value) for value in cast(Sequence[object], payload["symbols"])),
        strategy_id=str(payload["strategy_id"]),
        reason=str(payload["reason"]),
        symbol_specifications=tuple(
            ResolvedSymbolSpecification.model_validate(value)
            for value in cast(Sequence[object], payload.get("symbol_specifications", ()))
        ),
        actor=command.actor,
        status=command.status,
        created_at=command.created_at,
        updated_at=command.updated_at,
        replayed=replayed,
    )


def _validate_symbol_preflight(
    requested: Sequence[str], resolved: Sequence[ResolvedSymbolSpecification]
) -> None:
    """Reject incomplete, ambiguous, inactive, stale, or cross-currency resolution."""

    by_symbol: dict[str, list[ResolvedSymbolSpecification]] = {}
    for item in resolved:
        by_symbol.setdefault(item.symbol, []).append(item)
    requested_set = set(requested)
    if set(by_symbol) != requested_set or any(len(items) != 1 for items in by_symbol.values()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="symbol preflight failed: every canonical symbol must resolve exactly once",
        )
    selected = tuple(by_symbol[symbol][0] for symbol in requested)
    if any(not item.active for item in selected):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="symbol preflight failed: inactive specification",
        )
    if any(item.stale for item in selected):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="symbol preflight failed: stale specification",
        )
    if len({item.quote_currency for item in selected}) != 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="symbol preflight failed: all quote currencies must match",
        )
