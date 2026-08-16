"""Prompt-injection heuristics.

An important disclaimer, stated plainly because it is the point of the project:
these heuristics are NOT the security control. They are decoration.

Pattern matching against injection text is a losing game -- an attacker rephrases
and walks straight through it. Airlock does not rely on catching the attack. The
control is the trust boundary in ``trust.py``: a consequential action asked for
by an untrusted channel needs approval from a trusted one, whether or not
anything here fires.

What these signals are actually for: telling the human WHY they are being asked.
"Approve forward_inbox?" is a question nobody can answer well. "Approve
forward_inbox to an outside address, requested by an email that also told me to
ignore my instructions and keep it secret?" answers itself.
"""

from __future__ import annotations

import re

# (regex, what it means in plain language)
SIGNALS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"ignore\s+(all\s+)?(previous|prior|above|earlier)", re.I),
     "tries to cancel the agent's existing instructions"),
    (re.compile(r"disregard\s+(all\s+)?(previous|prior|your)", re.I),
     "tries to cancel the agent's existing instructions"),
    (re.compile(r"you\s+are\s+now\s+", re.I),
     "tries to redefine who the agent is"),
    (re.compile(r"new\s+(system\s+)?(instructions?|prompt|rules?)", re.I),
     "claims to supply new system instructions"),
    (re.compile(r"\b(i\s+am|this\s+is)\s+(the\s+)?(admin|administrator|owner|developer)", re.I),
     "claims administrative authority in message text"),
    (re.compile(r"(pre[-\s]?approved|already\s+approved|no\s+approval\s+needed)", re.I),
     "claims the action is already approved"),
    (re.compile(r"(do\s*n[o']?t|never)\s+(tell|inform|notify|ask|confirm)", re.I),
     "asks the agent to act without telling anyone"),
    (re.compile(r"without\s+(asking|confirming|approval|permission)", re.I),
     "explicitly asks to bypass approval"),
    (re.compile(r"forward\s+(all|every|the\s+entire)", re.I),
     "asks for bulk forwarding of data"),
    (re.compile(r"(send|email|copy)\s+(all|every|everything)", re.I),
     "asks to send everything somewhere"),
    (re.compile(r"urgent|immediately|right\s+now|asap", re.I),
     "applies time pressure, a common social-engineering lever"),
]


def analyse(text: str | None) -> list[str]:
    """Plain-language reasons this text looks like an injection attempt."""
    if not text:
        return []
    seen: list[str] = []
    for pattern, reason in SIGNALS:
        if pattern.search(text) and reason not in seen:
            seen.append(reason)
    return seen


def summarise(text: str | None) -> str:
    """One line for the approval prompt, or empty if nothing fired."""
    reasons = analyse(text)
    if not reasons:
        return ""
    if len(reasons) == 1:
        return f"This message {reasons[0]}."
    joined = "; ".join(reasons[:3])
    return f"This message {joined}."
