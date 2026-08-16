"""Append-only, hash-chained audit log.

Every decision Airlock makes is written here: actions executed, approvals
requested, approvals granted, and -- most importantly -- attacks refused.

Each record carries the SHA-256 of the record before it. Editing or deleting any
line breaks the chain from that point on, so the log is tamper-evident: you
cannot quietly remove the entry showing that an action was taken.

This is deliberately a plain JSONL file. No database, no service. You can read it
with `cat`, and verify it with `python -m airlock.audit`.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

LOG_PATH = os.environ.get("AIRLOCK_AUDIT_LOG", "audit.jsonl")

GENESIS = "0" * 64


def _digest(record: dict[str, Any]) -> str:
    """Hash of a record, computed over its canonical JSON form."""
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _last_hash(path: str = LOG_PATH) -> str:
    if not os.path.exists(path):
        return GENESIS
    last = GENESIS
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    last = json.loads(line)["hash"]
                except (json.JSONDecodeError, KeyError):
                    continue
    return last


def record(event: str, **fields: Any) -> dict[str, Any]:
    """Append one event to the chain and return the written record."""
    body = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "event": event,
        **fields,
    }
    body["prev"] = _last_hash()
    body["hash"] = _digest(body)

    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(body) + "\n")
    return body


def verify(path: str = LOG_PATH) -> tuple[bool, str]:
    """Walk the chain. Returns (ok, human-readable message)."""
    if not os.path.exists(path):
        return True, "no audit log yet (0 records)"

    prev = GENESIS
    count = 0
    with open(path, "r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            count += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                return False, f"line {lineno}: not valid JSON -- log was edited"

            if rec.get("prev") != prev:
                return False, (
                    f"line {lineno}: broken chain. This record expected prev="
                    f"{prev[:12]}... but carries {str(rec.get('prev'))[:12]}... "
                    "A record before this one was altered or removed."
                )

            stated = rec.pop("hash", None)
            if _digest(rec) != stated:
                return False, f"line {lineno}: contents were modified after writing"
            prev = stated

    return True, f"chain intact across {count} record(s)"


if __name__ == "__main__":  # pragma: no cover
    ok, message = verify()
    print(("OK   " if ok else "BROKEN ") + message)
    raise SystemExit(0 if ok else 1)
