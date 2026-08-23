"""Notification delivery boundary."""

from collections.abc import Mapping
from typing import Protocol

from quantora_trade.notifications.models import AlertEvent


class NotificationPort(Protocol):
    """Provider adapter receiving an already-sanitized alert."""

    def deliver(self, event: AlertEvent, payload: Mapping[str, object]) -> None: ...
