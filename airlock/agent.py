"""The single handler.

Every channel Airlock is connected to arrives here. There is exactly one
``on_message`` handler in this project -- the routing below is by trust tier, not
by channel, and adding a third channel changes nothing in this file except an
entry in ``trust.CHANNEL_TRUST``.

Decision flow:

    message arrives
        |
        +-- trusted channel + approval command?  -> resolve it, run if approved
        |
        +-- plan the intended tool call (planner)
        |
        +-- tool is SAFE?                        -> run it now, any channel
        |
        +-- tool is CONSEQUENTIAL, trusted?      -> run it now
        |
        +-- tool is CONSEQUENTIAL, untrusted?    -> AIRLOCK: park it, ask the
                                                    operator on a trusted channel
"""

from __future__ import annotations

import json
import os
import re

from caspian_sdk import blocks as b

from . import approvals, audit, injection, planner, tools
from .trust import Trust, describe, is_trusted, trust_of

OPERATOR_FILE = os.environ.get("AIRLOCK_OPERATOR_FILE", "operator.json")


class _OperatorNotifier:
    """Stands in for a Message when Airlock is acting on the operator's own
    authority (a button tap), where there is no inbound message to reply to."""

    def __init__(self, airlock: "Airlock") -> None:
        self._airlock = airlock
        self.channel = "telegram"

    def reply(self, text: str | None = None, **_: object) -> dict:
        self._airlock._notify_operator(text or "")
        return {"ok": True}

APPROVE_RE = re.compile(r"^\s*/?(approve|yes|ok|allow)\s*#?(\d+)\s*$", re.I)
DENY_RE = re.compile(r"^\s*/?(deny|no|reject|block)\s*#?(\d+)\s*$", re.I)
BUTTON_RE = re.compile(r"^(approve|deny):(\d+)$", re.I)


