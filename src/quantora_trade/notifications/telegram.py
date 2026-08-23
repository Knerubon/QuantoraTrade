"""Telegram adapter with injected HTTP transport and ephemeral credentials."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from quantora_trade.notifications.models import AlertEvent

TELEGRAM_API_BASE_URL = "https://api.telegram.org"


class HttpTransport(Protocol):
    def post(self, url: str, *, json: Mapping[str, object], timeout_seconds: float) -> None: ...


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    chat_id: str
    base_url: str = TELEGRAM_API_BASE_URL
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not self.chat_id or self.chat_id != self.chat_id.strip():
            raise ValueError("chat_id must be a non-empty trimmed value")
        if self.base_url != TELEGRAM_API_BASE_URL:
            raise ValueError("base_url must be the pinned Telegram API host")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")


class TelegramNotificationAdapter:
    """Deliver text alerts; the token is fetched per call and never retained."""

    def __init__(
        self,
        *,
        config: TelegramConfig,
        token_provider: Callable[[], str],
        transport: HttpTransport,
    ) -> None:
        self._config = config
        self._token_provider = token_provider
        self._transport = transport

    def deliver(self, event: AlertEvent, payload: Mapping[str, object]) -> None:
        token = self._token_provider()
        if not token or token != token.strip():
            raise ValueError("Telegram token provider returned an invalid token")
        text = f"[{event.severity.value.upper()}] {event.event_code}: {event.message}"
        if payload:
            text = f"{text}\n{payload}"
        self._transport.post(
            f"{self._config.base_url}/bot{token}/sendMessage",
            json={"chat_id": self._config.chat_id, "text": text},
            timeout_seconds=self._config.timeout_seconds,
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}(chat_id={self._config.chat_id!r})"
