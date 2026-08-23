from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

from quantora_trade.notifications import (
    AlertEvent,
    AlertSeverity,
    DeliveryOutcome,
    NotificationService,
    TelegramConfig,
    TelegramNotificationAdapter,
)

NOW = datetime(2026, 8, 23, 3, tzinfo=UTC)


def alert(**changes: object) -> AlertEvent:
    values: dict[str, object] = {
        "event_code": "PAPER_ORDER_REJECTED",
        "severity": AlertSeverity.WARNING,
        "component": "paper_execution",
        "message": "Paper order rejected",
        "dedup_key": "paper:rejected:XAUUSD",
        "cooldown": timedelta(minutes=5),
        "occurred_at": NOW,
        "payload": {"symbol": "XAUUSD"},
    }
    values.update(changes)
    return AlertEvent(**values)  # type: ignore[arg-type]


class RecordingPort:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[AlertEvent, Mapping[str, object]]] = []

    def deliver(self, event: AlertEvent, payload: Mapping[str, object]) -> None:
        self.calls.append((event, payload))
        if self.fail:
            raise OSError("provider unavailable")


def test_service_sanitizes_sensitive_fields_before_delivery() -> None:
    port = RecordingPort()
    service = NotificationService(port)
    event = alert(
        payload={
            "symbol": "XAUUSD",
            "token": "do-not-leak",
            "telegram_token": "also-do-not-leak",
            "nested": {"authorization": "Bearer secret", "count": 2},
            "items": [{"password": "hidden"}],
        }
    )
    assert service.publish(event, now=NOW) is DeliveryOutcome.DELIVERED
    delivered = port.calls[0][1]
    assert delivered == {
        "symbol": "XAUUSD",
        "token": "[REDACTED]",
        "telegram_token": "[REDACTED]",
        "nested": {"authorization": "[REDACTED]", "count": 2},
        "items": ({"password": "[REDACTED]"},),
    }
    with pytest.raises(TypeError):
        event.payload["symbol"] = "EURUSD"  # type: ignore[index]
    nested = event.payload["nested"]
    assert isinstance(nested, Mapping)
    with pytest.raises(TypeError):
        nested["count"] = 3  # type: ignore[index]


def test_cooldown_suppresses_duplicate_only_after_success() -> None:
    port = RecordingPort()
    service = NotificationService(port)
    event = alert()
    assert service.publish(event, now=NOW) is DeliveryOutcome.DELIVERED
    assert service.publish(event, now=NOW + timedelta(minutes=4)) is DeliveryOutcome.SUPPRESSED
    assert service.publish(event, now=NOW + timedelta(minutes=5)) is DeliveryOutcome.DELIVERED
    assert len(port.calls) == 2


def test_provider_failure_is_contained_and_can_be_retried() -> None:
    port = RecordingPort(fail=True)
    service = NotificationService(port)
    event = alert()
    assert service.publish(event, now=NOW) is DeliveryOutcome.FAILED
    port.fail = False
    assert service.publish(event, now=NOW) is DeliveryOutcome.DELIVERED


def test_publish_requires_explicit_utc_clock_value() -> None:
    service = NotificationService(RecordingPort())
    with pytest.raises(ValueError, match="UTC"):
        service.publish(alert(), now=datetime(2026, 1, 1))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"dedup_key": ""}, "dedup_key"),
        ({"cooldown": timedelta(seconds=-1)}, "non-negative"),
        ({"occurred_at": datetime(2026, 1, 1)}, "UTC"),
    ],
)
def test_alert_validation(changes: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        alert(**changes)


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, object], float]] = []

    def post(self, url: str, *, json: Mapping[str, object], timeout_seconds: float) -> None:
        self.calls.append((url, json, timeout_seconds))


def test_telegram_adapter_uses_injected_transport_and_does_not_retain_token() -> None:
    transport = RecordingTransport()
    adapter = TelegramNotificationAdapter(
        config=TelegramConfig(chat_id="12345"),
        token_provider=lambda: "ephemeral-token",
        transport=transport,
    )
    adapter.deliver(alert(), {"symbol": "XAUUSD"})
    url, body, timeout = transport.calls[0]
    assert url.endswith("/botephemeral-token/sendMessage")
    assert body["chat_id"] == "12345"
    assert "PAPER_ORDER_REJECTED" in str(body["text"])
    assert timeout == 5.0
    assert "ephemeral-token" not in repr(adapter)
    assert not hasattr(adapter, "_token")


def test_telegram_config_and_ephemeral_token_are_validated() -> None:
    with pytest.raises(ValueError, match="pinned Telegram API host"):
        TelegramConfig(chat_id="123", base_url="http://telegram.test")
    with pytest.raises(ValueError, match="pinned Telegram API host"):
        TelegramConfig(chat_id="123", base_url="https://attacker.example")
    adapter = TelegramNotificationAdapter(
        config=TelegramConfig(chat_id="123"),
        token_provider=lambda: "",
        transport=RecordingTransport(),
    )
    with pytest.raises(ValueError, match="invalid token"):
        adapter.deliver(alert(), {})
