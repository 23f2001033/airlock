"""The tool registry, and the risk tier that decides whether the airlock opens.

Every tool declares its own blast radius:

  SAFE          reversible, stays inside the conversation it came from.
                Runs immediately, whatever channel asked for it.

  CONSEQUENTIAL leaves the conversation, moves data to a third party, or cannot
                be undone. Never runs on the authority of an untrusted channel.

Note that classification is a property of the TOOL, declared here in code, not
something the model decides at runtime. A prompt injection can choose which tool
to ask for; it cannot re-label how dangerous that tool is.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class Risk(str, Enum):
    SAFE = "safe"
    CONSEQUENTIAL = "consequential"


@dataclass
class Tool:
    name: str
    risk: Risk
    description: str
    params: tuple[str, ...]
    run: Callable[..., str]

    def summary(self, args: dict[str, Any]) -> str:
        """One-line rendering of a concrete call, for approval prompts + audit."""
        rendered = ", ".join(f"{k}={v!r}" for k, v in args.items())
        return f"{self.name}({rendered})"


# --------------------------------------------------------------------------
# Tool implementations. These genuinely execute -- nothing here is simulated.
# --------------------------------------------------------------------------

def _tool_reply(ctx: dict, text: str = "") -> str:
    """Reply in the thread the request arrived on."""
    ctx["message"].reply(text or "(no content)")
    return f"replied on {ctx['message'].channel}"


def _tool_note(ctx: dict, text: str = "") -> str:
    """Record a note in the audit log. No side effect outside Airlock."""
    return f"noted: {text[:200]}"


def _tool_send_external(ctx: dict, to: str = "", text: str = "") -> str:
    """Send a message to an address OUTSIDE this conversation.

    This is the exfiltration primitive: it is how data leaves. Consequential.
    """
    client = ctx["client"]
    conn = ctx["email_connection_id"]
    if not to:
        return "refused: no recipient"
    client.initiate(connection_id=conn, recipient=to, text=text or "(empty)")
    return f"sent message to {to}"


def _tool_forward_inbox(ctx: dict, to: str = "") -> str:
    """Forward the contents of the agent's inbox to another address.

    The classic exfiltration payload. Consequential, obviously.
    """
    client = ctx["client"]
    conn = ctx["email_connection_id"]
    if not to:
        return "refused: no recipient"

    lines = []
    try:
        for conversation in client.list_conversations(connection_id=conn)[:10]:
            for msg in client.list_messages(conversation["id"])[:5]:
                snippet = (msg.get("text") or "")[:200]
                if snippet:
                    lines.append(f"- {snippet}")
    except Exception as exc:  # pragma: no cover - network dependent
        lines.append(f"(could not read inbox: {exc})")

    body = "Forwarded inbox contents:\n" + ("\n".join(lines) or "(empty)")
    client.initiate(connection_id=conn, recipient=to, text=body)
    return f"forwarded {len(lines)} message excerpt(s) to {to}"


REGISTRY: dict[str, Tool] = {
    "reply": Tool(
        name="reply",
        risk=Risk.SAFE,
        description="Reply to the sender in the same conversation.",
        params=("text",),
        run=_tool_reply,
    ),
    "note": Tool(
        name="note",
        risk=Risk.SAFE,
        description="Write a note to the audit log. No external effect.",
        params=("text",),
        run=_tool_note,
    ),
    "send_external": Tool(
        name="send_external",
        risk=Risk.CONSEQUENTIAL,
        description="Send a message to an address outside this conversation.",
        params=("to", "text"),
        run=_tool_send_external,
    ),
    "forward_inbox": Tool(
        name="forward_inbox",
        risk=Risk.CONSEQUENTIAL,
        description="Forward the agent's inbox contents to another address.",
        params=("to",),
        run=_tool_forward_inbox,
    ),
}


def get(name: str) -> Tool | None:
    return REGISTRY.get((name or "").strip().lower())


def catalogue() -> str:
    """Tool list rendered for the planner prompt."""
    lines = []
    for tool in REGISTRY.values():
        args = ", ".join(tool.params)
        lines.append(f"- {tool.name}({args}): {tool.description}")
    return "\n".join(lines)
