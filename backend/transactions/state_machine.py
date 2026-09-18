"""Transaction lifecycle state machine."""
from __future__ import annotations

CREATED = "created"
ESTIMATING = "estimating"
AWAITING_CONFIRMATION = "awaiting_confirmation"
SIGNING = "signing"
BROADCASTING = "broadcasting"
BROADCASTED = "broadcasted"
PENDING = "pending"
CONFIRMED = "confirmed"
FAILED = "failed"
REPLACED = "replaced"
CANCELLED = "cancelled"
EXPIRED = "expired"

ALL_STATES = {
    CREATED, ESTIMATING, AWAITING_CONFIRMATION, SIGNING, BROADCASTING, BROADCASTED,
    PENDING, CONFIRMED, FAILED, REPLACED, CANCELLED, EXPIRED,
}

TERMINAL = {CONFIRMED, FAILED, REPLACED, CANCELLED, EXPIRED}

_TRANSITIONS: dict[str, set[str]] = {
    CREATED: {ESTIMATING, AWAITING_CONFIRMATION, SIGNING, CANCELLED, EXPIRED},
    ESTIMATING: {AWAITING_CONFIRMATION, CANCELLED, EXPIRED, FAILED},
    AWAITING_CONFIRMATION: {SIGNING, CANCELLED, EXPIRED},
    SIGNING: {BROADCASTING, FAILED},
    BROADCASTING: {BROADCASTED, FAILED},
    BROADCASTED: {PENDING, CONFIRMED, FAILED, REPLACED},
    PENDING: {CONFIRMED, FAILED, REPLACED},
    CONFIRMED: set(),
    FAILED: set(),
    REPLACED: set(),
    CANCELLED: set(),
    EXPIRED: set(),
}


def can_transition(src: str, dst: str) -> bool:
    return dst in _TRANSITIONS.get(src, set())


def is_terminal(state: str) -> bool:
    return state in TERMINAL
