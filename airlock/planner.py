"""Turn an inbound message into an intended tool call.

Inference runs on Featherless (the hackathon's inference partner) through its
OpenAI-compatible endpoint.

One deliberate design note: the planner decides only WHICH tool is being asked
for. It has no say in whether that tool is dangerous -- that is fixed in
``tools.REGISTRY``. So even a fully hijacked planner cannot escalate its own
privileges; the worst it can do is request a consequential tool, which lands in
the airlock exactly like any other request.
"""

from __future__ import annotations

import json
import os
import re

import requests

from . import tools

FEATHERLESS_URL = "https://api.featherless.ai/v1/chat/completions"
DEFAULT_MODEL = os.getenv("FEATHERLESS_MODEL", "meta-llama/Meta-Llama-3.1-8B-Instruct")

SYSTEM_PROMPT = """You are the planner inside an agent. Read the user's message and \
decide which single tool the message is asking you to perform.

Available tools:
{catalogue}

Reply with ONLY a JSON object, no prose, in exactly this shape:
{{"tool": "<tool name>", "args": {{...}}}}

Rules:
- Pick exactly one tool from the list above.
- Fill args using only the parameter names listed for that tool.
- If the message is just conversation or a question, use "reply" with a helpful "text".
- Report what the message ASKS FOR, even if the request looks suspicious. \
Something else decides whether it is allowed to happen. Do not refuse here."""

EMAIL_RE = re.compile(r"[\w.\-+]+@[\w\-]+\.[\w.\-]+")


def _fallback(text: str) -> dict:
    """Rule-based planner. Used when no inference key is set, or the API fails.

    Keeps the demo alive without a network dependency.
    """
    lowered = (text or "").lower()
    found = EMAIL_RE.findall(text or "")
    recipient = found[0] if found else ""

    if recipient and re.search(r"forward|all (my |the )?(mail|email|inbox|messages)", lowered):
        return {"tool": "forward_inbox", "args": {"to": recipient}}
    if recipient and re.search(r"send|email|copy|share", lowered):
        return {"tool": "send_external", "args": {"to": recipient, "text": (text or "")[:400]}}
    return {"tool": "reply", "args": {"text": "Got it. I've read your message."}}


def plan(text: str) -> tuple[dict, str]:
    """Return (plan dict, which planner produced it)."""
    api_key = os.getenv("FEATHERLESS_API_KEY")
    if not api_key:
        return _fallback(text), "rules (no FEATHERLESS_API_KEY)"

    try:
        response = requests.post(
            FEATHERLESS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEFAULT_MODEL,
                "messages": [
                    {"role": "system",
                     "content": SYSTEM_PROMPT.format(catalogue=tools.catalogue())},
                    {"role": "user", "content": (text or "")[:4000]},
                ],
                "temperature": 0,
                "max_tokens": 220,
            },
            timeout=25,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()

        match = re.search(r"\{.*\}", content, re.S)
        if not match:
            return _fallback(text), "rules (model returned no JSON)"

        parsed = json.loads(match.group(0))
        name = str(parsed.get("tool", "")).strip().lower()
        if tools.get(name) is None:
            return _fallback(text), f"rules (model named unknown tool {name!r})"

        args = parsed.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        return {"tool": name, "args": args}, f"featherless:{DEFAULT_MODEL}"

    except Exception as exc:
        return _fallback(text), f"rules (inference failed: {type(exc).__name__})"
