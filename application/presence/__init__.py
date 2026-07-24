"""Hardware presence adapters for the application layer."""

from application.presence.serial_adapter import (
    SerialConnectionStatus,
    SerialPresenceAdapter,
)

__all__ = ["SerialConnectionStatus", "SerialPresenceAdapter"]
