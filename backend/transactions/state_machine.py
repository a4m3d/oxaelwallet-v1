"""Transaction lifecycle state machine."""
from __future__ import annotations

CREATED = "created"
AWAITING_CONFIRMATION = "awaiting_confirmation"
SIGNING = "signing"
BROADCASTING = "broadcasting"
BROADCASTED = "broadcasted"
PENDING = "pending"
CONFIRMED = "confirmed"
FAILED = "failed"
CANCELLED = "cancelled"
EXPIRED = "expired"

ALL_STATES = {
    CREATED, AWAITING_CONFIRMATION, SIGNING, BROADCASTING, BROADCASTED,
    PENDING, CONFIRMED, FAILED, CANCELLED, EXPIRED,
}

TERMINAL = {CONFIRMED, FAILED, CANCELLED, EXPIRED}

_TRANSITIONS: dict[str, set[str]] = {
    CREATED: {AWAITING_CONFIRMATION, SIGNING, CANCELLED, EXPIRED},
    AWAITING_CONFIRMATION: {SIGNING, CANCELLED, EXPIRED},
    SIGNING: {BROADCASTING, FAILED},
    BROADCASTING: {BROADCASTED, FAILED},
    BROADCASTED: {PENDING, CONFIRMED, FAILED},
    PENDING: {CONFIRMED, FAILED},
    CONFIRMED: set(),
    FAILED: set(),
    CANCELLED: set(),
    EXPIRED: set(),
}


def can_transition(src: str, dst: str) -> bool:
    return dst in _TRANSITIONS.get(src, set())


def is_terminal(state: str) -> bool:
    return state in TERMINAL
