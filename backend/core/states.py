"""Durable job state machine for ForgeBot Phase 1.

A failed gate stops downstream steps that depend on it while earlier
artifacts stay available for review; cancellation keeps partial results.
"""

from __future__ import annotations

QUEUED = "queued"
SPECIFYING = "specifying"
AWAITING_REVIEW = "awaiting_review"
GENERATING = "generating"
VALIDATING = "validating"
SIMULATING = "simulating"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL = frozenset({SUCCEEDED, FAILED, CANCELLED})

_TRANSITIONS: dict[str, frozenset[str]] = {
    QUEUED: frozenset({SPECIFYING, CANCELLED}),
    SPECIFYING: frozenset({AWAITING_REVIEW, FAILED, CANCELLED}),
    AWAITING_REVIEW: frozenset({GENERATING, CANCELLED}),
    GENERATING: frozenset({VALIDATING, FAILED, CANCELLED}),
    VALIDATING: frozenset({SIMULATING, SUCCEEDED, FAILED, CANCELLED}),
    SIMULATING: frozenset({SUCCEEDED, FAILED, CANCELLED}),
    SUCCEEDED: frozenset(),
    FAILED: frozenset(),
    CANCELLED: frozenset(),
}


def can_transition(current: str, target: str) -> bool:
    """Return True when moving from current to target is a legal transition."""
    return target in _TRANSITIONS.get(current, frozenset())


def check_transition(current: str, target: str) -> None:
    """Raise ValueError on an illegal transition (keeps bad states loud)."""
    if not can_transition(current, target):
        raise ValueError(f"illegal job transition: {current!r} -> {target!r}")
