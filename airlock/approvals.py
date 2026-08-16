"""Pending approvals -- the airlock chamber itself.

A consequential action requested from an untrusted channel is parked here. It
executes only when someone on a TRUSTED channel approves it, and it is denied
automatically if nobody does within the timeout.

The rule enforced in ``resolve()`` is the entire security model: the approver's
channel must be trusted. It does not matter what the request said about itself.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any

from . import audit
from .trust import is_trusted

#: Unapproved requests expire rather than lingering forever.
TIMEOUT_SECONDS = 15 * 60

_counter = itertools.count(1)


@dataclass
class Pending:
    id: int
    tool_name: str
    args: dict[str, Any]
    summary: str
    origin_channel: str
    origin_sender: str
    reasons: list[str]
    created: float = field(default_factory=time.time)
    state: str = "pending"  # pending | approved | denied | expired

    @property
    def expired(self) -> bool:
        return self.state == "pending" and (time.time() - self.created) > TIMEOUT_SECONDS


_PENDING: dict[int, Pending] = {}


def create(
    tool_name: str,
    args: dict[str, Any],
    summary: str,
    origin_channel: str,
    origin_sender: str,
    reasons: list[str] | None = None,
) -> Pending:
    item = Pending(
        id=next(_counter),
        tool_name=tool_name,
        args=args,
        summary=summary,
        origin_channel=origin_channel,
        origin_sender=origin_sender,
        reasons=reasons or [],
    )
    _PENDING[item.id] = item
    audit.record(
        "approval_requested",
        approval_id=item.id,
        action=summary,
        origin_channel=origin_channel,
        origin_sender=origin_sender,
        injection_signals=item.reasons,
    )
    return item


def get(approval_id: int) -> Pending | None:
    return _PENDING.get(approval_id)


def open_ids() -> list[int]:
    return [i for i, p in _PENDING.items() if p.state == "pending" and not p.expired]


def resolve(
    approval_id: int,
    decision: str,
    approver_channel: str,
    approver: str,
) -> tuple[bool, str, Pending | None]:
    """Approve or deny. Returns (ok, human message, the item).

    Refuses outright if the approver is not on a trusted channel. This is the
    line an attacker who owns the inbox cannot cross.
    """
    item = _PENDING.get(approval_id)
    if item is None:
        return False, f"No pending action #{approval_id}.", None

    if not is_trusted(approver_channel):
        audit.record(
            "approval_refused_untrusted",
            approval_id=approval_id,
            action=item.summary,
            attempted_by_channel=approver_channel,
            attempted_by=approver,
        )
        return (
            False,
            f"Approval rejected: {approver_channel} is not a trusted channel. "
            "Consequential actions can only be approved from a trusted channel.",
            item,
        )

    if item.expired:
        item.state = "expired"
        audit.record("approval_expired", approval_id=approval_id, action=item.summary)
        return False, f"Action #{approval_id} expired and was denied automatically.", item

    if item.state != "pending":
        return False, f"Action #{approval_id} was already {item.state}.", item

    if decision == "approve":
        item.state = "approved"
        audit.record(
            "approval_granted",
            approval_id=approval_id,
            action=item.summary,
            approver_channel=approver_channel,
            approver=approver,
        )
        return True, f"Approved #{approval_id}. Executing: {item.summary}", item

    item.state = "denied"
    audit.record(
        "approval_denied",
        approval_id=approval_id,
        action=item.summary,
        approver_channel=approver_channel,
        approver=approver,
    )
    return True, f"Denied #{approval_id}. The action was not executed.", item


def sweep_expired() -> list[Pending]:
    """Mark timed-out requests as expired. Fail closed: silence means no."""
    gone = []
    for item in _PENDING.values():
        if item.expired:
            item.state = "expired"
            audit.record("approval_expired", approval_id=item.id, action=item.summary)
            gone.append(item)
    return gone