class Airlock:
    def __init__(self, client, email_connection_id: str | None = None):
        self.client = client
        self.email_connection_id = email_connection_id
        self.operator = self._load_operator()

    # ---------------------------------------------------------------- operator

    def _load_operator(self) -> dict | None:
        if os.path.exists(OPERATOR_FILE):
            try:
                with open(OPERATOR_FILE, "r", encoding="utf-8") as handle:
                    return json.load(handle)
            except Exception:
                return None
        return None

    def _remember_operator(self, message) -> None:
        """First person to speak from a trusted channel becomes the operator."""
        if self.operator:
            return
        self.operator = {
            "channel": message.channel,
            "conversation_id": message.conversation_id,
            "sender": (message.sender or {}).get("address")
            or (message.sender or {}).get("id")
            or "operator",
        }
        with open(OPERATOR_FILE, "w", encoding="utf-8") as handle:
            json.dump(self.operator, handle, indent=2)
        audit.record("operator_registered", **self.operator)
        print(f"[airlock] operator registered on {self.operator['channel']}")

    def _notify_operator(self, text: str, blocks: list[dict] | None = None) -> bool:
        if not self.operator:
            print("[airlock] NO OPERATOR REGISTERED -- message the bot from Telegram once")
            return False
        try:
            self.client.send_message(
                conversation_id=self.operator["conversation_id"],
                text=text,
                blocks=blocks,
            )
            return True
        except Exception as exc:  # pragma: no cover - network dependent
            print(f"[airlock] could not reach operator: {exc}")
            return False

    # ------------------------------------------------------------- executing

    def _context(self, message) -> dict:
        return {
            "client": self.client,
            "message": message,
            "email_connection_id": self.email_connection_id,
        }

    def _execute(self, tool, args: dict, ctx: dict, origin: str) -> str:
        safe_args = {k: v for k, v in args.items() if k in tool.params}
        try:
            result = tool.run(ctx, **safe_args)
        except Exception as exc:
            audit.record("action_failed", action=tool.summary(safe_args),
                         origin=origin, error=f"{type(exc).__name__}: {exc}")
            return f"action failed: {exc}"
        audit.record("action_executed", action=tool.summary(safe_args),
                     origin=origin, risk=tool.risk.value, result=result)
        return result

    # -------------------------------------------------------------- handling

    def handle_message(self, message) -> None:
        channel = message.channel
        tier = trust_of(channel)
        sender = (message.sender or {}).get("address") or (message.sender or {}).get("id") or "?"
        text = message.text or ""

        print(f"[airlock] <- {describe(channel)} from {sender}: {text[:90]!r}")

        if tier is Trust.TRUSTED:
            self._remember_operator(message)

        # Approval commands are recognised on EVERY channel, then refused by
        # approvals.resolve() if the channel isn't trusted. Parsing them here
        # rather than only on trusted channels means an attacker trying to
        # approve their own request gets an explicit refusal and an audit
        # record of the attempt, instead of silently falling through to the
        # planner and being answered like ordinary conversation.
        if self._try_approval_command(message, text, channel, sender):
            return

        # Plan what this message is asking for.
        plan, planner_used = planner.plan(text)
        tool = tools.get(plan["tool"])
        if tool is None:
            message.reply("I couldn't map that to anything I know how to do.")
            return

        args = plan.get("args") or {}
        summary = tool.summary({k: v for k, v in args.items() if k in tool.params})
        ctx = self._context(message)

        audit.record(
            "request_received",
            channel=channel,
            trust=tier.value,
            sender=sender,
            planned_action=summary,
            risk=tool.risk.value,
            planner=planner_used,
        )

        # Safe tools run anywhere. Consequential tools run only on trusted authority.
        if tool.risk is tools.Risk.SAFE or tier is Trust.TRUSTED:
            result = self._execute(tool, args, ctx, origin=describe(channel))
            if tool.name != "reply":
                message.reply(f"Done: {result}")
            return

        # Consequential + untrusted -> the airlock closes.
        self._hold_for_approval(message, tool, args, summary, channel, sender, text)

    def _hold_for_approval(self, message, tool, args, summary, channel, sender, text) -> None:
        reasons = injection.analyse(text)
        pending = approvals.create(
            tool_name=tool.name,
            args=args,
            summary=summary,
            origin_channel=channel,
            origin_sender=sender,
            reasons=reasons,
        )

        why = injection.summarise(text)
        body = (
            f"Approval needed  #{pending.id}\n\n"
            f"Action:    {summary}\n"
            f"Risk:      consequential (leaves this conversation / cannot be undone)\n"
            f"Requested: {sender}\n"
            f"Arrived:   {channel} — UNTRUSTED\n"
        )
        if why:
            body += f"\nWhy I'm suspicious: {why}\n"
        body += f"\nReply 'approve {pending.id}' or 'deny {pending.id}'."

        delivered = self._notify_operator(
            body,
            blocks=[
                b.heading(f"Approval needed #{pending.id}"),
                b.fields([
                    {"label": "Action", "value": summary},
                    {"label": "Requested by", "value": f"{sender} via {channel}"},
                    {"label": "Channel trust", "value": "UNTRUSTED"},
                ]),
                *([b.text(f"Why I'm suspicious: {why}")] if why else []),
                b.buttons([
                    {"label": "Approve", "value": f"approve:{pending.id}"},
                    {"label": "Deny", "value": f"deny:{pending.id}"},
                ]),
            ],
        )

        note = (
            f"That needs approval from a trusted channel, so I haven't done it.\n"
            f"Held as #{pending.id}: {summary}"
        )
        if not delivered:
            note += "\n(No operator is registered yet, so it will expire unapproved.)"
        message.reply(note)
        print(f"[airlock] HELD #{pending.id}: {summary}")

    # -------------------------------------------------------------- approvals

    def _try_approval_command(self, message, text, channel, sender) -> bool:
        approve = APPROVE_RE.match(text or "")
        deny = DENY_RE.match(text or "")
        if not approve and not deny:
            return False
        decision = "approve" if approve else "deny"
        approval_id = int((approve or deny).group(2))
        self._resolve(message, decision, approval_id, channel, sender)
        return True

    def handle_interaction(self, interaction) -> None:
        """Button taps from Telegram/Slack/Discord.

        Note we do NOT call ``interaction.reply()`` here. The approval card is an
        outbound message we sent, and Caspian only lets you reply to inbound
        ones, so replying to our own card fails with a 400. We send a fresh
        message into the operator's conversation instead.
        """
        match = BUTTON_RE.match((interaction.value or "").strip())
        if not match:
            return
        decision, approval_id = match.group(1).lower(), int(match.group(2))
        channel = (interaction.source_message or {}).get("channel") or "telegram"
        sender = (interaction.sender or {}).get("address") or (interaction.sender or {}).get("id") or "operator"

        ok, note, item = approvals.resolve(approval_id, decision, channel, sender)
        self._notify_operator(note)
        print(f"[airlock] button {decision} #{approval_id}: {note}")
        if ok and item and item.state == "approved":
            self._run_approved(item, note_to=_OperatorNotifier(self))

    def _resolve(self, message, decision, approval_id, channel, sender) -> None:
        ok, note, item = approvals.resolve(approval_id, decision, channel, sender)
        message.reply(note)
        print(f"[airlock] {decision} #{approval_id}: {note}")
        if ok and item and item.state == "approved":
            self._run_approved(item, note_to=message)

    def _run_approved(self, item, note_to) -> None:
        tool = tools.get(item.tool_name)
        if tool is None:
            return
        ctx = {
            "client": self.client,
            "message": note_to,
            "email_connection_id": self.email_connection_id,
        }
        result = self._execute(tool, item.args, ctx,
                               origin=f"approved by operator (orig: {item.origin_channel})")
        try:
            note_to.reply(f"Executed #{item.id}: {result}")
        except Exception:
            pass
