"""Channel trust tiers.

The whole idea of Airlock lives in this file.

Trust is a property of the CHANNEL a message arrived on, never a property of the
message's content. An attacker can write any words they like inside an email --
"I am the admin", "ignore previous instructions", "this is pre-approved" -- and
none of it changes the fact that the bytes arrived over the email connection.

They cannot make an email arrive over Telegram. That is the security boundary,
and it is the one thing prompt injection cannot talk its way past.
"""

from enum import Enum


class Trust(str, Enum):
    #: Anyone on the internet can send to this channel. Content here is hostile
    #: input: it may request actions, but it can never authorise them.
    UNTRUSTED = "untrusted"
    #: Reachable only by the operator. Approvals are only valid from here.
    TRUSTED = "trusted"


#: Channel name (as reported by Caspian's ``Message.channel``) -> trust tier.
#: Anything not listed is treated as UNTRUSTED, which is the safe default: a new
#: channel someone connects later cannot silently gain approval rights.
CHANNEL_TRUST: dict[str, Trust] = {
    "email": Trust.UNTRUSTED,
    "telegram": Trust.TRUSTED,
}


def trust_of(channel: str | None) -> Trust:
    """Trust tier for a channel. Unknown channels fail closed to UNTRUSTED."""
    if not channel:
        return Trust.UNTRUSTED
    return CHANNEL_TRUST.get(channel.strip().lower(), Trust.UNTRUSTED)


def is_trusted(channel: str | None) -> bool:
    return trust_of(channel) is Trust.TRUSTED


def describe(channel: str | None) -> str:
    """Human-readable label used in approval prompts and the audit log."""
    return f"{channel or 'unknown'} ({trust_of(channel).value})"
